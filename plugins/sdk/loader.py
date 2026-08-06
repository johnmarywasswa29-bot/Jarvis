"""PluginLoader: manifest + entry point loader with safe JSON parsing."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Optional

from plugins.sdk.state import PluginContext, PluginManifest


class PluginLoadError(Exception):
    pass


class PluginLoader:
    @staticmethod
    def load_manifest(plugin_dir: Path) -> PluginManifest:
        manifest_path = plugin_dir / "manifest.json"
        if not manifest_path.exists():
            raise PluginLoadError(f"manifest.json missing in {plugin_dir}")
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise PluginLoadError(f"Invalid manifest JSON: {e}")
        return PluginManifest(
            name=str(data.get("name", "")),
            version=str(data.get("version", "0.0.0")),
            author=str(data.get("author", "")),
            description=str(data.get("description", "")),
            permissions=list(data.get("permissions", [])),
            required_api_version=str(data.get("required_api_version", "1.0.0")),
            dependencies=list(data.get("dependencies", [])),
            entry_point=str(data.get("entry_point", "plugin.py")),
        )

    @staticmethod
    def load_entry_point(plugin_dir: Path, entry_point: str) -> Any:
        module_path = plugin_dir / entry_point
        if not module_path.exists():
            raise PluginLoadError(f"Entry point missing: {module_path}")
        spec = importlib.util.spec_from_file_location(
            f"plugin_{plugin_dir.name}", module_path
        )
        if spec is None or spec.loader is None:
            raise PluginLoadError(f"Cannot load spec for {module_path}")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            raise PluginLoadError(f"Entry point execution failed: {e}")
        return module
