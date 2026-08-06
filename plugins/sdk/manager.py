"""PluginManager: orchestrates lifecycle, permissions, events."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, List, Optional

from plugins.sdk.api import PluginAPI
from plugins.sdk.events import PluginEvents, PluginEvent
from plugins.sdk.loader import PluginLoader, PluginLoadError
from plugins.sdk.permissions import PluginPermissions
from plugins.sdk.registry import PluginRegistry
from plugins.sdk.sandbox import PluginSandbox
from plugins.sdk.state import PluginContext, PluginManifest


class PluginManager:
    def __init__(
        self,
        plugins_dir: Optional[str] = None,
        registry: Optional[PluginRegistry] = None,
        events: Optional[PluginEvents] = None,
        api: Optional[PluginAPI] = None,
    ) -> None:
        self.plugins_dir = Path(plugins_dir) if plugins_dir else Path(__file__).resolve().parents[1] / "plugins"
        self.registry = registry or PluginRegistry()
        self.events = events or PluginEvents()
        self.api = api or PluginAPI()
        self._lock = threading.RLock()

    def discover(self) -> list[PluginContext]:
        contexts = []
        if not self.plugins_dir.exists():
            return contexts
        for child in self.plugins_dir.iterdir():
            if child.name == "sdk" or not child.is_dir():
                continue
            manifest_path = child / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = PluginLoader.load_manifest(child)
                ctx = PluginContext(
                    plugin_id=child.name,
                    manifest=manifest,
                    install_path=str(child),
                )
                contexts.append(ctx)
                self.registry.register(ctx)
            except Exception:
                continue
        return contexts

    def install(self, plugin_dir: Path) -> PluginContext:
        manifest = PluginLoader.load_manifest(plugin_dir)
        ctx = PluginContext(
            plugin_id=plugin_dir.name,
            manifest=manifest,
            install_path=str(plugin_dir),
        )
        self.registry.register(ctx)
        self.events.publish(PluginEvent(event_type="plugin_installed", data={"plugin_id": ctx.plugin_id}))
        return ctx

    def load(self, plugin_id: str) -> PluginContext:
        ctx = self.registry.get(plugin_id)
        if ctx is None:
            raise PluginLoadError(f"Unknown plugin: {plugin_id}")
        if ctx.loaded:
            return ctx
        plugin_dir = Path(ctx.install_path)
        try:
            module = PluginLoader.load_entry_point(plugin_dir, ctx.manifest.entry_point)
            ctx.instance = module
            ctx.loaded = True
            ctx.error = None
        except Exception as e:
            ctx.error = str(e)
            raise PluginLoadError(str(e))
        self.events.publish(PluginEvent(event_type="plugin_loaded", data={"plugin_id": plugin_id}))
        return ctx

    def enable(self, plugin_id: str, auto_load: bool = True) -> None:
        with self._lock:
            ctx = self.registry.get(plugin_id)
            if ctx is None:
                raise PluginLoadError(f"Unknown plugin: {plugin_id}")
            if auto_load and not ctx.loaded:
                self.load(plugin_id)
            ctx.enabled = True
            self.events.publish(PluginEvent(event_type="plugin_enabled", data={"plugin_id": plugin_id}))

    def disable(self, plugin_id: str) -> None:
        with self._lock:
            ctx = self.registry.get(plugin_id)
            if ctx is None:
                raise PluginLoadError(f"Unknown plugin: {plugin_id}")
            ctx.enabled = False
            self.events.publish(PluginEvent(event_type="plugin_disabled", data={"plugin_id": plugin_id}))

    def reload(self, plugin_id: str) -> PluginContext:
        with self._lock:
            ctx = self.registry.get(plugin_id)
            if ctx is None:
                raise PluginLoadError(f"Unknown plugin: {plugin_id}")
            if ctx.loaded:
                self.unload(plugin_id)
            return self.load(plugin_id)

    def unload(self, plugin_id: str) -> None:
        with self._lock:
            ctx = self.registry.get(plugin_id)
            if ctx is None or not ctx.loaded:
                return
            ctx.instance = None
            ctx.loaded = False
            self.events.publish(PluginEvent(event_type="plugin_unloaded", data={"plugin_id": plugin_id}))

    def uninstall(self, plugin_id: str) -> None:
        with self._lock:
            ctx = self.registry.get(plugin_id)
            if ctx is None:
                raise PluginLoadError(f"Unknown plugin: {plugin_id}")
            if ctx.enabled:
                self.disable(plugin_id)
            if ctx.loaded:
                self.unload(plugin_id)
            self.registry.unregister(plugin_id)
            self.events.publish(PluginEvent(event_type="plugin_uninstalled", data={"plugin_id": plugin_id}))

    def update(self, plugin_id: str) -> PluginContext:
        return self.reload(plugin_id)

    def list_plugins(self) -> list[dict[str, Any]]:
        result = []
        for ctx in self.registry.list_plugins():
            result.append(
                {
                    "plugin_id": ctx.plugin_id,
                    "name": ctx.manifest.name,
                    "version": ctx.manifest.version,
                    "author": ctx.manifest.author,
                    "enabled": ctx.enabled,
                    "loaded": ctx.loaded,
                    "error": ctx.error,
                }
            )
        return result

    def sandbox(self, plugin_id: str, permissions: Optional[PluginPermissions] = None) -> PluginSandbox:
        ctx = self.registry.get(plugin_id)
        if ctx is None or not ctx.loaded:
            raise PluginLoadError(f"Plugin not loaded: {plugin_id}")
        perms = permissions or PluginPermissions(ctx.manifest.permissions)
        return PluginSandbox(context=ctx, permissions=perms)
