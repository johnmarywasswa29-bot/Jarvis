"""
Runtime integration benchmark (P0-1 / P0-2 verification).

Measures: build_runtime() latency, dependency-injection cost, plugin
initialization, and graceful shutdown latency. Prints a table and a summary line.

Run:  python benchmark_runtime.py
"""
from __future__ import annotations

import sys
import time
import tracemalloc
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from runtime.runtime import build_runtime, startup, stop_runtime  # noqa: E402


def main() -> None:
    print("=" * 60)
    print("Jarvis Runtime Integration Benchmark")
    print("=" * 60)

    # Cold build
    t0 = time.perf_counter()
    tracemalloc.start()
    ctx = build_runtime(repo=REPO)
    build_s = time.perf_counter() - t0
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    managers = ctx.all_managers()
    present = [k for k, v in managers.items() if v is not None]
    missing = [k for k, v in managers.items() if v is None]
    print(f"{'Build runtime':<32} {build_s*1000:>10.1f} ms")
    print(f"{'Managers present':<32} {len(present)}/{len(managers)}")
    print(f"{'Build errors':<32} {len(ctx.errors)}")
    print(f"{'Peak RSS (tracemalloc)':<32} {peak/1024/1024:>10.2f} MB")

    # Dependency injection cost: how long did pure DI take vs model loads?
    # (Approximate by re-measuring a no-op ctx attribute access fan-out.)
    t0 = time.perf_counter()
    _ = [getattr(ctx, m) for m in managers]
    di_s = time.perf_counter() - t0
    print(f"{'Dep-injection access fan-out':<32} {di_s*1000:>10.3f} ms")

    # Plugin initialization (already loaded during build; measure calendar op)
    if ctx.calendar_plugin is not None:
        t0 = time.perf_counter()
        _ = ctx.calendar_plugin.reminders()
        plugin_s = time.perf_counter() - t0
        print(f"{'Calendar plugin op (reminders)':<32} {plugin_s*1000:>10.3f} ms")

    # Startup (workspace + proactive + APP_STARTED publish)
    t0 = time.perf_counter()
    startup(ctx)
    start_s = time.perf_counter() - t0
    print(f"{'startup()':<32} {start_s*1000:>10.2f} ms")

    # EventBus throughput sanity
    from core.events import Event, EventType
    t0 = time.perf_counter()
    n = 1000
    for _ in range(n):
        ctx.event_bus.publish(Event(event_type=EventType.CUSTOM, source="bench", payload={}))
    evt_s = (time.perf_counter() - t0) / n * 1_000_000
    print(f"{'EventBus publish/event':<32} {evt_s:>10.2f} us")

    # Shutdown
    t0 = time.perf_counter()
    stop_runtime(ctx)
    stop_s = time.perf_counter() - t0
    print(f"{'shutdown()':<32} {stop_s*1000:>10.2f} ms")

    print("-" * 60)
    print(f"BUILD_OK={'True' if not ctx.errors and not missing else 'False'} "
          f"managers={len(present)} build_ms={build_s*1000:.0f} "
          f"startup_ms={start_s*1000:.0f} shutdown_ms={stop_s*1000:.0f}")
    print("=" * 60)


if __name__ == "__main__":
    main()
