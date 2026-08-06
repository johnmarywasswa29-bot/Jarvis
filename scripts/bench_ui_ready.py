"""Quick UI-ready benchmark (no embedder first touch)."""
import sys, time, os, tracemalloc
sys.path.insert(0, '.')
from pathlib import Path
REPO = Path('.').resolve()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from runtime.runtime import build_runtime, startup, stop_runtime  # noqa: E402

t0 = time.perf_counter()
tracemalloc.start()
ctx = build_runtime(repo=REPO)
build_ms = (time.perf_counter() - t0) * 1000
_, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()

startup(ctx)

ui_ready_ms = build_ms  # no embedder touch here

errors = len(ctx.errors)
print(f"UI-ready build_ms: {build_ms:.1f}")
print(f"startup_ms: {(time.perf_counter() - t0) * 1000 - build_ms:.1f}")
print(f"peak_mb: {peak/1024/1024:.2f}")
print(f"errors: {errors}")
print(f"managers: {sum(1 for v in ctx.all_managers().values() if v is not None)}/19")

stop_runtime(ctx)
print("DONE")
