"""MCP client for dynamic tool discovery.

Purpose: keep `tools.py` functional while allowing new tools to be loaded
dynamically from filesystem-based tool plugins. Each plugin is a Python module
/ folder that exposes a `Tool` and a `tool_spec(...)` factory.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from modules.config import JarvisConfig
from modules.logger import get_logger

logger = get_logger("mcp")


def discover_tools(mcp_roots: list[Path]) -> list[Any]:
    tools = []
    for root in mcp_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if path.name.startswith("_"):
                continue
            spec = importlib.util.spec_from_file_location(path.stem, str(path))
            if spec is None or spec.loader is None:
                continue
            try:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[path.stem] = mod
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
                if hasattr(mod, "tool_spec") and hasattr(mod, "Tool"):
                    tool_instance = mod.tool_spec()
                    tools.append(tool_instance)
            except Exception as exc:
                logger.warning("Tool plugin load failed %s: %s", path, exc)
    return tools


class MCPClient:
    def __init__(self, config: JarvisConfig) -> None:
        self.config = config
        self.plugin_dirs = [config.project_root / "mcp_plugins"]

    def load_plugins(self) -> list[Any]:
        return discover_tools(self.plugin_dirs)
