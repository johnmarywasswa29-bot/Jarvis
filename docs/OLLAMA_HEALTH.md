# Ollama Health Management (P1-2)

**Status:** Implemented.
**Goal:** Jarvis must always know whether the local LLM is ready, slow, offline, or missing — and keep working regardless.

---

## 1. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Jarvis Process                          │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  Runtime                                                │
│  ├── build_runtime()                                     │
│  │    └── OllamaHealth(config.llm_base_url, config.llm_model) │
│  └── startup()                                           │
│       └── ollama_health.start()  ← background monitor     │
│                                                           │
│  OllamaHealth                                            │
│  ├── Health states: READY / LOADING / BUSY / SLOW /      │
│  │    VERY_SLOW / OFFLINE / UNREACHABLE / MODEL_MISSING / │
│  │    ERROR / DEGRADED                                   │
│  ├── refresh()          ← synchronous full/lightweight check│
│  ├── start()/stop()     ← background monitor loop         │
│  ├── Diagnostics        ← detailed JSON report            │
│  └── attempt_recovery() ← auto reconnect                  │
│                                                           │
│  Brain / LLM layers                                      │
│  ├── JarvisBrain          ← accepts ollama_health kwarg   │
│  └── OllamaLLM            ← accepts ollama_health kwarg   │
│                                                           │
│  UI                                                      │
│  └── _do_ollama_check()    ← reads ollama_health.refresh()│
│                                                           │
└─────────────────────────────────────────────────────────┘
```

---

## 2. State Machine

```
             ┌─────────┐
             │ OFFLINE │
             └────┬────┘
                  │ server up
                  ▼
             ┌─────────┐
   ┌─────────│ UNRCH   │◄──────────────┐
   │         └────┬────┘               │
   │              │ API ok              │ reconnect
   │              ▼                    │
   │         ┌──────────┐              │
   │         │ MODEL_   │◄──── model missing
   │         │ MISSING  │
   │         └────┬────┘
   │              │ model found
   │              ▼
   │         ┌──────────┐
   │         │ READY    │◄────────────┐
   │         └────┬────┘             │
   │              │ slow              │ timeout / error
   │              ▼                   │
   │         ┌──────────┐            │
   │         │ SLOW     │            │
   │         └────┬────┘            │
   │              │ very slow       │
   │              ▼                 │
   │         ┌──────────┐          │
   │         │ VERY_SLOW│          │
   │         └────┬────┘          │
   │              │ load / busy   │
   │              ▼               │
   │         ┌──────────┐        │
   │         │ LOADING  │        │
   │         └────┬────┘        │
   │              │ busy         │
   │              ▼              │
   │         ┌──────────┐       │
   │         │ BUSY     │───────┘
   │         └────┬────┘
   │              │ recoverable failure
   │              ▼
   │         ┌──────────┐
   │         │ DEGRADED │
   │         └──────────┘
   └─────────────────────────────┘
```

`is_available()` = everything except OFFLINE, UNREACHABLE, MODEL_MISSING, ERROR.
`is_degraded()` = exactly DEGRADED.

---

## 3. Health Checks

| Check | Endpoint | Timeout | Fallback |
|-------|----------|---------|----------|
| Server reachable | `GET /` | `ping_timeout_s` | mark OFFLINE |
| API responding | `GET /api/tags` | `ping_timeout_s` | mark UNREACHABLE |
| Model installed | `/api/tags` body | — | mark MODEL_MISSING |
| Inference probe | `POST /api/generate` with `num_predict=1` | `inference_timeout_s` | use ping latency only |

All checks run inside `_safe`-style exception handling — failures never crash startup.

---

## 4. Model Discovery API

```python
health.list_models()          # list[str]
health.current_model()        # str
health.model_exists(name)     # bool
health.model_size(name)       # Optional[str]
health.recommended_models()   # list[str] — prefers llama3/mistral/gemma
```

On startup, `OllamaHealth.refresh()` automatically validates the configured model. If missing:
- state = `MODEL_MISSING`
- diagnostics include installed alternatives
- degraded mode can be enabled via config

---

## 5. Degraded Mode

When Ollama is unavailable:

| Subsystem | Behavior |
|-----------|----------|
| Desktop tools | full |
| Plugin SDK | full |
| Calendar | full |
| Memory | full |
| Workspace | full |
| Workflow engine | full |
| LLM chat | fallback response with status |

Degraded mode is enabled by default (`ollama_degraded_mode: true`). The brain returns a status-aware fallback string instead of crashing.

---

## 6. Recovery

- `attempt_recovery()` performs a fresh inference probe after 0.5 s delay.
- Background monitor runs every `ollama_health_interval_s` (default 30 s).
- If auto-reconnect is on, state transitions back to READY/SLOW automatically when Ollama returns.
- No Jarvis restart required.

---

## 7. UI Integration

`ui/main_window.py` `_do_ollama_check()` now reads from `ollama_health.refresh()` instead of direct HTTP.

Status indicator states:
- `ready` → green
- `loading` → green
- `busy` / `slow` / `very_slow` → yellow
- `offline` / `unreachable` / `model_missing` / `error` / `degraded` → red

Timer interval: 1500 ms (existing).

---

## 8. Configuration

```yaml
llm:
  provider: ollama
  model: llama3
  base_url: http://localhost:11434
  timeout_s: 12

ollama:
  health_interval_s: 30
  warning_latency_s: 8
  critical_latency_s: 20
  auto_reconnect: true
  degraded_mode: true
```

---

## 9. Benchmarks

See `benchmark_ollama_health.py`.

### Results (offline)

| Metric | Value |
|--------|-------|
| health check offline | 327 ms |
| startup validation | 211 ms |
| model discovery | 214 ms |
| recommended models | 210 ms |
| offline detection | 202 ms |
| recovery attempt | 709 ms |
| 10 checks (monitor) | 2081 ms (208 ms avg) |
| diagnostics | 18 µs |

Thresholds: health check < 500 ms, 10 checks < 2000 ms. ✅

---

## 10. Tests

- `tests/test_ollama_health.py` — 24 tests
- `tests/test_ollama_runtime.py` — 5 tests
- Focused regression: 114 passed, 0 failed
