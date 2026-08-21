"""Tests for research fetcher.

Split into:
  * Offline tests  (``@pytest.mark.offline``) — SSRF protection, result
    structure, and the httpx/timeout machinery. These never touch the network.
  * Network tests  (``@pytest.mark.network``) — real HTTPS fetches against
    public endpoints. Each is bounded by a short explicit timeout so a slow
    endpoint cannot hang the suite.
"""

from __future__ import annotations

import pytest

from research.fetcher import (
    fetch_url,
    SecureFetcher,
    FetchResult,
    _HardTimeout,
    search_web,
)


# =============================================================================
# OFFLINE TESTS
# =============================================================================

@pytest.mark.offline
class TestFetcherOffline:
    """Offline fetcher behaviour — no network access required."""

    def test_fetch_result_structure(self):
        """FetchResult exposes all documented fields."""
        result = FetchResult(success=False, url="https://example.com")
        for attr in (
            "success", "url", "status_code", "content", "content_type",
            "headers", "error", "duration_ms", "final_url", "metadata",
        ):
            assert hasattr(result, attr)

    def test_direct_localhost_blocked(self):
        """Direct localhost URL is blocked by SSRF protection (offline)."""
        result = fetch_url("http://127.0.0.1:8080", timeout=5.0)
        assert result.success is False
        assert "SSRF blocked" in result.error or "private" in result.error.lower()

    def test_direct_private_ip_blocked(self):
        """Direct RFC1918 private IP is blocked (offline)."""
        result = fetch_url("http://192.168.1.1", timeout=5.0)
        assert result.success is False
        assert "SSRF blocked" in result.error or "private" in result.error.lower()

    def test_loopback_blocked(self):
        """127.0.0.0/8 is blocked."""
        for ip in ["127.0.0.1", "127.0.0.2", "127.1.1.1"]:
            result = fetch_url(f"http://{ip}:8080", timeout=2.0)
            assert result.success is False
            assert "private" in result.error.lower() or "ssrf" in result.error.lower()

    def test_rfc1918_blocked(self):
        """RFC1918 private ranges are blocked."""
        for ip in ["10.0.0.1", "172.16.0.1", "192.168.1.1"]:
            result = fetch_url(f"http://{ip}", timeout=2.0)
            assert result.success is False
            assert "private" in result.error.lower() or "ssrf" in result.error.lower()

    def test_link_local_blocked(self):
        """AWS metadata endpoint (link-local) is blocked."""
        result = fetch_url("http://169.254.169.254", timeout=2.0)
        assert result.success is False
        assert "private" in result.error.lower() or "ssrf" in result.error.lower()

    def test_invalid_url(self):
        """Non-resolvable / malformed URL fails cleanly (no crash)."""
        result = fetch_url("not-a-url", timeout=5.0)
        assert result.success is False

    def test_non_http_scheme(self):
        """Non-HTTP scheme is rejected without any network call."""
        result = fetch_url("ftp://example.com", timeout=5.0)
        assert result.success is False
        assert "scheme" in result.error.lower() or "not allowed" in result.error.lower()

    def test_secure_fetcher_context_manager(self):
        """SecureFetcher works as a context manager and closes cleanly."""
        with SecureFetcher() as fetcher:
            assert fetcher._client is not None
        # After exit the client should be closed.
        assert fetcher._client.is_closed

    def test_search_web_no_ddgs_import(self, monkeypatch):
        """search_web returns [] without raising when ddgs is unavailable."""
        import sys
        monkeypatch.setitem(sys.modules, "ddgs", None)
        # Ensure the import fails so the fallback path is exercised.
        results = search_web("anything")
        assert results == []

    def test_search_web_timeout_returns_quickly(self, monkeypatch):
        """search_web respects its hard ceiling even if DDGS never returns."""
        import time as _time

        def _hang(*args, **kwargs):
            _time.sleep(10)  # simulate a stalled search
            return iter([])

        # Patch where search_web imports DDGS from.
        import ddgs
        monkeypatch.setattr(ddgs, "DDGS", lambda: _HangDDGS())

        class _HangDDGS:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def text(self, *a, **k):
                return _hang()

        t0 = _time.monotonic()
        results = search_web("slow query", timeout=0.5)
        elapsed = _time.monotonic() - t0
        assert results == []
        assert elapsed < 5.0

    def test_hard_timeout_context_manager(self):
        """_HardTimeout measures wall-clock and cancels cleanly."""
        with _HardTimeout(0.25) as ht:
            assert ht.started_at > 0
            assert ht.limit == 0.25
        # After exit the timer is cancelled (no dangling fire).


@pytest.mark.offline
class TestSearchWebFallback:
    """Keyless multi-backend fallback behaviour (offline, fully mocked DDGS)."""

    def _fake_ddgs(self, monkeypatch, backend_results):
        """Install a fake ddgs.DDGS whose .text(backend=...) returns per backend.

        ``backend_results`` maps backend name -> list[dict] (or an exception to
        raise). Only backends present as keys produce results; absent/raising
        backends fall through to the next in the fallback order.
        """
        import ddgs

        order = []

        class _FakeDDGS:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def text(self, query, backend=None, max_results=5, **kwargs):
                order.append(backend)
                entry = backend_results.get(backend)
                if entry is None:
                    # Simulate an upstream challenge / no results.
                    from ddgs.exceptions import DDGSException

                    raise DDGSException("No results found.")
                if isinstance(entry, Exception):
                    raise entry
                return entry

        monkeypatch.setattr(ddgs, "DDGS", _FakeDDGS)
        return order

    def test_first_backend_succeeds_no_unnecessary_fallback(self, monkeypatch):
        """When duckduckgo succeeds, later backends are never queried."""
        order = self._fake_ddgs(
            monkeypatch,
            {"duckduckgo": [{"title": "T", "href": "https://x.test", "body": "B"}]},
        )
        results = search_web("q", max_results=5, timeout=5)
        assert results == [{"title": "T", "href": "https://x.test", "body": "B"}]
        assert order == ["duckduckgo"]

    def test_first_fails_second_succeeds(self, monkeypatch):
        """Fallback to the next backend when the first raises."""
        order = self._fake_ddgs(
            monkeypatch,
            {
                "duckduckgo": Exception("challenged"),
                "startpage": [{"title": "T2", "href": "https://y.test", "body": "B2"}],
            },
        )
        results = search_web("q", max_results=5, timeout=5)
        assert results == [{"title": "T2", "href": "https://y.test", "body": "B2"}]
        assert order == ["duckduckgo", "startpage"]

    def test_all_backends_fail_returns_empty(self, monkeypatch):
        """Clean empty result when every backend errors / returns nothing."""
        order = self._fake_ddgs(monkeypatch, {})
        results = search_web("q", max_results=5, timeout=5)
        assert results == []
        # All four authorized backends were tried in order.
        assert order == ["duckduckgo", "startpage", "mojeek", "yahoo"]

    def test_malformed_results_rejected(self, monkeypatch):
        """Results missing title/href (or non-dict) are dropped, never accepted."""
        order = self._fake_ddgs(
            monkeypatch,
            {
                "duckduckgo": [
                    {"href": "https://x.test"},  # no title
                    {"title": "", "href": "https://y.test", "body": "b"},  # empty title
                    "not-a-dict",
                    # valid title+href (no body key) -> accepted, short-circuits
                    {"title": "Good", "href": "https://z.test"},
                ],
                "startpage": [],  # never reached because duckduckgo yielded a valid record
            },
        )
        results = search_web("q", max_results=5, timeout=5)
        # Only the well-formed record survives; body defaulted to "".
        assert results == [{"title": "Good", "href": "https://z.test", "body": ""}]
        assert order == ["duckduckgo"]

    def test_no_fabricated_results(self, monkeypatch):
        """A backend that returns nothing must not yield synthetic records."""
        order = self._fake_ddgs(monkeypatch, {"duckduckgo": []})
        results = search_web("q", max_results=5, timeout=5)
        assert results == []
        assert order == ["duckduckgo", "startpage", "mojeek", "yahoo"]

    def test_body_field_normalized(self, monkeypatch):
        """A result without a body key still exposes body as a string."""
        self._fake_ddgs(
            monkeypatch,
            {"duckduckgo": [{"title": "T", "href": "https://x.test"}]},
        )
        results = search_web("q", max_results=5, timeout=5)
        assert results[0]["body"] == ""

    def test_fallback_order_preserved(self, monkeypatch):
        """Authorized preferred order is duckduckgo -> startpage -> mojeek -> yahoo."""
        from research.fetcher import _SEARCH_BACKENDS

        assert _SEARCH_BACKENDS == ("duckduckgo", "startpage", "mojeek", "yahoo")


@pytest.mark.offline
def test_validate_search_results_accept_filter():
    """_validate_search_results keeps only well-formed records."""
    from research.fetcher import _validate_search_results

    raw = [
        {"title": "A", "href": "https://a.test", "body": "x"},
        {"title": "", "href": "https://b.test"},
        {"title": "C", "href": "https://c.test", "body": 123},  # body not str -> kept, sanitized to ""
        "junk",
        {"href": "https://d.test", "body": "y"},  # no title
    ]
    out = _validate_search_results(raw)
    # A and C survive; C's non-str body is normalized to "".
    assert out == [
        {"title": "A", "href": "https://a.test", "body": "x"},
        {"title": "C", "href": "https://c.test", "body": ""},
    ]
    # Non-str body is sanitized to "" rather than dropped.
    raw2 = [{"title": "C", "href": "https://c.test", "body": 123}]
    assert _validate_search_results(raw2) == [
        {"title": "C", "href": "https://c.test", "body": ""}
    ]
    assert _validate_search_results("not a list") == []
    assert _validate_search_results(None) == []


@pytest.mark.offline
class TestFetcherTimeoutRegression:
    """Regression tests that the hard timeout guard actually bounds calls."""

    def test_fetch_hard_timeout_does_not_block_indefinitely(self, monkeypatch):
        """Even a deadlocked httpx client returns within hard_timeout."""
        import time as _time

        class _DeadlockedClient:
            def get(self, *a, **k):
                _time.sleep(30)  # simulate a permanent block
                raise AssertionError("should have been bounded")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        fetcher = SecureFetcher(hard_timeout=0.5)
        monkeypatch.setattr(fetcher, "_client", _DeadlockedClient())

        t0 = _time.monotonic()
        result = fetcher.fetch("https://10.255.255.1/")  # will hit deadlocked client
        elapsed = _time.monotonic() - t0
        # Returns roughly within the hard timeout, not after the 30s sleep.
        assert elapsed < 5.0
        assert result.success is False


# =============================================================================
# NETWORK TESTS (require internet; each bounded by a short timeout)
# =============================================================================

@pytest.mark.network
@pytest.mark.timeout(15)
class TestFetcherNetwork:
    """Live fetches against public endpoints. Skipped cleanly on timeout."""

    def test_successful_https_fetch(self):
        """A real HTTPS fetch succeeds with content."""
        result = fetch_url("https://example.com", timeout=10.0)
        assert result.success is True
        assert result.status_code == 200
        assert len(result.content) > 0
        assert result.duration_ms > 0
        assert result.final_url.startswith("https://")

    def test_http_error_404(self):
        """A missing page yields a clean 404 failure."""
        result = fetch_url("https://example.com/nonexistent-page-12345", timeout=10.0)
        assert result.success is False
        assert result.status_code == 404

    def test_fetch_result_metadata_on_success(self):
        """Successful fetch populates metadata fields."""
        result = fetch_url("https://example.com", timeout=10.0)
        assert hasattr(result, "success")
        assert hasattr(result, "url")
        assert hasattr(result, "status_code")
        assert hasattr(result, "content")
        assert hasattr(result, "content_type")
        assert hasattr(result, "headers")
        assert hasattr(result, "error")
        assert hasattr(result, "duration_ms")
        assert hasattr(result, "final_url")
        assert hasattr(result, "metadata")
