"""Tests for research extractor."""

from __future__ import annotations

import pytest
from research.extractor import extract_content, ExtractionResult


class TestExtractor:
    """Test the content extractor."""
    
    def test_html_extraction(self):
        """Test basic HTML extraction."""
        html = """
        <!DOCTYPE html>
        <html>
        <head><title>Test Article</title></head>
        <body>
            <nav>Navigation menu</nav>
            <main>
                <h1>Article Title</h1>
                <p>This is the main content of the article. It has multiple paragraphs.</p>
                <p>Second paragraph with more information.</p>
            </main>
            <footer>Footer content</footer>
            <script>console.log('ads');</script>
        </body>
        </html>
        """
        result = extract_content(html, url="https://example.com/article")
        assert result.success is True
        assert "Article Title" in result.text
        assert "main content" in result.text
        assert "Second paragraph" in result.text
        assert result.title == "Article Title"
        assert result.method in ("trafilatura", "bs4")
    
    def test_boilerplate_removal(self):
        """Test that navigation, scripts, styles are removed."""
        html = """
        <html>
        <head><title>Test</title><style>body {color: red;}</style></head>
        <body>
            <nav>Navigation</nav>
            <header>Header</header>
            <main><p>Main content here.</p></main>
            <footer>Footer</footer>
            <script>alert('ads');</script>
            <aside>Sidebar</aside>
        </body>
        </html>
        """
        result = extract_content(html)
        assert result.success is True
        assert "Main content here" in result.text
        assert "Navigation" not in result.text
        assert "Header" not in result.text
        assert "Footer" not in result.text
        assert "alert" not in result.text
        assert "Sidebar" not in result.text
    
    def test_metadata_extraction(self):
        """Test metadata extraction."""
        html = """
        <html>
        <head>
            <title>Page Title</title>
            <meta name="description" content="Page description">
            <meta property="og:title" content="OG Title">
            <meta property="og:description" content="OG Description">
        </head>
        <body><main><p>Content</p></main></body>
        </html>
        """
        result = extract_content(html, url="https://example.com")
        assert result.success is True
        assert "title" in result.metadata or result.title
        assert result.method in ("trafilatura", "bs4")
    
    def test_empty_html(self):
        """Test empty/short HTML returns failure."""
        result = extract_content("")
        assert result.success is False
        
        result = extract_content("<html></html>")
        assert result.success is False
    
    def test_unextractable_page(self):
        """Test page with no extractable content."""
        html = "<html><body><div class='ad'>Ad content only</div></body></html>"
        result = extract_content(html)
        # May succeed or fail depending on extractor, but should not crash
        assert hasattr(result, 'success')
    
    def test_extraction_result_structure(self):
        """Test ExtractionResult has all required fields."""
        html = "<html><head><title>Test</title></head><body><main><p>Content</p></main></body></html>"
        result = extract_content(html)
        assert hasattr(result, 'success')
        assert hasattr(result, 'text')
        assert hasattr(result, 'title')
        assert hasattr(result, 'url')
        assert hasattr(result, 'metadata')
        assert hasattr(result, 'error')
        assert hasattr(result, 'method')
    
    def test_method_is_identified(self):
        """Test that extraction method is identified."""
        html = "<html><head><title>Test</title></head><body><main><p>Content with enough text to be extractable by the algorithm.</p></main></body></html>"
        result = extract_content(html)
        assert result.success is True
        assert result.method in ("trafilatura", "bs4", "none")
    
    def test_url_in_metadata(self):
        """Test URL is stored in metadata."""
        html = """<html><head><title>Test</title></head><body><main><p>Content with enough text to pass the minimum length check for extraction.</p></main></body></html>"""
        result = extract_content(html, url="https://example.com/article")
        assert result.url == "https://example.com/article"
    
    def test_non_html_content(self):
        """Test non-HTML content handling."""
        # Plain text should fail or be handled gracefully
        result = extract_content("Just plain text", url="https://example.com")
        # Should not crash
        assert hasattr(result, 'success')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])