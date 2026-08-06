# Jarvis Runtime Architecture (`build_runtime`)

**Status:** Implemented as part of P0-1 / P0-2 (Jarvis 1.0 System Integration Audit).
**Module:** `runtime/runtime.py`
**Shared factory:** `build_runtime(config=None, repo=None) -> RuntimeContext`
**Lifecycle:** `startup(ctx)`, `shutdown(ctx)` (alias `stop_runtime`)

---

## 1. Purpose

Before this module existed, every implemented subsystem (Event Bus, Telemetry,
Logger, Plugin SDK + Calendar, Workflow Manager, Proactive Manager, Habit
Learning, Intent Confidence, Workspace Awareness, Memory V3, RAG, Goals/Tasks)
was built and *unit-tested in isolation* but was **never instantiated by the
running application**. The GUI entry point (`ui.main_window.AgentRuntime`) and the
CLI entry point (`jarvis.JarvisAssistant`) each constructed a partial, divergent
set of subsystems.

`build_runtime()` is now the **single authoritative construction path**. Every
subsystem is instantiated exactly once with explicit dependency injection and
returned in a `RuntimeContext`. Both `jarvis.py` and `ui/main_window.py` call it.

---

## 2. Dependency / Construction Order

Construction is sequential and order-dependent (later steps depend on earlier
ones). See `docs/runtime_dependency_graph.mmd` (Mermaid) and
`docs/runtime_dependency_graph.txt` (ASCII) for the full graph.

| Layer | Subsystem | Injected from |
|------|-----------|---------------|
| L0 | `JarvisConfig` | repo root |
| L1 | `EventBus`, `TelemetryManager`, `EventLogger` | config; logs → `repo/logs/events.jsonl` |
| L2 | `PluginEvents`, `PluginManager` | bridged into `EventBus` via `bridge_plugin_events` |
| L3 | `PermissionManager`, `ToolRegistry` | config + permissions |
| L4 | `MemoryManager` (V3), `JarvisMemoryV2` (chat), `KnowledgeEngine` (RAG) | config |
| L5 | `FastIntentRouter`, `IntentAnalyzer` | tool_registry; memory |
| L6 | `HabitManager` | config defaults |
| L7 | `WorkspaceManager` | config defaults |
| L8 | `WorkflowManager` | config defaults |
| L9 | `ProactiveManager` | memory, habits, rag, workflow, workspace, intent |
| L10 | `GoalManager`, `TaskQueue` | goal_manager → task_queue |
| L11 | `JarvisBrain` (LLM orchestrator) | config, tools, memory |
| L12 | `CalendarPlugin` | loaded+enabled via `PluginManager`; live instance bound to `PluginAPI` |

> Note: `JarvisBrain` (the LLM orchestrator) is intentionally **not** inside
> `RuntimeContext.managers` because it is the runtime's reasoning entry point,
> not a "manager" — but it is constructed from the shared context's tools/memory.

---

## 3. RuntimeContext

A plain dataclass holding every manager. No module-level singletons are used;
callers receive the context and pass managers explicitly (dependency injection).

```python
ctx = build_runtime(repo=REPO)
ctx.event_bus            # EventBus
ctx.telemetry            # TelemetryManager
ctx.event_logger         # EventLogger
ctx.plugin_manager       # PluginManager (Calendar plugin loaded+enabled)
ctx.calendar_plugin      # live CalendarPlugin instance
ctx.tool_registry        # ToolRegistry
ctx.memory_manager       # Memory V3
ctx.chat_memory          # JarvisMemoryV2
ctx.knowledge            # RAG service
ctx.intent_analyzer      # IntentAnalyzer
ctx.habit_manager        # HabitManager
ctx.workspace_manager    # WorkspaceManager
ctx.workflow_manager     # WorkflowManager
ctx.proactive_manager    # ProactiveManager
ctx.goal_manager         # GoalManager
ctx.task_queue           # TaskQueue
ctx.all_managers()       # dict of all of the above
ctx.errors               # list of construction errors (empty on success)
```

If a subsystem fails to construct, the error is captured in `ctx.errors` and the
build continues (resilience). A fully successful build leaves `ctx.errors == []`.

---

## 4. Observability Wiring

`build_runtime` subscribes a single observer to the `EventBus` for all relevant
`EventType` members. Every published event is:

1. **Recorded** by `TelemetryManager.record(source, duration_ms, success)`.
2. **Persisted** by `EventLogger` to `logs/events.jsonl`.

The Plugin SDK's legacy `PluginEvents` bus is bridged into the central
`EventBus` via `core.events.adapters.bridge_plugin_events`, so plugin lifecycle
events (loaded/enabled/disabled/error/installed/updated/uninstalled) appear in
telemetry and the event log automatically.

---

## 5. Initialization Sequence

```
build_runtime()
  ├─ construct config
  ├─ construct observability core (EventBus/Telemetry/Logger) + attach observer
  ├─ construct plugins (PluginEvents + PluginManager) + bridge
  ├─ construct tools/permissions
  ├─ construct memory (V3 + chat) + RAG
  ├─ construct intent (router + analyzer)
  ├─ construct habits / workspace / workflow
  ├─ construct proactive (depends on all of the above)
  ├─ construct goals + task queue
  ├─ construct brain (LLM orchestrator)
  └─ load + enable Calendar plugin (Plugin SDK) and create live instance
startup(ctx)
  ├─ workspace_manager.start()      # begins workspace polling
  ├─ proactive_manager.start()      # registers default triggers
  └─ publish EventType.APP_STARTED  # source="runtime"
```

In `ui.main_window`, the startup queue runs `_wire_panels()` (calls
`WorkflowPanel.set_manager(...)` and `WorkspacePanel.set_manager(...)`) and then
`_runtime_start()` (calls `startup(ctx)`) before showing the dashboard.

---

## 6. Shutdown Sequence

`shutdown(ctx)` (alias `stop_runtime`) tears down in dependency-reverse order:

```
shutdown(ctx)
  ├─ proactive_manager.stop()     (if present)
  ├─ workspace_manager.stop()     (stops watcher + closes history)
  ├─ workflow_manager.close()
  ├─ habit_manager.close()
  ├─ knowledge.close()            (RAG storage)
  ├─ chat_memory.shutdown()
  ├─ memory_manager.shutdown()
  ├─ plugin_manager: unload all loaded plugins
  ├─ unbridge plugin events from EventBus
  └─ publish EventType.APP_CLOSED
```

`ui/main_window.closeEvent` calls `stop_runtime(self.runtime._ctx)` first, then
performs UI-specific teardown (timers, workers, session). `jarvis.py` calls
`stop_runtime(self._ctx)` in `cleanup()`.

---

## 7. UI Wiring (P0-1)

| Panel | Manager | Wired via |
|-------|---------|-----------|
| `WorkflowPanel` | `RuntimeContext.workflow_manager` | `set_manager()` |
| `WorkspacePanel` | `RuntimeContext.workspace_manager` | `set_manager()` |

All other panels (chat, goals, tasks, knowledge, memory, activity, monitor,
settings) read from `AgentRuntime`'s mirrored attributes (`self.workflow_manager`,
etc.), which are copied from the shared context by `AgentRuntime._sync_from_ctx()`.

---

## 8. Single Construction Path (P0-2)

Both entry points import the **same** factory:

```python
# ui/main_window.py
from runtime.runtime import build_runtime, startup as rt_startup, stop_runtime as rt_stop
self._ctx = build_runtime(repo=repo)
self._sync_from_ctx()

# jarvis.py
from runtime.runtime import build_runtime, stop_runtime as rt_stop
self._ctx = build_runtime(config=self.config, repo=REPO)
```

There is exactly one `EventBus` and one `PluginManager` instance per process
(no duplication, no global singletons).

---

## 9. Verification

- `tests/test_app_wiring.py` (11 tests) asserts:
  - every required manager is built (`ctx.errors == []`),
  - no duplicate EventBus/PluginManager,
  - PluginManager discovers/loads/enables the calendar plugin,
  - calendar plugin is operational (reminders/free-time/conflicts),
  - EventBus → Telemetry records bridged plugin events,
  - workflow executes, proactive analyzes, workspace updates,
  - memory (V3) accepts writes, RAG engine is connected,
  - UI panels receive live managers via `set_manager`,
  - both entry points import the same `build_runtime`.
- Full regression suite (`pytest tests/`) must remain green.

---

## 10. Known Costs / Limitations

- **Embedding model load:** `MemoryManager` (V3) and `KnowledgeEngine` (RAG)
  load a `sentence-transformers` model on first use. On a cold start this adds
  ~15–25s to `build_runtime` (one-time). This is the dominant startup cost and
  is tracked as audit P1-1 (lazy/once-per-process model caching).
- **No UI panel exists yet** for Plugin Manager, Calendar, Proactive, or
  Telemetry viewers. The managers are fully instantiated and operable via the
  API and `test_app_wiring.py`; dedicated panels are a 1.1 follow-up.
