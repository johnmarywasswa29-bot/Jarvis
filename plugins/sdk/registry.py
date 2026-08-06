"""PluginRegistry: in-memory registry of plugin metadata."""
from __future__ import annotations

from typing import Dict, Optional

from plugins.sdk.state import PluginContext, PluginManifest


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: Dict[str, PluginContext] = {}
        self._by_name: Dict[str, str] = {}

    def register(self, context: PluginContext) -> None:
        self._plugins[context.plugin_id] = context
        self._by_name[context.manifest.name.lower()] = context.plugin_id

    def unregister(self, plugin_id: str) -> None:
        ctx = self._plugins.pop(plugin_id, None)
        if ctx:
            self._by_name.pop(ctx.manifest.name.lower(), None)

    def get(self, plugin_id: str) -> Optional[PluginContext]:
        return self._plugins.get(plugin_id)

    def get_by_name(self, name: str) -> Optional[PluginContext]:
        pid = self._by_name.get(name.lower())
        return self._plugins.get(pid) if pid else None

    def list_plugins(self) -> list[PluginContext]:
        return list(self._plugins.values())

    def all(self) -> Dict[str, PluginContext]:
        return dict(self._plugins)
