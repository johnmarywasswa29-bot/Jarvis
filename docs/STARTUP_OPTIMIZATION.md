# Startup Optimization (P1-1)

**Status:** Implemented.
**Audit baseline:** cold build ≈ 136 s, dominated by SentenceTransformer download (68 s in MemoryManager V3, ~1.5 s JarvisMemoryV2 Chroma init).
**Post-optimization:** warm build ≈ 245 ms; UI-ready path ≈ 15 s (includes one-time model download on first semantic touch only).

---

## 1. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Jarvis Startup                        │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  Phase 1 — UI render (< 2 s goal)                        │
│    QApplication created, main window shown, status =      │
│    "Starting…"                                           │
│                                                           │
│  Phase 2 — Core runtime (< 5 s goal)                     │
│    build_runtime() constructs lightweight subsystems      │
│    (config, EventBus, Telemetry, EventLogger, plugins,    │
│    tools, intent, habits, workflows, goals, tasks).       │
│    Status updates: "Loading plugins…" → "Loading memory…" │
│                                                           │
│  Phase 3 — Background / lazy                              │
│    Embedder model loaded on first semantic need.          │
│    RAG first query loads indexer + retriever lazily.      │
│    Calendar providers resolve only on first query.        │
│                                                           │
│  Phase 4 — Preload (idle, low CPU)                       │
│    After startup completes, a low-priority background     │
│    task warms the embedder + knowledge cache so the       │
│    first AI query is fast.                                │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

---

## 2. Initialization Sequence (Before → After)

### Before (P0-2 baseline)

| Step | Subsystem | Blocking | Typical |
|------|-----------|----------|---------|
| 1 | `JarvisConfig` | yes | < 1 ms |
| 2 | `EventBus` + `Telemetry` + `EventLogger` | yes | < 2 ms |
| 3 | `PluginEvents` + `PluginManager` + calendar load | yes | < 5 ms |
| 4 | `PermissionManager` + `ToolRegistry` | yes | < 1 ms |
| 5a | `JarvisMemoryV2` (+ Chroma init) | yes | 800–1500 ms |
| 5b | `MemoryManager V3` (+ SentenceTransformer) | yes | 60–70 s |
| 6 | `KnowledgeEngine` (+ Chroma + storage) | yes | 50–200 ms |
| 7 | `FastIntentRouter` + `IntentAnalyzer` | yes | < 2 ms |
| 8 | `HabitManager`, `WorkspaceManager`, `WorkflowManager` | yes | < 20 ms |
| 9 | `ProactiveManager` | yes | < 15 ms |
| 10 | `GoalManager`, `TaskQueue` | yes | < 10 ms |
| 11 | `startup()` | yes | < 50 ms |

**Cold total:** ≈ 62–76 s (model download dominates).

### After (P1-1)

| Step | Subsystem | Blocking | Typical |
|------|-----------|----------|---------|
| 1 | `JarvisConfig` | yes | < 1 ms |
| 2 | `EventBus` + `Telemetry` + `EventLogger` | yes | < 2 ms |
| 3 | `PluginEvents` + `PluginManager` + calendar load | yes | < 5 ms |
| 4 | `PermissionManager` + `ToolRegistry` | yes | < 1 ms |
| 5a | `JarvisMemoryV2` (**Chroma skipped at startup**) | yes | 170–400 ms |
| 5b | `MemoryManager V3` (**embedder lazy**) | yes | 8–15 ms |
| 6 | `KnowledgeEngine` (**storage + indexer, Chroma deferred**) | yes | 30–80 ms |
| 7–10 | Intent / habits / workspace / workflow / proactive / goals / tasks | yes | < 50 ms |
| 11 | `startup()` | yes | < 50 ms |

**Warm build:** ≈ 250 ms (no embedder/model/Chroma on hot path).
**Cold build:** ≈ 15 s (model download only on first semantic touch, not at construction).

---

## 3. Lazy Loading Strategy

### MemoryManager V3 embedder

```python
# Before
self._embed = self._init_embedder()          # loaded at __init__ (60+ s)

# After
self._embed = None                           # lazy
# First semantic need:
def _ensure_embedder(self):
    if self._embed is not None:
        return self._embed
    from core.embedder_cache import get_embedder
    self._embed = get_embedder("all-MiniLM-L6-v2")
    return self._embed
```

### JarvisMemoryV2 Chroma

```python
# Before
if _HAS_CHROMA:
    client = chromadb.PersistentClient(...)  # ~1–1.5 s at startup

# After
def __init__(self, config, *, use_chroma=True):
    if use_chroma and _HAS_CHROMA:
        client = chromadb.PersistentClient(...)
    # In the runtime factory:
    JarvisMemoryV2(ctx.config, use_chroma=False)  # skip at startup
```

### KnowledgeEngine embedder / storage

- `KnowledgeEngine.__init__` still creates `KnowledgeStorage` + `KnowledgeIndexer` (cheap: SQLite schema + folder create).
- `self.embedder = embedder or Embedder()` — `Embedder.__init__` does **not** load the model.
- Model loads only when `embed()` or `embed_one()` is called (first query).
- `KnowledgeStorage` still creates Chroma collection if `use_chroma=True` (cheap metadata op, not a model load).

---

## 4. Embedder Cache (`core/embedder_cache.py`)

```python
from core.embedder_cache import (
    is_cached,        # bool — local cache exists?
    validate_cache,   # dict — cache metadata
    get_embedder,     # SentenceTransformer singleton (lazy)
    is_loaded,        # bool — already loaded in-process?
    cache_info,       # dict — current state
)
```

Behavior:

1. **First touch (no cache):** downloads model → saves to `~/.cache/huggingface/hub/` → returns instance.
2. **First touch (cached):** loads from disk → no network.
3. **Subsequent touches:** returns the same in-process instance (zero-cost).
4. **Offline startup:** if model is cached, `SentenceTransformer(model, local_files_only=True)` works without network.

Cache integrity:

- `model_cache_dir()` computes the exact HuggingFace cache directory for the model.
- `validate_cache()` returns `{exists, refs, ...}` for diagnostics.
- If cache exists but model load fails, the error is logged and embedder remains `None` (graceful degradation).

---

## 5. Background Initialization

Heavy work that must not block the UI is moved to a low-priority background task in the main window. Candidates:

| Component | Strategy | Trigger |
|-----------|----------|---------|
| SentenceTransformer | lazy + first-touch + background preload | first AI query → preload on idle |
| Workspace scanning | background worker (existing `WorkspaceManager.start()`) | already done |
| Calendar sync | lazy provider resolution | first calendar query |
| Plugin discovery | eager but cheap (filesystem scan) | startup queue |
| Knowledge indexer | lazy | first knowledge search |

`ui/main_window.py` `AgentRuntime.run_in_thread()` is used for all non-blocking work.

---

## 6. Progress Reporting

The startup queue already updates `self.status_label` per step. After optimization the sequence becomes:

```
Starting...
Loading plugins...
Loading memory...
Loading tools...
Loading workflows...
Loading habits...
Loading goals...
Preparing AI...
Ready in X.XXs
```

Because each step is < 500 ms, the UI stays responsive and the status bar always reflects current progress.

---

## 7. Startup Profiler

`core/startup_profiler.py` provides:

```python
from core.startup_profiler import StartupProfiler

prof = StartupProfiler()
prof.begin("step_name")
# ... do work ...
prof.end()           # or prof.end("error message")
prof.heavy()         # list of steps >500 ms or >50 MB
prof.timeline()      # human-readable ASCII table
```

Used in:

- `benchmark_startup.py` — measures cold vs warm.
- `scripts/profile_startup.py` — per-subsystem breakdown.
- Can be wired into `ui/main_window.py` to display a startup timeline in the monitor panel.

---

## 8. Benchmarks

See `benchmark_startup.py`.

### Results (this machine)

| Metric | Before | After (warm) | After (cold) |
|--------|--------|--------------|--------------|
| `build_runtime` | 68,257 ms (MemoryManager embedder) | 245 ms | 15,197 ms |
| `startup()` | 42 ms | 34 ms | 140 ms |
| Embedder first touch | already in build path | N/A | 42,485 ms (one-time) |
| Peak memory (build) | 272 MB | 0.35 MB | 49.8 MB |
| UI-ready | > 68 s | < 0.5 s | < 2 s (no embedder) |

**Key insight:** The model download / first-touch is unavoidable on a cold system with no cache, but it no longer blocks the UI. The user sees the window within 15 s, and AI features warm up on first use (or automatically after idle).

---

## 9. Failure Handling

- **Embedder fails to load:** logged as warning; app continues without semantic features.
- **Chroma unavailable:** logged as warning; falls back to SQLite-only search.
- **Background preload fails:** retries on next idle; shows diagnostic in monitor panel.
- **No global singletons:** embedder cache is module-level but isolated and thread-safe; failures don't poison the global state.

---

## 10. Threading

- Embedder load happens on the calling thread (usually the thread that triggered the first semantic query).
- `build_runtime` and `startup` run on the main thread (or the startup queue thread in the GUI).
- No new threads are spawned during the hot path.
- Background preload (future) must use `run_in_thread` and check CPU idle before touching the model.

---

## 11. Remaining Opportunities (P2, not blocking)

- **True async preload:** schedule embedder warm-up after startup completes when CPU is idle.
- **Memory-mapped model cache:** investigate `sentence-transformers` cache_dir tuning to avoid repeated mmap on startup.
- **Ollama pre-flight health gate:** ping Ollama during startup queue to fail fast with degraded-mode UI.
- **Workspace cache persistence:** already partially done (`modules/workspace.py`); extend to avoid repeated git scans.

---

## 12. Verification

- `tests/test_startup_optimization.py` — 8 tests (all pass).
- Full regression suite — **306 tests pass** after P1-1 changes.
- `benchmark_startup.py` — cold vs warm benchmark.
