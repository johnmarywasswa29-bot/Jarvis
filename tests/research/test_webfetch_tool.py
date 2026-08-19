"""Tests for WebFetchTool integration.

Offline vs network split:
  * ``@pytest.mark.offline`` — unit tests for WebFetchTool (can_handle,
    argument validation, mocked fetch/extract) and registry wiring. These
    never touch the network.
  * ``@pytest.mark.network`` — a live fetch through the real fetcher. Bounded
    by a short timeout; a network timeout yields a clean failure/skip.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from modules.tools import WebFetchTool, ToolRegistry
from modules.config import JarvisConfig
from research.fetcher import FetchResult
from research.extractor import ExtractionResult


@pytest.mark.offline
class TestWebFetchTool:
    """Test the WebFetchTool (offline / mocked)."""

    def setup_method(self):
        self.tool = WebFetchTool()

    def test_can_handle_fetch(self):
        assert self.tool.can_handle("fetch https://example.com") is True
        assert self.tool.can_handle("read url https://example.com") is True
        assert self.tool.can_handle("open url https://example.com") is True
        assert self.tool.can_handle("read page https://example.com") is True
        assert self.tool.can_handle("fetch page https://example.com") is True
        assert self.tool.can_handle("search for cats") is False
        assert self.tool.can_handle("hello world") is False

    def test_execute_missing_url(self):
        result = self.tool.execute()
        assert result.success is False
        assert "Missing URL" in result.error

    def test_execute_invalid_scheme(self):
        result = self.tool.execute(url="ftp://example.com")
        assert result.success is False
        assert "Unsupported scheme" in result.error

    @patch("modules.tools.fetch_url")
    @patch("modules.tools.extract_content")
    def test_execute_success(self, mock_extract, mock_fetch):
        mock_fetch.return_value = FetchResult(
            success=True,
            url="https://example.com",
            final_url="https://example.com/article",
            status_code=200,
            content="<html><head><title>Test</title></head><body><main><p>Content</p></main></body></html>",
            content_type="text/html",
            metadata={"title": "Test"},
        )
        mock_extract.return_value = ExtractionResult(
            success=True,
            text="Article content here",
            title="Test Article",
            metadata={"author": "John Doe"},
            method="trafilatura",
        )

        result = self.tool.execute(url="https://example.com/article")

        assert result.success is True
        assert "Fetched:" in result.output
        assert "Test Article" in result.output
        assert "trafilatura" in result.output

    @patch("modules.tools.fetch_url")
    def test_execute_fetch_failure(self, mock_fetch):
        mock_fetch.return_value = FetchResult(
            success=False,
            url="https://example.com",
            error="Timeout after 10s",
        )
        result = self.tool.execute(url="https://example.com")
        assert result.success is False
        assert "Fetch failed" in result.error
        assert "Timeout" in result.error

    @patch("modules.tools.fetch_url")
    @patch("modules.tools.extract_content")
    def test_execute_extraction_failure(self, mock_extract, mock_fetch):
        mock_fetch.return_value = FetchResult(
            success=True,
            url="https://example.com",
            final_url="https://example.com",
            status_code=200,
            content="<html><body><p>Content</p></body></html>",
            content_type="text/html",
        )
        mock_extract.return_value = ExtractionResult(
            success=False,
            error="Trafilatura extracted no content",
            method="trafilatura",
        )
        result = self.tool.execute(url="https://example.com")
        assert result.success is False
        assert "Extraction failed" in result.error

    def test_execute_non_http_scheme(self):
        result = self.tool.execute(url="javascript:alert(1)")
        assert result.success is False
        assert "Unsupported scheme" in result.error

    def test_execute_file_scheme(self):
        result = self.tool.execute(url="file:///etc/passwd")
        assert result.success is False
        assert "Unsupported scheme" in result.error


@pytest.mark.offline
class TestWebFetchToolRegistryIntegration:
    """WebFetchTool integration with ToolRegistry (offline)."""

    def test_tool_registry_includes_web_fetch(self):
        config = JarvisConfig()
        registry = ToolRegistry(config)
        tool_names = [t.name for t in registry.tools]
        assert "web_fetch" in tool_names
        assert "web_search" in tool_names

    def test_select_tools_prefers_web_fetch_for_fetch(self):
        config = JarvisConfig()
        registry = ToolRegistry(config)
        tools = registry.select_tools("fetch https://example.com")
        assert len(tools) == 1
        assert tools[0].name == "web_fetch"

    def test_select_tools_prefers_web_search_for_search(self):
        config = JarvisConfig()
        registry = ToolRegistry(config)
        tools = registry.select_tools("search for python tutorials")
        assert len(tools) >= 1
        assert any(t.name == "web_search" for t in tools)

    def test_run_web_fetch_tool(self):
        config = JarvisConfig()
        registry = ToolRegistry(config)
        fetch_tool = next(t for t in registry.tools if t.name == "web_fetch")
        assert fetch_tool is not None
        with patch("modules.tools.fetch_url") as mock_fetch:
            mock_fetch.return_value = FetchResult(
                success=False, url="https://example.com", error="Test error"
            )
            result = registry.run_tool(fetch_tool, "fetch https://example.com", url="https://example.com")
            assert result.success is False
            assert "Test error" in result.error


@pytest.mark.offline
class TestPermissionSafety:
    """WebFetchTool follows the safety model (offline)."""

    def test_web_fetch_is_read_only(self):
        from modules.permission_manager import PermissionManager
        pm = PermissionManager()
        level = pm.get_level("web_fetch")
        assert level in ("SAFE", "CAUTION")
        assert level != "DANGEROUS"

    def test_web_fetch_does_not_execute_code(self):
        tool = WebFetchTool()
        assert "eval" not in tool.execute.__code__.co_names
        assert "exec" not in tool.execute.__code__.co_names

    def test_no_credentials_sent(self):
        from research.fetcher import SecureFetcher
        fetcher = SecureFetcher()
        assert fetcher._client.cookies is not None
        assert len(fetcher._client.cookies.jar) == 0
        assert "User-Agent" in fetcher._client.headers
        fetcher.close()


@pytest.mark.network
@pytest.mark.timeout(20)
class TestWebFetchToolNetwork:
    """Live fetch through the real fetcher (bounded)."""

    def test_execute_live_fetch(self):
        tool = WebFetchTool()
        result = tool.execute(url="https://example.com")
        # Either it fetched successfully, or failed cleanly without hanging.
        assert result is not None
        assert isinstance(result.success, bool)
