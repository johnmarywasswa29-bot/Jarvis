import os, sys, time, tracemalloc, threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[0]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from plugins.sdk.manager import PluginManager
from plugins.sdk.registry import PluginRegistry
from plugins.sdk.events import PluginEvents
from plugins.sdk.api import PluginAPI
from plugins.sdk.permissions import PluginPermissions

PLUGINS_DIR = REPO / "plugins"
mgr = PluginManager(plugins_dir=str(PLUGINS_DIR))

# Discover
t0 = time.perf_counter()
discovered = mgr.discover()
discover_latency = time.perf_counter() - t0
print(f"discover_latency: {discover_latency*1000:.2f} ms")

# Install lifecycle latency
t0 = time.perf_counter()
ctx = mgr.install(Path(PLUGINS_DIR / "calculator_plus"))
install_latency = time.perf_counter() - t0
print(f"install_latency: {install_latency*1000:.2f} ms")

t0 = time.perf_counter()
mgr.load(ctx.plugin_id)
load_latency = time.perf_counter() - t0
print(f"load_latency: {load_latency*1000:.2f} ms")

t0 = time.perf_counter()
mgr.enable(ctx.plugin_id)
enable_latency = time.perf_counter() - t0
print(f"enable_latency: {enable_latency*1000:.2f} ms")

# Event latency
events = PluginEvents()
received = []
def handler(event): received.append(event)
events.subscribe("test", handler)
t0 = time.perf_counter()
for i in range(1000):
    events.publish(__import__("plugins.sdk.state", fromlist=["PluginEvent"]).PluginEvent(event_type="test", data={"i": i}))
event_latency = (time.perf_counter() - t0) / 1000
print(f"event_latency_avg_us: {event_latency*1_000_000:.2f}")

# API latency
api = PluginAPI(events=events)
t0 = time.perf_counter()
for i in range(1000):
    api.emit("test", {"i": i})
api_latency = (time.perf_counter() - t0) / 1000
print(f"api_latency_avg_us: {api_latency*1_000_000:.2f}")

# Memory
tracemalloc.start()
mem_before = tracemalloc.get_traced_memory()[0] / 1024 / 1024
for i in range(100):
    reg = PluginRegistry()
    reg.register(__import__("plugins.sdk.state", fromlist=["PluginContext"]).PluginContext(
        plugin_id=f"r{i}",
        manifest=__import__("plugins.sdk.state", fromlist=["PluginManifest"]).PluginManifest(name=str(i), version="1.0", author=""),
        install_path=".",
    ))
mem_after = tracemalloc.get_traced_memory()[0] / 1024 / 1024
tracemalloc.stop()
print(f"registry_memory_delta_mb: {mem_after-mem_before:.3f}")

# Cleanup
mgr.disable(ctx.plugin_id)
mgr.unload(ctx.plugin_id)
mgr.uninstall(ctx.plugin_id)
print("BENCHMARK_OK")
