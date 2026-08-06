"""Phase 8 Plugin SDK tests."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from plugins.sdk.state import PluginManifest, PluginContext, PluginEvent
from plugins.sdk.registry import PluginRegistry
from plugins.sdk.loader import PluginLoader, PluginLoadError
from plugins.sdk.permissions import PluginPermissions, ALL_PERMISSIONS
from plugins.sdk.sandbox import PluginSandbox
from plugins.sdk.events import PluginEvents
from plugins.sdk.api import PluginAPI
from plugins.sdk.manager import PluginManager


def tmp_plugin_dir(name: str) -> Path:
    base = REPO / "tests" / "tmp_plugins" / name
    base.mkdir(parents=True, exist_ok=True)
    manifest = {"name": name, "version": "1.0", "author": "test"}
    (base / "manifest.json").write_text(
        '{"name":"' + name + '","version":"1.0","author":"test"}',
        encoding="utf-8",
    )
    plugin_source = 'name=""""' + name + '"""\nversion="1.0"\n'
    (base / "plugin.py").write_text(plugin_source, encoding="utf-8")
    return base


class TestRegistry(unittest.TestCase):
    def test_register_and_get(self):
        reg = PluginRegistry()
        ctx = PluginContext(plugin_id="p1", manifest=PluginManifest(name="A", version="1.0", author="a"), install_path=".")
        reg.register(ctx)
        assert reg.get("p1") is ctx
        assert reg.get_by_name("A") is ctx
        assert reg.get_by_name("a") is ctx
        assert reg.get_by_name("B") is None

    def test_unregister(self):
        reg = PluginRegistry()
        ctx = PluginContext(plugin_id="p1", manifest=PluginManifest(name="A", version="1.0", author="a"), install_path=".")
        reg.register(ctx)
        reg.unregister("p1")
        assert reg.get("p1") is None
        assert reg.get_by_name("A") is None

    def test_list_plugins(self):
        reg = PluginRegistry()
        reg.register(PluginContext(plugin_id="p1", manifest=PluginManifest(name="A", version="1.0", author="a"), install_path="."))
        reg.register(PluginContext(plugin_id="p2", manifest=PluginManifest(name="B", version="1.0", author="b"), install_path="."))
        assert len(reg.list_plugins()) == 2


class TestLoader(unittest.TestCase):
    def test_load_manifest_valid(self):
        d = tmp_plugin_dir("loader_valid")
        (d / "manifest.json").write_text('{"name":"m","version":"1.0","author":"a"}', encoding="utf-8")
        m = PluginLoader.load_manifest(d)
        assert m.name == "m"
        assert m.version == "1.0"

    def test_load_manifest_missing(self):
        with self.assertRaises(PluginLoadError):
            PluginLoader.load_manifest(REPO / "tests")

    def test_load_manifest_invalid_json(self):
        d = tmp_plugin_dir("loader_invalid")
        (d / "manifest.json").write_text("not json", encoding="utf-8")
        with self.assertRaises(PluginLoadError):
            PluginLoader.load_manifest(d)

    def test_load_entry_point(self):
        d = tmp_plugin_dir("loader_entry")
        (d / "manifest.json").write_text('{"name":"m","version":"1.0","author":"a","entry_point":"plugin.py"}', encoding="utf-8")
        (d / "plugin.py").write_text('name="m"\nversion="1.0"\n', encoding="utf-8")
        module = PluginLoader.load_entry_point(d, "plugin.py")
        assert module.name == "m"

    def test_load_entry_point_missing(self):
        d = tmp_plugin_dir("loader_missing_ep")
        (d / "manifest.json").write_text('{"name":"m","version":"1.0","author":"a"}', encoding="utf-8")
        with self.assertRaises(PluginLoadError):
            PluginLoader.load_entry_point(d, "missing.py")

    def test_load_entry_point_syntax_error(self):
        d = tmp_plugin_dir("loader_syntax")
        (d / "manifest.json").write_text('{"name":"m","version":"1.0","author":"a"}', encoding="utf-8")
        (d / "plugin.py").write_text("1 + =", encoding="utf-8")
        with self.assertRaises(PluginLoadError):
            PluginLoader.load_entry_point(d, "plugin.py")


class TestPermissions(unittest.TestCase):
    def test_grant_and_check(self):
        p = PluginPermissions()
        p.grant("filesystem")
        assert p.has("filesystem")
        assert not p.has("network")

    def test_revoke(self):
        p = PluginPermissions()
        p.grant("filesystem")
        p.revoke("filesystem")
        assert not p.has("filesystem")

    def test_check_raises(self):
        p = PluginPermissions()
        with self.assertRaises(PermissionError):
            p.check("network")

    def test_invalid_permission(self):
        p = PluginPermissions()
        with self.assertRaises(ValueError):
            p.grant("does_not_exist")

    def test_all_permissions_set(self):
        p = PluginPermissions(set(ALL_PERMISSIONS))
        for perm in ALL_PERMISSIONS:
            assert p.has(perm)


class TestSandbox(unittest.TestCase):
    def test_safe_call_success(self):
        ctx = PluginContext(plugin_id="p1", manifest=PluginManifest(name="A", version="1.0", author="a"), install_path=".")
        ctx.instance = SimpleNamespace(hello=lambda name: f"hi {name}")
        sb = PluginSandbox(ctx)
        assert sb.safe_call("hello", "world") == "hi world"

    def test_safe_call_missing_method(self):
        ctx = PluginContext(plugin_id="p1", manifest=PluginManifest(name="A", version="1.0", author="a"), install_path=".")
        ctx.instance = SimpleNamespace()
        sb = PluginSandbox(ctx)
        with self.assertRaises(AttributeError):
            sb.safe_call("missing")

    def test_safe_call_unloaded(self):
        ctx = PluginContext(plugin_id="p1", manifest=PluginManifest(name="A", version="1.0", author="a"), install_path=".")
        sb = PluginSandbox(ctx)
        with self.assertRaises(RuntimeError):
            sb.safe_call("anything")


class TestEvents(unittest.TestCase):
    def test_publish_and_subscribe(self):
        events = PluginEvents()
        received = []

        def handler(event):
            received.append(event)

        events.subscribe("startup", handler)
        event = PluginEvent(event_type="startup", data={"x": 1})
        events.publish(event)
        assert len(received) == 1
        assert received[0].data["x"] == 1

    def test_unsubscribe(self):
        events = PluginEvents()
        received = []

        def handler(event):
            received.append(event)

        events.subscribe("startup", handler)
        events.unsubscribe("startup", handler)
        events.publish(PluginEvent(event_type="startup"))
        assert len(received) == 0

    def test_multiple_handlers(self):
        events = PluginEvents()
        a, b = [], []

        def h1(event): a.append(event)
        def h2(event): b.append(event)

        events.subscribe("msg", h1)
        events.subscribe("msg", h2)
        events.publish(PluginEvent(event_type="msg"))
        assert len(a) == 1 and len(b) == 1

    def test_failing_handler(self):
        events = PluginEvents()
        received = []

        def bad(event):
            raise RuntimeError("boom")

        def good(event):
            received.append(event)

        events.subscribe("msg", bad)
        events.subscribe("msg", good)
        events.publish(PluginEvent(event_type="msg"))
        assert len(received) == 1


class TestAPI(unittest.TestCase):
    def test_emit_with_events(self):
        events = PluginEvents()
        received = []

        def handler(event):
            received.append(event)

        events.subscribe("test", handler)
        api = PluginAPI(events=events)
        api.emit("test", {"v": 1})
        assert len(received) == 1
        assert received[0].data["v"] == 1

    def test_emit_without_events(self):
        api = PluginAPI()
        api.emit("test")  # should not raise


class TestPluginManager(unittest.TestCase):
    def test_discover(self):
        mgr = PluginManager(plugins_dir=str(REPO / "plugins"))
        discovered = mgr.discover()
        names = {c.manifest.name for c in discovered}
        assert "calculator_plus" in names
        assert "git_helper" in names
        assert "system_monitor" in names

    def test_install_load_enable_disable_reload_uninstall(self):
        mgr = PluginManager(plugins_dir=str(REPO / "plugins"))
        d = tmp_plugin_dir("lifecycle_plugin")
        (d / "manifest.json").write_text('{"name":"lifecycle","version":"1.0","author":"t"}', encoding="utf-8")
        (d / "plugin.py").write_text('name="lifecycle"\nversion="1.0"\n', encoding="utf-8")

        ctx = mgr.install(d)
        assert ctx.plugin_id == "lifecycle_plugin"
        assert ctx.enabled is False

        loaded = mgr.load(ctx.plugin_id)
        assert loaded.loaded is True
        assert loaded.enabled is False

        mgr.enable(ctx.plugin_id)
        assert mgr.registry.get(ctx.plugin_id).enabled is True

        mgr.disable(ctx.plugin_id)
        assert mgr.registry.get(ctx.plugin_id).enabled is False

        reloaded = mgr.reload(ctx.plugin_id)
        assert reloaded.loaded is True

        mgr.uninstall(ctx.plugin_id)
        assert mgr.registry.get(ctx.plugin_id) is None

    def test_sandbox_permission_check(self):
        mgr = PluginManager(plugins_dir=str(REPO / "plugins"))
        ctx = mgr.install(tmp_plugin_dir("perm_plugin"))
        mgr.load(ctx.plugin_id)
        sb = mgr.sandbox(ctx.plugin_id)
        with self.assertRaises(PermissionError):
            sb.enforce("memory")

    def test_list_plugins(self):
        mgr = PluginManager(plugins_dir=str(REPO / "plugins"))
        mgr.discover()
        plugins = mgr.list_plugins()
        assert len(plugins) >= 3
        for p in plugins:
            assert "plugin_id" in p
            assert "name" in p
            assert "version" in p
            assert "enabled" in p

    def test_unload_after_load(self):
        mgr = PluginManager(plugins_dir=str(REPO / "plugins"))
        ctx = mgr.install(tmp_plugin_dir("unload_plugin"))
        mgr.load(ctx.plugin_id)
        mgr.unload(ctx.plugin_id)
        assert mgr.registry.get(ctx.plugin_id).loaded is False

    def test_update_reloads(self):
        mgr = PluginManager(plugins_dir=str(REPO / "plugins"))
        ctx = mgr.install(tmp_plugin_dir("update_plugin"))
        mgr.load(ctx.plugin_id)
        updated = mgr.update(ctx.plugin_id)
        assert updated.loaded is True


if __name__ == "__main__":
    unittest.main()
