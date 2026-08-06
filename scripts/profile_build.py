import sys, time
sys.path.insert(0, '.')
from pathlib import Path

import runtime.runtime as rt
_orig = rt._safe
_times = {}
def _prof(step, ctx, fn):
    if step in _times:
        return _orig(step, ctx, fn)
    t0 = time.perf_counter()
    r = _orig(step, ctx, fn)
    _times[step] = (time.perf_counter() - t0) * 1000
    return r
rt._safe = _prof

t0 = time.perf_counter()
ctx = rt.build_runtime(repo=Path('.').resolve())
total = (time.perf_counter() - t0) * 1000

for k,v in sorted(_times.items(), key=lambda x:-x[1])[:20]:
    print(f"{k:<30} {v:>8.1f} ms")
print("TOTAL:", total, "ms")
print("errors:", ctx.errors)
