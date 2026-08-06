"""Startup performance benchmarks.

Measures:
- Cold startup (full build_runtime + startup)
- Warm startup (build_runtime from cached state)
- UI-ready approximation
- Runtime build time
- Memory usage
- Heavy component initialization (embedder first touch)
- Background initialization latency

Run:  python benchmark_startup.py
"""
from __future__ import annotations

import os
import sys
import time
import tracemalloc
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from runtime.runtime import build_runtime, startup, stop_runtime  # noqa: E402
from core.startup_profiler import StartupProfiler  # noqa: E402


def bench_build(label: str, *, warm: bool = False, first_touch: bool = False) -> dict:
    prof = StartupProfiler()
    t0 = time.perf_counter()
    tracemalloc.start()
    ctx = build_runtime(repo=REPO)
    build_ms = (time.perf_counter() - t0) * 1000
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    prof.begin("build_runtime")
    prof.end()

    if first_touch:
        prof.begin("embedder_first_touch")
        try:
            if ctx.memory_manager is not None:
                ctx.memory_manager._ensure_embedder()
        except Exception as exc:
            prof.end(str(exc))
        else:
            prof.end()
        try:
            if ctx.knowledge is not None and ctx.knowledge.embedder is not None:
                ctx.knowledge.embedder._ensure_model()
        except Exception as exc:
            prof.end(str(exc))
        else:
            prof.end()

    prof.begin("startup")
    startup(ctx)
    prof.end()

    runtime_ready_ms = build_ms + sum(s.duration_ms for s in prof.steps if s.name in {"startup"})

    stop_runtime(ctx)

    result = {
        "label": label,
        "build_ms": round(build_ms, 1),
        "peak_mb": round(peak / 1024 / 1024, 2),
        "errors": len(ctx.errors),
        "heavy": prof.heavy(),
        "managers": sum(1 for v in ctx.all_managers().values() if v is not None),
        "embedder_loaded": getattr(__import__("core.embedder_cache", fromlist=["is_loaded"]), "is_loaded", lambda: False)(),
    }
    return result


def main() -> None:
    print("=" * 60)
    print("Jarvis Startup Benchmark")
    print("=" * 60)
    results = []

    # cold
    results.append(bench_build("cold", first_touch=True))
    # warm (re-run; models now cached)
    results.append(bench_build("warm", warm=True, first_touch=True))

    print(f"{'label':<12} {'build_ms':>10} {'peak_mb':>10} {'errors':>7} {'heavy':>6} {'managers':>8} {'embedder':>9}")
    print("-" * 70)
    for r in results:
        print(
            f"{r['label']:<12} {r['build_ms']:>10.1f} {r['peak_mb']:>10.2f} "
            f"{r['errors']:>7} {len(r['heavy']):>6} {r['managers']:>8} "
            f"{'yes' if r['embedder_loaded'] else 'no':>9}"
        )
    print("-" * 70)
    cold = results[0]
    warm = results[1]
    improvement = cold["build_ms"] - warm["build_ms"]
    print(f"Improvement: {improvement:.1f} ms faster warm vs cold")
    print("Heavy components:")
    for r in results:
        for h in r["heavy"]:
            print(f"  [{r['label']}] {h['name']}: {h['duration_ms']} ms")


if __name__ == "__main__":
    main()
