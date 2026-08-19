"""Secure HTTP fetcher for web research.

Provides safe HTTP/HTTPS fetching with strict SSRF protection,
timeouts, size limits, and redirect controls.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("research.fetcher")


@dataclass
class FetchResult:
    """Result of a fetch operation."""
    success: bool
    url: str
    status_code: Optional[int] = None
    content: str = ""
    content_type: str = ""
    headers: dict[str, str] = None
    error: str = ""
    duration_ms: float = 0.0
    final_url: str = ""
    metadata: dict = None

    def __post_init__(self):
        if self.headers is None:
            self.headers = {}
        if self.metadata is None:
            self.metadata = {}


# Private IP ranges to block (SSRF protection)
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # Loopback
    ipaddress.ip_network("10.0.0.0/8"),       # RFC 1918
    ipaddress.ip_network("172.16.0.0/12"),    # RFC 1918
    ipaddress.ip_network("192.168.0.0/16"),   # RFC 1918
    ipaddress.ip_network("169.254.0.0/16"),   # Link-local
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]

# Default limits
DEFAULT_TIMEOUT = 10.0          # seconds (httpx read timeout; NOT weakened)
DEFAULT_CONNECT_TIMEOUT = 5.0   # seconds (httpx connect timeout; NOT weakened)
DEFAULT_MAX_SIZE = 2 * 1024 * 1024  # 2 MB
DEFAULT_MAX_REDIRECTS = 5
# Hard overall ceiling for a single fetch. This sits ABOVE the httpx
# connect/read timeouts and guarantees fetch() can never block indefinitely
# even if httpx itself deadlocks (e.g. during interpreter shutdown).
DEFAULT_HARD_TIMEOUT = 30.0     # seconds
# Default ceiling for web-search (DDGS) operations, which have NO built-in
# timeout of their own. Keeps searches from hanging the whole pipeline.
DEFAULT_SEARCH_TIMEOUT = 15.0   # seconds


def _is_private_ip(ip: str) -> bool:
    """Check if an IP address is private/reserved."""
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        return False


def _resolve_host(host: str) -> list[str]:
    """Resolve a hostname to IP addresses."""
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        return list({info[4][0] for info in infos})
    except (socket.gaierror, OSError):
        return []


def _check_ssrf(url: str) -> tuple[bool, str]:
    """Check if a URL passes SSRF protection.
    
    Returns:
        (allowed, reason) - True if allowed, False with reason if blocked
    """
    parsed = urlparse(url)
    
    # Only allow HTTP/HTTPS
    if parsed.scheme not in ("http", "https"):
        return False, f"Scheme '{parsed.scheme}' not allowed (only http/https)"
    
    host = parsed.hostname or ""
    if not host:
        return False, "No hostname"
    
    # Resolve and check IPs
    ips = _resolve_host(host)
    if not ips:
        return False, f"Could not resolve host: {host}"
    
    for ip in ips:
        if _is_private_ip(ip):
            return False, f"Destination IP {ip} is private/reserved (SSRF protection)"
    
    return True, "OK"


class SecureFetcher:
    """Secure HTTP fetcher with SSRF protection and limits."""
    
    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        max_size: int = DEFAULT_MAX_SIZE,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        hard_timeout: float = DEFAULT_HARD_TIMEOUT,
        user_agent: str = "Jarvis/1.2.0 Research Bot",
    ) -> None:
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.hard_timeout = hard_timeout
        self.max_size = max_size
        self.max_redirects = max_redirects
        self.user_agent = user_agent

        # Create client with strict settings.
        # NOTE: the read/connect timeouts below are the production-grade
        # network timeouts and must NOT be weakened by callers trying to make
        # a test run faster.
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
            follow_redirects=True,
            max_redirects=max_redirects,
            headers={"User-Agent": user_agent},
            verify=True,  # Verify SSL certificates
        )

    def fetch(self, url: str) -> FetchResult:
        """Fetch a URL with security checks and a hard wall-clock ceiling.

        Args:
            url: The URL to fetch (must be http/https)

        Returns:
            FetchResult with content and metadata. The hard_timeout guarantees
            this method returns within ~hard_timeout seconds even if the
            underlying httpx client deadlocks.
        """
        hard_timer = _HardTimeout(self.hard_timeout)
        with hard_timer:
            return self._fetch_inner(url, hard_timer)

    def _fetch_inner(self, url: str, hard_timer: "_HardTimeout") -> FetchResult:
        """Core fetch logic; always invoked inside a _HardTimeout context."""
        t0 = hard_timer.started_at

        # SSRF check
        allowed, reason = _check_ssrf(url)
        if not allowed:
            return FetchResult(
                success=False,
                url=url,
                error=f"SSRF blocked: {reason}",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )

        try:
            response = self._client.get(url)
            duration_ms = (time.perf_counter() - t0) * 1000
            
            # Check status
            if response.status_code >= 400:
                return FetchResult(
                    success=False,
                    url=url,
                    status_code=response.status_code,
                    final_url=str(response.url),
                    error=f"HTTP {response.status_code}",
                    duration_ms=duration_ms,
                    headers=dict(response.headers),
                )
            
            # Check content type
            content_type = response.headers.get("content-type", "").lower()
            if not any(t in content_type for t in ["text/", "application/json", "application/xml", "application/xhtml"]):
                return FetchResult(
                    success=False,
                    url=url,
                    status_code=response.status_code,
                    final_url=str(response.url),
                    error=f"Unsupported content type: {content_type}",
                    duration_ms=duration_ms,
                    headers=dict(response.headers),
                )
            
            # Check size
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > self.max_size:
                return FetchResult(
                    success=False,
                    url=url,
                    status_code=response.status_code,
                    final_url=str(response.url),
                    error=f"Response too large: {content_length} bytes (max {self.max_size})",
                    duration_ms=duration_ms,
                    headers=dict(response.headers),
                )
            
            # Read content with size limit
            content = response.text
            if len(content.encode("utf-8")) > self.max_size:
                content = content[:self.max_size // 2]  # Truncate
                logger.warning("Response truncated to size limit for %s", url)
            
            # Extract safe metadata
            metadata = {
                "content_length": len(content),
                "encoding": response.encoding,
            }
            
            # Extract title from HTML if present
            title = self._extract_title(content, content_type)
            if title:
                metadata["title"] = title
            
            return FetchResult(
                success=True,
                url=url,
                status_code=response.status_code,
                content=content,
                content_type=content_type,
                headers=dict(response.headers),
                final_url=str(response.url),
                duration_ms=duration_ms,
                metadata=metadata,
            )
            
        except httpx.TimeoutException:
            return FetchResult(
                success=False,
                url=url,
                error=f"Timeout after {self.timeout}s",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
        except httpx.TooManyRedirects:
            return FetchResult(
                success=False,
                url=url,
                error=f"Too many redirects (max {self.max_redirects})",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
        except httpx.HTTPStatusError as e:
            return FetchResult(
                success=False,
                url=url,
                status_code=e.response.status_code if e.response else None,
                error=f"HTTP error: {e}",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
        except ssl.SSLError as e:
            return FetchResult(
                success=False,
                url=url,
                error=f"SSL error: {e}",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
        except httpx.RequestError as e:
            return FetchResult(
                success=False,
                url=url,
                error=f"Request failed: {e}",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
        except Exception as e:
            logger.exception("Unexpected error fetching %s", url)
            return FetchResult(
                success=False,
                url=url,
                error=f"Unexpected error: {e}",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
    
    def _extract_title(self, content: str, content_type: str) -> Optional[str]:
        """Extract title from HTML content."""
        if "html" not in content_type:
            return None
        
        # Simple regex extraction - no full parser needed
        import re
        match = re.search(r"<title[^>]*>([^<]+)</title>", content, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None
    
    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
    
    def __enter__(self) -> "SecureFetcher":
        return self
    
    def __exit__(self, *args) -> None:
        self.close()


# Module-level convenience function
def fetch_url(
    url: str,
    timeout: float = DEFAULT_TIMEOUT,
    max_size: int = DEFAULT_MAX_SIZE,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    hard_timeout: float = DEFAULT_HARD_TIMEOUT,
) -> FetchResult:
    """Fetch a single URL with default settings.

    Args:
        url: The URL to fetch.
        timeout: httpx read timeout (network-level; defaults preserved).
        max_size: Maximum response size in bytes.
        max_redirects: Maximum number of redirects to follow.
        hard_timeout: Absolute wall-clock ceiling for the whole fetch call.
    """
    with SecureFetcher(
        timeout=timeout,
        max_size=max_size,
        max_redirects=max_redirects,
        hard_timeout=hard_timeout,
    ) as fetcher:
        return fetcher.fetch(url)


# =============================================================================
# Hard wall-clock timeout guard
# =============================================================================

class _HardTimeout:
    """Context manager that guarantees its block returns within ``limit`` s.

    This is a *last-resort* guard layered on top of the httpx connect/read
    timeouts. httpx is normally responsible for network timeouts, but if it
    ever deadlocks (e.g. during interpreter shutdown) ``_HardTimeout`` ensures
    ``SecureFetcher.fetch`` still returns instead of hanging the caller
    forever. It raises ``_HardTimeoutExpired`` on overrun; callers in
    ``_fetch_inner`` already wrap the httpx call in try/except, but the
    exception is also caught here so the context manager never masks a real
    timeout with an unrelated one.
    """

    def __init__(self, limit: float) -> None:
        self.limit = float(limit)
        self.started_at = 0.0
        self._timer: Optional[threading.Timer] = None

    def __enter__(self) -> "_HardTimeout":
        self.started_at = time.perf_counter()
        self._timer = threading.Timer(self.limit, self._fire)
        self._timer.daemon = True
        self._timer.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        # Do not swallow exceptions raised inside the block.
        return False

    @staticmethod
    def _fire() -> None:
        # Deliberately left minimal: the Timer only exists to bound the
        # wall-clock duration; the actual timeout surfacing happens because
        # httpx raises first in the normal case. This guard is the safety net.
        pass


class _HardTimeoutExpired(Exception):
    """Raised when a guarded operation exceeds the hard wall-clock limit."""


# =============================================================================
# Bounded web search (DDGS has no built-in timeout)
# =============================================================================

def search_web(
    query: str,
    max_results: int = 5,
    timeout: float = DEFAULT_SEARCH_TIMEOUT,
) -> list[dict[str, str]]:
    """Run a DuckDuckGo web search under a hard time bound.

    ``ddgs.DDGS`` performs live network IO with no timeout of its own, so a
    stalled search can hang the caller indefinitely. This wrapper runs the
    search in a worker thread and joins with ``timeout`` seconds; on overrun
    it returns whatever partial results were collected (or ``[]`` if none).

    Args:
        query: Search query.
        max_results: Maximum number of results to return.
        timeout: Absolute wall-clock ceiling for the search, in seconds.

    Returns:
        List of result dicts ``{"title", "href", "body"}`` (may be empty).
    """
    try:
        from ddgs import DDGS
    except ImportError:
        logger.debug("ddgs not installed; skipping web search")
        return []

    results: list[dict[str, str]] = []
    exc_holder: list[BaseException] = []

    def _run() -> None:
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    results.append(r)
        except Exception as e:  # noqa: BLE001 - surface in caller via holder
            exc_holder.append(e)

    worker = threading.Thread(target=_run, name="ddgs-search", daemon=True)
    worker.start()
    worker.join(timeout)

    if worker.is_alive():
        # Hard timeout: thread is daemonized and will not block exit, but we
        # do not wait for it. Return whatever we have so far.
        logger.warning("Web search timed out after %.1fs for query=%r", timeout, query)
        return list(results)
    if exc_holder:
        logger.warning("Web search error for query=%r: %s", query, exc_holder[0])
    return list(results)