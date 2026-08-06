# Phase 8 — Plugin SDK

## Architecture
```
plugins/
  sdk/
    state.py           — PluginManifest, PluginContext, PluginEvent
    registry.py        — in-memory plugin registry
    loader.py          — manifest + entry-point loader
    permissions.py     — granular permission checks
    sandbox.py         — runtime guard
    events.py          — typed event bus
    api.py             — stable facade
    manager.py         — orchestrator
  calculator_plus/
    manifest.json
    plugin.py
  git_helper/
    manifest.json
    plugin.py
  system_monitor/
    manifest.json
    plugin.py
```

## Lifecycle
install -> load -> enable -> disable -> reload -> uninstall -> update

## Benchmarks
```
discover_latency:      5.19 ms
install_latency:       1.39 ms
load_latency:          9.57 ms
enable_latency:        0.03 ms
event_latency_avg_us: 24.76
api_latency_avg_us:   19.52
registry_memory_delta_mb: 0.001
```

## Verification
- Plugin SDK tests: **29/29 pass**
- Full regression: **279/279 pass**
