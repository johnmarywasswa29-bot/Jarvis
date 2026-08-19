"""Content extractor for web research.

Extracts useful article/page text while removing navigation, scripts, styles,
ads, and boilerplate. Uses Trafilatura when available with a fallback to
basic HTML parsing.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("research.extractor")


@dataclass
class ExtractionResult:
    """Result of content extraction."""
    success: bool
    text: str = ""
    title: str = ""
    url: str = ""
    metadata: dict = field(default_factory=dict)
    error: str = ""
    method: str = ""


# Check for optional dependencies
try:
    import trafilatura
    _HAS_TRAFILATURA = True
except ImportError:
    _HAS_TRAFILATURA = False
    logger.debug("Trafilatura not available, using fallback extractor")

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False
    logger.debug("BeautifulSoup4 not available")


def _clean_text(text: str) -> str:
    """Clean extracted text."""
    if not text:
        return ""
    
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)
    # Remove zero-width chars
    text = text.replace("\u200b", "").replace("\ufeff", "")
    return text.strip()


def _extract_with_trafilatura(html: str, url: str = "") -> ExtractionResult:
    """Extract content using Trafilatura."""
    if not _HAS_TRAFILATURA:
        return ExtractionResult(success=False, error="Trafilatura not available", method="trafilatura")
    
    try:
        # Extract with metadata
        extracted = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
            favor_precision=True,
        )
        
        if not extracted:
            return ExtractionResult(
                success=False,
                error="Trafilatura extracted no content",
                method="trafilatura",
            )
        
        # Extract metadata
        metadata = trafilatura.extract_metadata(html)
        meta_dict = {}
        if metadata:
            meta_dict = {
                "title": metadata.title or "",
                "author": metadata.author or "",
                "date": metadata.date or "",
                "description": metadata.description or "",
                "categories": metadata.categories or [],
                "tags": metadata.tags or [],
            }
        
        # Get title from metadata or try to extract
        title = meta_dict.get("title", "")
        
        return ExtractionResult(
            success=True,
            text=_clean_text(extracted),
            title=title,
            metadata=meta_dict,
            method="trafilatura",
        )
    except Exception as e:
        logger.debug("Trafilatura extraction failed: %s", e)
        return ExtractionResult(success=False, error=str(e), method="trafilatura")


def _extract_with_bs4(html: str, url: str = "") -> ExtractionResult:
    """Extract content using BeautifulSoup fallback."""
    if not _HAS_BS4:
        return ExtractionResult(success=False, error="BeautifulSoup4 not available", method="bs4")
    
    try:
        soup = BeautifulSoup(html, "html.parser")
        
        # Remove unwanted elements
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript", "iframe", "svg"]):
            tag.decompose()
        
        # Remove common ad/boilerplate classes
        for selector in [
            "[class*='ad']", "[class*='Ad']", "[id*='ad']",
            "[class*='banner']", "[class*='cookie']", "[class*='consent']",
            "[class*='newsletter']", "[class*='popup']", "[class*='modal']",
            "[class*='sidebar']", "[class*='widget']", "[class*='social']",
            "[role='banner']", "[role='navigation']", "[role='complementary']",
        ]:
            for el in soup.select(selector):
                el.decompose()
        
        # Try to find main content
        main_candidates = [
            soup.select_one("main"),
            soup.select_one("article"),
            soup.select_one("[role='main']"),
            soup.select_one(".content"),
            soup.select_one("#content"),
            soup.select_one(".post"),
            soup.select_one(".entry"),
        ]
        
        content_element = None
        for candidate in main_candidates:
            if candidate and len(candidate.get_text(strip=True)) > 200:
                content_element = candidate
                break
        
        if not content_element:
            # Fallback to body
            content_element = soup.body or soup
        
        # Extract text
        text = content_element.get_text(separator="\n", strip=True)
        text = _clean_text(text)
        
        if len(text) < 100:
            return ExtractionResult(
                success=False,
                error="Extracted text too short",
                method="bs4",
            )
        
        # Extract title
        title_tag = soup.select_one("title")
        title = title_tag.get_text(strip=True) if title_tag else ""
        
        # Extract metadata
        meta_dict = {"title": title}
        for meta in soup.select("meta"):
            if meta.get("property") == "og:title" and not title:
                title = meta.get("content", "")
                meta_dict["title"] = title
            elif meta.get("name") == "description":
                meta_dict["description"] = meta.get("content", "")
            elif meta.get("property") == "og:description":
                meta_dict["description"] = meta.get("content", "")
        
        return ExtractionResult(
            success=True,
            text=text,
            title=title,
            metadata=meta_dict,
            method="bs4",
        )
    except Exception as e:
        logger.debug("BS4 extraction failed: %s", e)
        return ExtractionResult(success=False, error=str(e), method="bs4")


def extract_content(
    html: str,
    url: str = "",
    prefer_trafilatura: bool = True,
) -> ExtractionResult:
    """Extract useful content from HTML.
    
    Args:
        html: HTML content to extract from
        url: Source URL (for metadata)
        prefer_trafilatura: Try Trafilatura first if available
        
    Returns:
        ExtractionResult with extracted text and metadata
    """
    if not html or len(html) < 100:
        return ExtractionResult(
            success=False,
            error="HTML content too short or empty",
            method="none",
        )
    
    # Try Trafilatura first (best quality)
    if prefer_trafilatura and _HAS_TRAFILATURA:
        result = _extract_with_trafilatura(html, url)
        if result.success:
            result.url = url
            return result
        logger.debug("Trafilatura failed, falling back: %s", result.error)
    
    # Fallback to BeautifulSoup
    if _HAS_BS4:
        result = _extract_with_bs4(html, url)
        result.url = url
        return result
    
    # No extractors available
    return ExtractionResult(
        success=False,
        error="No extraction libraries available (install trafilatura or beautifulsoup4)",
        method="none",
    )


# Module-level convenience function
def extract_article(html: str, url: str = "") -> ExtractionResult:
    """Extract article content from HTML (convenience function)."""
    return extract_content(html, url)