"""
Jarvis shared runtime factory.

This is the SINGLE authoritative construction path for the entire application
(P0-2 from the 1.0 System Integration Audit). Both the GUI entry point
(ui.main_window) and the CLI entry point (jarvis.py), as well as tests and
future API/CLI surfaces, MUST build their subsystems through build_runtime().

The factory performs explicit dependency injection in a fixed order so that
every implemented subsystem is instantiated exactly once and connected to the
central EventBus / Telemetry / Logger layer.

DI / construction order
-----------------------
 1. config
 2. EventBus, TelemetryManager, EventLogger          (observability core)
 3. PluginEvents + PluginManager                      (bridged into EventBus)
 4. PermissionManager, ToolRegistry                   (desktop tools)
 5. MemoryManager (V3) + JarvisMemoryV2 (chat)        (memory)
 6. KnowledgeEngine (RAG)                             (retrieval)
 7. FastIntentRouter -> IntentAnalyzer               (intent)
 8. HabitManager                                      (habit learning)
 9. WorkspaceManager                                  (workspace awareness)
10. WorkflowManager                                   (workflow execution)
11. ProactiveManager                                  (proactive assistant)
12. Calendar Plugin via PluginManager + live instance (plugin SDK proof)
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("runtime")


@dataclass
class RuntimeContext:
    """Holds every subsystem instance for one Jarvis process.

    This is the single object that owns all managers. Nothing here uses
    module-level singletons; callers receive the context and pass managers
    explicitly where needed.
    """

    repo: Path
    config: Any = None

    # observability
    event_bus: Any = None
    telemetry: Any = None
    event_logger: Any = None

    # plugins
    plugin_events: Any = None
    plugin_manager: Any = None
    calendar_plugin: Any = None
    _plugin_unbridge: Any = None

    # tools / llm
    permission_manager: Any = None
    tool_registry: Any = None
    llm: Any = None
    brain: Any = None

    # memory / rag
    memory_manager: Any = None          # Memory V3
    chat_memory: Any = None             # JarvisMemoryV2
    knowledge: Any = None              # RAG service

    # intelligence
    intent_router: Any = None
    intent_analyzer: Any = None
    habit_manager: Any = None
    workspace_manager: Any = None
    workflow_manager: Any = None
    proactive_manager: Any = None

    # misc
    goal_manager: Any = None
    task_queue: Any = None
    developer_advisor: Any = None
    system_awareness: Any = None
    ollama_health: Any = None

    # bookkeeping
    built_at: float = field(default_factory=time.perf_counter)
    errors: list[str] = field(default_factory=list)
    _lock: Any = field(default_factory=threading.RLock)

    def all_managers(self) -> dict[str, Any]:
        return {
            "event_bus": self.event_bus,
            "telemetry": self.telemetry,
            "event_logger": self.event_logger,
            "plugin_events": self.plugin_events,
            "plugin_manager": self.plugin_manager,
            "calendar_plugin": self.calendar_plugin,
            "permission_manager": self.permission_manager,
            "tool_registry": self.tool_registry,
            "memory_manager": self.memory_manager,
            "chat_memory": self.chat_memory,
            "knowledge": self.knowledge,
            "intent_router": self.intent_router,
            "intent_analyzer": self.intent_analyzer,
            "habit_manager": self.habit_manager,
            "workspace_manager": self.workspace_manager,
            "workflow_manager": self.workflow_manager,
            "proactive_manager": self.proactive_manager,
            "goal_manager": self.goal_manager,
            "task_queue": self.task_queue,
        }


def _safe(step: str, ctx: RuntimeContext, fn) -> Any:
    """Run one construction step, capturing failures instead of aborting."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - integration must be resilient
        msg = f"{step} failed: {type(exc).__name__}: {exc}"
        logger.error(msg)
        ctx.errors.append(msg)
        return None


def build_runtime(config: Optional[Any] = None, repo: Optional[Path] = None) -> RuntimeContext:
    """Construct every core subsystem exactly once and wire them together.

    Args:
        config: a JarvisConfig instance, or None to build a default one.
        repo:   repo root; defaults to the parent of the runtime package.

    Returns:
        RuntimeContext with all managers instantiated (or errors recorded).
    """
    from modules.config import JarvisConfig

    repo = Path(repo or Path(__file__).resolve().parent.parent)
    ctx = RuntimeContext(repo=repo)
    ctx.config = config or JarvisConfig(project_root=repo)

    # 1. observability core
    from core.events.event_bus import EventBus
    from core.events.telemetry import TelemetryManager
    from core.events.logger import EventLogger

    ctx.event_bus = _safe("event_bus", ctx, EventBus)
    ctx.telemetry = _safe("telemetry", ctx, TelemetryManager)
    ctx.event_logger = _safe("event_logger", ctx, lambda: EventLogger(log_dir=str(repo / "logs")))

    if ctx.event_bus is not None:
        # Record every published event to telemetry + persisted logger.
        def _observe(event) -> None:
            try:
                if ctx.telemetry is not None:
                    ctx.telemetry.record(
                        source=event.source or "unknown",
                        duration_ms=getattr(event, "duration_ms", None),
                    )
                if ctx.event_logger is not None:
                    ctx.event_logger.log(event)
            except Exception:  # pragma: no cover - observer must never break publish
                pass

        # Subscribe the observer using the actual EventType enum values so it
        # matches events emitted by the bridges (which map to EventType members).
        from core.events import EventType as _ET

        _observed = [
            _ET.PLUGIN_LOADED, _ET.PLUGIN_ENABLED, _ET.PLUGIN_DISABLED,
            _ET.PLUGIN_RELOADED, _ET.PLUGIN_UNLOADED, _ET.PLUGIN_ERROR,
            _ET.WORKFLOW_STARTED, _ET.WORKFLOW_FAILED, _ET.MEMORY_ADDED,
            _ET.HABIT_LEARNED, _ET.WORKSPACE_CHANGED, _ET.RAG_SEARCH_COMPLETED,
            _ET.CALENDAR_EVENT_CREATED, _ET.CALENDAR_REMINDER_FIRED,
            _ET.PROACTIVE_SUGGESTION_GENERATED, _ET.APP_STARTED, _ET.APP_CLOSED,
            _ET.CUSTOM,
        ]
        for _et in _observed:
            try:
                ctx.event_bus.subscribe(_et, _observe)
            except Exception:
                pass

    # 2. plugins (with legacy bridge into the central bus)
    from plugins.sdk.events import PluginEvents
    from plugins.sdk.manager import PluginManager
    from core.events.adapters import bridge_plugin_events

    ctx.plugin_events = _safe("plugin_events", ctx, PluginEvents)
    ctx.plugin_manager = _safe(
        "plugin_manager",
        ctx,
        lambda: PluginManager(
            plugins_dir=str(repo / "plugins"),
            events=ctx.plugin_events,
        ),
    )
    if ctx.plugin_manager is not None and ctx.event_bus is not None and ctx.plugin_events is not None:
        ctx._plugin_unbridge = bridge_plugin_events(ctx.event_bus, ctx.plugin_events)

    # 3. permissions + tools
    from modules.permission_manager import PermissionManager

    ctx.permission_manager = _safe("permission_manager", ctx, PermissionManager)

    from modules.tools import ToolRegistry

    ctx.tool_registry = _safe(
        "tool_registry",
        ctx,
        lambda: ToolRegistry(ctx.config, permissions=ctx.permission_manager),
    )

    # 4. memory
    from modules.memory_v2 import MemoryManager, JarvisMemoryV2

    ctx.memory_manager = _safe("memory_manager", ctx, lambda: MemoryManager(ctx.config))
    ctx.chat_memory = _safe(
        "chat_memory",
        ctx,
        lambda: JarvisMemoryV2(ctx.config, use_chroma=False),
    )

    # 5. RAG
    from knowledge.knowledge_engine import KnowledgeEngine

    ctx.knowledge = _safe(
        "knowledge",
        ctx,
        lambda: KnowledgeEngine(
            root_dir=ctx.config.knowledge_root,
            use_chroma=True,
        ),
    )

    # 6. intent
    from modules.fast_intent import FastIntentRouter
    from modules.intent.analyzer import IntentAnalyzer

    ctx.intent_router = _safe(
        "intent_router", ctx, lambda: FastIntentRouter(ctx.tool_registry)
    )
    ctx.intent_analyzer = _safe(
        "intent_analyzer",
        ctx,
        lambda: IntentAnalyzer(router=ctx.intent_router, memory=ctx.memory_manager),
    )

    # 7. habits
    from habits.habit_manager import HabitManager

    ctx.habit_manager = _safe("habit_manager", ctx, lambda: HabitManager())

    # 8. workspace
    from workspace.workspace_manager import WorkspaceManager

    ctx.workspace_manager = _safe("workspace_manager", ctx, lambda: WorkspaceManager())

    # 9. workflows
    from workflows.manager import WorkflowManager

    ctx.workflow_manager = _safe("workflow_manager", ctx, lambda: WorkflowManager())

    # 10. proactive (depends on memory, habits, rag, workflow, workspace, intent)
    from proactive.proactive_manager import ProactiveManager

    ctx.proactive_manager = _safe(
        "proactive_manager",
        ctx,
        lambda: ProactiveManager(
            memory=ctx.memory_manager,
            habits=ctx.habit_manager,
            rag=ctx.knowledge,
            workflow_manager=ctx.workflow_manager,
            workspace_manager=ctx.workspace_manager,
            intent_analyzer=ctx.intent_analyzer,
        ),
    )

    # 11. goals + task queue (workflow/execution backbone)
    from goal_manager.goal_manager import GoalManager
    from goal_manager.goal_storage import GoalStorage
    from task_queue.task_queue import TaskQueue
    from task_queue.task_storage import TaskStorage

    # Ensure the data directory exists before SQLite tries to create the files
    # (sqlite will not create parent directories on its own).
    data_dir = repo / "data"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        ctx.errors.append(f"data dir creation failed: {exc}")

    ctx.goal_manager = _safe(
        "goal_manager",
        ctx,
        lambda: GoalManager(storage=GoalStorage(data_dir / "goals.sqlite")),
    )
    ctx.task_queue = _safe(
        "task_queue",
        ctx,
        lambda: TaskQueue(storage=TaskStorage(data_dir / "tasks.sqlite"), goal_manager=ctx.goal_manager),
    )

    # 12. calendar plugin (exercises Plugin SDK end-to-end + live instance)
    _load_calendar_plugin(ctx)

    # 13. Ollama health + degraded-mode monitor
    from core.ollama_health import OllamaHealth

    ctx.ollama_health = _safe(
        "ollama_health",
        ctx,
        lambda: OllamaHealth(
            base_url=getattr(ctx.config, "llm_base_url", "http://localhost:11434"),
            model=getattr(ctx.config, "llm_model", "llama3"),
            warning_latency_s=float(getattr(ctx.config, "ollama_warning_latency_s", 8.0)),
            critical_latency_s=float(getattr(ctx.config, "ollama_critical_latency_s", 20.0)),
            auto_reconnect=bool(getattr(ctx.config, "ollama_auto_reconnect", True)),
            check_interval_s=float(getattr(ctx.config, "ollama_health_interval_s", 30.0)),
        ),
    )

    if ctx.errors:
        logger.warning("build_runtime completed with %d error(s): %s", len(ctx.errors), ctx.errors)
    else:
        logger.info("build_runtime: all subsystems constructed")
    return ctx


def _load_calendar_plugin(ctx: RuntimeContext) -> None:
    """Load the calendar plugin through the Plugin SDK and create a live instance."""
    if ctx.plugin_manager is None:
        return
    # Discover + load + enable the calendar plugin (fires plugin events -> bus).
    try:
        ctx.plugin_manager.discover()
        for p in ctx.plugin_manager.list_plugins():
            if p["plugin_id"] == "calendar_plugin":
                ctx.plugin_manager.load("calendar_plugin")
                ctx.plugin_manager.enable("calendar_plugin")
    except Exception as exc:  # noqa: BLE001
        ctx.errors.append(f"calendar_plugin sdk load failed: {exc}")
        logger.warning("calendar_plugin sdk load failed: %s", exc)

    # Build the live, callable plugin instance bound to a PluginAPI facade.
    try:
        from plugins.calendar_plugin.plugin import CalendarPlugin
        from plugins.sdk.api import PluginAPI

        api = PluginAPI(
            memory=ctx.memory_manager,
            rag=ctx.knowledge,
            workflow_manager=ctx.workflow_manager,
            workspace_manager=ctx.workspace_manager,
            intent_analyzer=ctx.intent_analyzer,
            habit_manager=ctx.habit_manager,
            tool_registry=ctx.tool_registry,
            events=ctx.plugin_events,
        )
        plugin = CalendarPlugin(api=api)
        plugin.on_load()
        ctx.calendar_plugin = plugin
    except Exception as exc:  # noqa: BLE001
        ctx.errors.append(f"calendar_plugin instance failed: {exc}")
        logger.warning("calendar_plugin instance failed: %s", exc)


def startup(ctx: RuntimeContext) -> None:
    """Start long-running / background subsystems. Idempotent and safe to re-call."""
    with ctx._lock:
        if ctx.workspace_manager is not None:
            try:
                ctx.workspace_manager.start()
            except Exception as exc:  # noqa: BLE001
                ctx.errors.append(f"workspace start failed: {exc}")
        if ctx.proactive_manager is not None:
            try:
                ctx.proactive_manager.start()
            except Exception as exc:  # noqa: BLE001
                ctx.errors.append(f"proactive start failed: {exc}")
        if ctx.ollama_health is not None:
            try:
                ctx.ollama_health.start()
            except Exception as exc:  # noqa: BLE001
                ctx.errors.append(f"ollama_health start failed: {exc}")
        if ctx.event_bus is not None:
            try:
                from core.events import Event, EventType
                ctx.event_bus.publish(
                    Event(
                        event_type=EventType.APP_STARTED,
                        source="runtime",
                        payload={"managers": [k for k, v in ctx.all_managers().items() if v is not None]},
                    )
                )
            except Exception:
                pass


def shutdown(ctx: RuntimeContext) -> None:
    """Gracefully shut down every subsystem in dependency-reverse order."""
    with ctx._lock:
        ordered = [
            ("proactive_manager", "stop"),
            ("workspace_manager", "stop"),
            ("ollama_health", "stop"),
            ("workflow_manager", "close"),
            ("habit_manager", "close"),
            ("knowledge", "close"),
            ("chat_memory", "shutdown"),
            ("memory_manager", "shutdown"),
            ("plugin_manager", "_unload_all"),
        ]
        for attr, method in ordered:
            obj = getattr(ctx, attr, None)
            if obj is None:
                continue
            try:
                if method == "_unload_all":
                    for p in ctx.plugin_manager.list_plugins():
                        try:
                            ctx.plugin_manager.unload(p["plugin_id"])
                        except Exception:
                            pass
                elif hasattr(obj, method):
                    getattr(obj, method)()
            except Exception as exc:  # noqa: BLE001
                logger.warning("shutdown %s.%s failed: %s", attr, method, exc)

        # Unbridge plugin events from the central bus.
        if ctx._plugin_unbridge is not None:
            try:
                ctx._plugin_unbridge()
            except Exception:
                pass

        if ctx.event_bus is not None:
            try:
                from core.events import Event, EventType
                ctx.event_bus.publish(
                    Event(
                        event_type=EventType.APP_CLOSED,
                        source="runtime",
                        payload={"managers": len([v for v in ctx.all_managers().values() if v is not None])},
                    )
                )
            except Exception:
                pass


# Alias so callers/tests can avoid clashing with builtins/blocklisted names.
stop_runtime = shutdown
