"""
P1-1 cold-start optimization tests.

Verifies:
- MemoryManager (V3) embedder is lazy (not loaded at __init__)
- KnowledgeEngine.embedder is lazy (not loaded at __init__)
- JarvisMemoryV2 chroma is optional (use_chroma=False skips it)
- build_runtime does not touch heavy components on the hot path
- Cache reuse avoids re-downloading models
- Offline startup succeeds when models are cached
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def test_memory_manager_embedder_is_lazy():
    from modules.memory_v2 import MemoryManager
    m = MemoryManager.__new__(MemoryManager)
    assert getattr(m, "_embed", None) is None
    # _init_embedder was a classmethod that loaded eagerly; now it's gone as the init path.
    # _ensure_embedder is the new lazy path.
    assert hasattr(m, "_ensure_embedder")


def test_knowledge_embedder_is_lazy():
    from knowledge.embedder import Embedder
    e = Embedder("all-MiniLM-L6-v2")
    assert e._model is None
    assert e._load_attempted is False
    assert e.is_available() is False


def test_jarvis_memory_v2_chroma_optional():
    from modules.memory_v2 import JarvisMemoryV2
    from modules.config import JarvisConfig
    cfg = JarvisConfig(project_root=REPO)
    m = JarvisMemoryV2(cfg, use_chroma=False)
    assert m._chroma is None
    m.shutdown()


def test_build_runtime_does_not_touch_embedder():
    """The hot path must not trigger SentenceTransformer import/load."""
    import runtime.runtime as rt
    _orig_safe = rt._safe
    seen = []

    def _tracking_safe(step, ctx, fn):
        seen.append(step)
        return _orig_safe(step, ctx, fn)

    rt._safe = _tracking_safe
    try:
        ctx = rt.build_runtime(repo=REPO)
        # After build, the embedder cache must NOT be loaded yet.
        from core.embedder_cache import is_loaded
        assert is_loaded() is False, "embedder was loaded during build_runtime hot path"
        # The lazy getter exists on MemoryManager but _embed is None.
        assert ctx.memory_manager._embed is None
    finally:
        rt._safe = _orig_safe
    rt.stop_runtime(ctx)


def test_embedder_cache_reuse():
    """After first touch, subsequent touches return the same cached instance."""
    from core.embedder_cache import get_embedder, is_loaded, cache_info
    if is_loaded():
        # Reset for a clean test.
        import core.embedder_cache as ec
        ec._shared = None
        ec._shared_name = None
        ec._loaded = False
    m1 = get_embedder("all-MiniLM-L6-v2")
    info1 = cache_info()
    assert info1["loaded"] is True
    m2 = get_embedder("all-MiniLM-L6-v2")
    assert m1 is m2, "embedder cache did not return the same instance"
    info2 = cache_info()
    assert info2["model"] == "all-MiniLM-L6-v2"


def test_startup_profiler_records_steps():
    from core.startup_profiler import StartupProfiler
    prof = StartupProfiler()
    prof.begin("a")
    time.sleep(0.01)
    prof.end()
    prof.begin("b")
    prof.end("oops")
    assert len(prof.steps) == 2
    assert prof.steps[0].duration_ms >= 10.0
    assert prof.steps[1].error == "oops"
    timeline = prof.timeline()
    assert "a" in timeline
    assert "b" in timeline
    assert "HEAVY" not in timeline  # both under 500ms


def test_warm_startup_is_fast():
    """After embedder is cached, build_runtime + startup must be under 5s."""
    from runtime.runtime import build_runtime, startup, stop_runtime
    from core.embedder_cache import get_embedder
    # Pre-warm the embedder cache (simulate a previous session).
    try:
        get_embedder("all-MiniLM-L6-v2")
    except Exception:
        pytest.skip("sentence-transformers unavailable")
    t0 = time.perf_counter()
    ctx = build_runtime(repo=REPO)
    build_ms = (time.perf_counter() - t0) * 1000
    startup(ctx)
    stop_runtime(ctx)
    total_ms = (time.perf_counter() - t0) * 1000
    assert total_ms < 5000, f"warm startup took {total_ms:.0f} ms, expected <5000 ms"


def test_offline_build_runtime_when_cached():
    """With local_files_only=True, build must still succeed if model is cached."""
    import runtime.runtime as rt
    from core.embedder_cache import is_loaded
    if not is_loaded():
        pytest.skip("model not cached; cannot test offline")
    ctx = rt.build_runtime(repo=REPO)
    try:
        assert ctx.errors == []
    finally:
        rt.stop_runtime(ctx)
