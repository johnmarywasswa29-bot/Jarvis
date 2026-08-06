"""Profile build_runtime exactly as the app calls it."""
import sys, time, contextlib
sys.path.insert(0, '.')
from pathlib import Path
REPO = Path('.').resolve()

_orig = None
_steps = []

def _instrument(step_name):
    def deco(fn):
        def wrapper(*a, **kw):
            t0 = time.perf_counter()
            try:
                return fn(*a, **kw)
            finally:
                dt = (time.perf_counter() - t0) * 1000
                _steps.append((step_name, dt))
        return wrapper
    return deco

import runtime.runtime as rt
# Patch the internal constructors by wrapping build_runtime's imports
# Simpler: monkey-patch _safe to record timings.
_orig_safe = rt._safe
_prof_steps = {}

def _profiled_safe(step, ctx, fn):
    if step in _prof_steps:
        # Avoid double-counting in a single step
        return _orig_safe(step, ctx, fn)
    t0 = time.perf_counter()
    result = _orig_safe(step, ctx, fn)
    _prof_steps[step] = (time.perf_counter() - t0) * 1000
    return result

rt._safe = _profiled_safe

t0 = time.perf_counter()
ctx = rt.build_runtime(repo=REPO)
total = (time.perf_counter() - t0) * 1000

print("=" * 60)
print(f"{'Step':<32} {'ms':>8}")
print("-" * 60)
heavy = []
for step, dt in sorted(_prof_steps.items(), key=lambda x: -x[1]):
    marker = " <<< HEAVY" if dt > 500 else ""
    if marker:
        heavy.append((step, dt))
    print(f"{step:<32} {dt:>8.1f}{marker}")
print("=" * 60)
print(f"TOTAL eager build = {total:.0f} ms")
print(f"heavy contributors (>500ms): {len(heavy)}")
print(f"errors: {len(ctx.errors)}")
if ctx.errors:
    for e in ctx.errors[:5]:
        print("  err:", e)
