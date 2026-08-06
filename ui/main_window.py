"""Jarvis desktop main window."""
from __future__ import annotations

import inspect
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ui.theme import apply
from ui.sidebar import Sidebar
from ui.chat_panel import ChatPanel
from ui.goal_panel import GoalPanel
from ui.task_panel import TaskPanel
from ui.knowledge_panel import KnowledgePanel
from ui.memory_panel import MemoryPanel
from ui.activity_panel import ActivityPanel
from ui.settings_panel import SettingsPanel
from ui.notification_center import NotificationCenter
from ui.permission_dialog import PermissionDialog
from ui.monitor_panel import MonitorPanel
from ui.workflow_panel import WorkflowPanel
from ui.workspace_panel import WorkspacePanel

from runtime.runtime import build_runtime, startup as rt_startup, stop_runtime as rt_stop


class _WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(str, int)


class _Worker(QThread):
    def __init__(self, fn, *args, parent=None, **kwargs):
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.signals = _WorkerSignals(parent=self)

    def run(self):
        print(f"[thread] _Worker.run start thread={threading.current_thread().name}")
        try:
            result = self._fn(*self._args, **self._kwargs)
            self.signals.finished.emit(result)
            print(f"[thread] _Worker.run finished result={result!r}")
        except Exception as exc:
            self.signals.failed.emit(f"{exc}: {traceback.format_exc()}")
            print(f"[thread] _Worker.run failed: {exc}")
        print("[thread] _Worker.run destroying")


class AgentRuntime:
    def __init__(self, repo: Path):
        self.repo = repo
        self.config: Any = None
        self.memory: Any = None
        self.goal_manager: Any = None
        self.task_queue: Any = None
        self.knowledge_engine: Any = None
        self.tool_registry: Any = None
        self.brain: Any = None
        self.execution_manager: Any = None
        self.permission_manager: Any = None
        self.voice: Any = None
        self.vision: Any = None
        self.logger: Any = None
        self.session: Any = None
        self.workspace_watcher: Any = None
        self.system_awareness: Any = None
        self.developer_advisor: Any = None
        self.monitor_panel: Any = None
        self._lock = threading.RLock()
        self._pending_inits: dict[str, Callable] = {}

        # P0-2: single authoritative runtime construction path.
        # build_runtime() instantiates every subsystem exactly once via explicit
        # dependency injection and returns a RuntimeContext. We then mirror the
        # managers onto this object using the attribute names the rest of the UI
        # already expects, so no subsystem is constructed twice.
        self._ctx = build_runtime(repo=repo)
        self._sync_from_ctx()

    def _sync_from_ctx(self) -> None:
        """Copy every manager from the shared RuntimeContext onto this object."""
        ctx = self._ctx
        self.config = ctx.config
        # observability
        self.event_bus = ctx.event_bus
        self.telemetry = ctx.telemetry
        self.event_logger = ctx.event_logger
        # plugins
        self.plugin_events = ctx.plugin_events
        self.plugin_manager = ctx.plugin_manager
        self.calendar_plugin = ctx.calendar_plugin
        # tools / llm
        self.permission_manager = ctx.permission_manager
        self.tool_registry = ctx.tool_registry
        # memory / rag
        self.memory_manager = ctx.memory_manager          # Memory V3
        self.memory = ctx.chat_memory                    # chat memory
        self.knowledge_engine = ctx.knowledge            # RAG service
        self.knowledge = ctx.knowledge
        # intelligence
        self.intent_router = ctx.intent_router
        self.intent_analyzer = ctx.intent_analyzer
        self.habit_manager = ctx.habit_manager
        self.workspace_manager = ctx.workspace_manager
        self.workflow_manager = ctx.workflow_manager
        self.proactive_manager = ctx.proactive_manager
        # misc
        self.goal_manager = ctx.goal_manager
        self.task_queue = ctx.task_queue
        if ctx.errors:
            print(f"[runtime] build completed with {len(ctx.errors)} error(s): {ctx.errors}")

    def run_in_thread(self, fn: Callable, *args, **kwargs):
        thread = threading.Thread(target=self._run, args=(fn, args, kwargs), daemon=True)
        thread.start()
        return thread

    def _run(self, fn, args, kwargs):
        try:
            fn(*args, **kwargs)
        except Exception:
            traceback.print_exc()


class JarvisWindow(QMainWindow):
    def __init__(
        self,
        repo: Optional[Path] = None,
        config: Any = None,
        voice: Any = None,
        brain: Any = None,
        memory: Any = None,
        vision: Any = None,
        permissions: Any = None,
        tools: Any = None,
    ):
        super().__init__()
        self.repo = repo or Path(__file__).resolve().parent.parent
        self.runtime = AgentRuntime(self.repo)
        if config is not None:
            self.runtime.config = config
        if voice is not None:
            self.runtime.voice = voice
        if brain is not None:
            self.runtime.brain = brain
        if memory is not None:
            self.runtime.memory = memory
        if vision is not None:
            self.runtime.vision = vision
        if permissions is not None:
            self.runtime.permission_manager = permissions
        if tools is not None:
            self.runtime.tool_registry = tools
        self._progress_visible = False
        self._ollama_ok = False
        self._ollama_retries = 0
        self._ollama_max_retries = 5
        self._ollama_backoff_s = 2
        self._startup_worker = None
        self._ollama_timer = None
        self._resource_timer = None
        self._monitor_timer = None
        self._build_ui()
        self._build_status()
        self._build_monitor()
        self._startup_t0 = time.time()
        self._startup_queue = [
            ("config", self._init_config),
            ("logger", self._init_logger),
            ("session", self._init_session),
            ("memory", self._init_memory),
            ("permissions", self._init_permissions),
            ("goals", self._init_goals_lazy),
            ("tasks", self._init_tasks_lazy),
            ("knowledge", self._init_knowledge_lazy),
            ("tools", self._init_tools),
            ("brain", self._init_brain),
            ("execution", self._init_execution),
            ("voice", self._init_voice_lazy),
            ("vision", self._init_vision_lazy),
            ("workspace", self._init_workspace_lazy),
            ("system", self._init_system_lazy),
            ("advisor", self._init_advisor_lazy),
            ("ollama", self._init_ollama),
            ("wire", self._wire_panels),
            ("runtime_start", self._runtime_start),
            ("dashboard", self._show_startup_dashboard),
        ]
        self._startup_next()

    def _runtime_start(self):
        # Start long-running subsystems (workspace watcher, proactive engine,
        # publish APP_STARTED) through the shared runtime lifecycle.
        try:
            rt_startup(self.runtime._ctx)
        except Exception as exc:
            print("[runtime_start]", exc)

    def _build_ui(self):
        self.setWindowTitle("Jarvis")
        self.resize(1280, 840)

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = Sidebar()
        root.addWidget(self.sidebar, 0)

        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)

        self.chat_panel = ChatPanel()
        self.goal_panel = GoalPanel()
        self.task_panel = TaskPanel()
        self.knowledge_panel = KnowledgePanel()
        self.memory_panel = MemoryPanel()
        self.activity_panel = ActivityPanel()
        self.workspace_panel = WorkspacePanel()
        self.settings_panel = SettingsPanel()

        for widget in [
            self.chat_panel,
            self.goal_panel,
            self.task_panel,
            self.knowledge_panel,
            self.memory_panel,
            self.activity_panel,
            self.workspace_panel,
            self.settings_panel,
        ]:
            self.pages.addWidget(widget)

        self.notifications = NotificationCenter(self)
        self.notifications.setVisible(False)

        self.sidebar.sectionChanged.connect(self._on_section_changed)

        # Chat signals
        self.chat_panel.sendMessage.connect(self._on_send_message)
        self.chat_panel.regenerateRequested.connect(lambda: None)
        self.chat_panel.stopGenerationRequested.connect(lambda: None)
        self.chat_panel.uploadFileRequested.connect(lambda: None)

        self.chat_panel.appendAssistant.connect(self._on_append_assistant)
        self.chat_panel.appendUser.connect(self._on_append_user)
        self.chat_panel.appendSystem.connect(self._on_append_system)

    def _on_append_assistant(self, text: str, streaming: bool = False):
        self.chat_panel.append_assistant(text, streaming=streaming)

    def _on_append_user(self, text: str):
        self.chat_panel.append_user(text)

    def _on_append_system(self, text: str):
        self.chat_panel.append_system(text)

    def _build_status(self):
        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet(f"QStatusBar {{ background: #111216; color: #9CA0A8; border-top: 1px solid #2E323A; }}")
        self.setStatusBar(self.status_bar)
        self.ollama_indicator = QLabel("● Ollama: checking")
        self.ollama_indicator.setStyleSheet("color: #FFB84D;")
        self.status_bar.addPermanentWidget(self.ollama_indicator)
        self.status_label = QLabel("Starting...")
        self.status_bar.addWidget(self.status_label, 1)

    def _build_monitor(self):
        self.runtime.monitor_panel = MonitorPanel()
        self.pages.addWidget(self.runtime.monitor_panel)
        self.workflow_panel = WorkflowPanel()
        self.pages.addWidget(self.workflow_panel)
        if hasattr(self, "sidebar") and hasattr(self.sidebar, "sections"):
            try:
                self.sidebar.sections["monitor"] = len(self.sidebar.sections)
            except Exception:
                pass

    def _startup_next(self):
        if not self._startup_queue:
            self._hide_progress()
            self.status_label.setText("Ready")
            if self.runtime.logger:
                self.runtime.logger.info("Startup complete")
            return
        label, fn = self._startup_queue.pop(0)
        self._show_progress(label)
        print(f"[thread] _startup_next queued={label}")
        worker = _Worker(self._safe(fn), parent=self)
        worker.signals.finished.connect(lambda result: self._startup_next())
        worker.signals.failed.connect(self._on_startup_failed)
        worker.finished.connect(lambda: self._on_startup_worker_finished(worker))
        self._startup_worker = worker
        worker.start()
        print(f"[thread] _startup_next started thread={worker.currentThread().objectName()}")

    def _show_progress(self, label: str):
        self.status_label.setText(f"Starting: {label}")

    def _hide_progress(self):
        pass

    def _safe(self, fn):
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)
        return wrapper

    def _on_startup_failed(self, err: str):
        print("[startup]", err)
        self.status_label.setText(f"Startup issue: {err}")
        self._startup_next()

    def _on_startup_worker_finished(self, worker):
        print(f"[thread] startup worker finished")
        if getattr(self, "_startup_worker", None) is worker:
            self._startup_worker = None

    def _init_config(self):
        # Already built by build_runtime(); keep a stable reference.
        self.runtime.config = self.runtime._ctx.config

    def _init_logger(self):
        from modules.logger import get_logger
        self.runtime.logger = get_logger("jarvis")
        self.runtime.logger.info("Runtime init starting")

    def _init_session(self):
        try:
            from modules.session import SessionStore
            self.runtime.session = SessionStore(base_dir=self.repo / "data")
            self.runtime.session.restore()
            self._apply_session(self.runtime.session.current())
        except Exception as exc:
            print("[session]", exc)

    def _apply_session(self, session):
        try:
            if session.panel in {"chat", "goals", "tasks", "knowledge", "memory", "activity", "settings", "monitor"}:
                self.sidebar.set_active(session.panel)
        except Exception:
            pass

    def _init_memory(self):
        # Re-mirrored from the shared context (built exactly once in __init__).
        self._sync_from_ctx()

    def _init_permissions(self):
        self.runtime.permission_manager = self.runtime._ctx.permission_manager

    def _init_tools(self):
        self.runtime.tool_registry = self.runtime._ctx.tool_registry

    def _init_brain(self):
        from modules.brain_graph import JarvisBrain
        mem = self.runtime.memory or self._safe_init_memory()
        self.runtime.brain = JarvisBrain(self.runtime.config, self.runtime.tool_registry, mem)

    def _safe_init_memory(self):
        from modules.memory_v2 import JarvisMemoryV2
        return JarvisMemoryV2(self.runtime.config)

    def _init_execution(self):
        from modules.execution_manager import ExecutionManager
        self.runtime.execution_manager = ExecutionManager(self.runtime.config, self.runtime.permission_manager)

    def _init_voice(self):
        try:
            from modules.voice import VoiceEngine
            self.runtime.voice = VoiceEngine(self.runtime.config)
        except Exception as exc:
            print("[voice]", exc)

    def _init_vision(self):
        try:
            from modules.vision import VisionEngine
            self.runtime.vision = VisionEngine(self.runtime.config)
        except Exception as exc:
            print("[vision]", exc)

    def _init_workspace(self):
        try:
            from modules.workspace import WorkspaceWatcher
            self.runtime.workspace_watcher = WorkspaceWatcher(cache_path=self.repo / "data" / "workspace_cache.json")
            self.runtime.workspace_watcher.start()
        except Exception as exc:
            print("[workspace]", exc)

    def _init_system(self):
        try:
            from modules.system_awareness import SystemAwareness
            self.runtime.system_awareness = SystemAwareness(ollama_base_url=getattr(self.runtime.config, "llm_base_url", "http://localhost:11434"))
            self._schedule_resource_refresh()
        except Exception as exc:
            print("[system]", exc)

    def _init_advisor(self):
        try:
            from modules.developer_advisor import DeveloperAdvisor
            self.runtime.developer_advisor = DeveloperAdvisor(project_root=self.repo)
        except Exception as exc:
            print("[advisor]", exc)

    def _init_goals(self):
        # Already built by build_runtime(); keep the shared instance.
        self.runtime.goal_manager = self.runtime._ctx.goal_manager

    def _init_tasks(self):
        self.runtime.task_queue = self.runtime._ctx.task_queue

    def _init_knowledge(self):
        self.runtime.knowledge_engine = self.runtime._ctx.knowledge

    def _wire_panels(self):
        """P0-1 UI wiring: hand fully-initialized managers to the panels."""
        try:
            if self.workflow_panel is not None and self.runtime.workflow_manager is not None:
                self.workflow_panel.set_manager(self.runtime.workflow_manager)
        except Exception as exc:
            print("[wire:workflow]", exc)
        try:
            if self.workspace_panel is not None and self.runtime.workspace_manager is not None:
                self.workspace_panel.set_manager(self.runtime.workspace_manager)
        except Exception as exc:
            print("[wire:workspace]", exc)

    def _init_goals_lazy(self):
        self.runtime._pending_inits["goals"] = self._init_goals

    def _init_tasks_lazy(self):
        self.runtime._pending_inits["tasks"] = self._init_tasks

    def _init_knowledge_lazy(self):
        self.runtime._pending_inits["knowledge"] = self._init_knowledge

    def _init_voice_lazy(self):
        self.runtime._pending_inits["voice"] = self._init_voice

    def _init_vision_lazy(self):
        self.runtime._pending_inits["vision"] = self._init_vision

    def _init_workspace_lazy(self):
        self.runtime._pending_inits["workspace"] = self._init_workspace

    def _init_system_lazy(self):
        self.runtime._pending_inits["system"] = self._init_system

    def _init_advisor_lazy(self):
        self.runtime._pending_inits["advisor"] = self._init_advisor

    def _resolve_pending(self, name: str) -> None:
        fn = self.runtime._pending_inits.pop(name, None)
        if fn is None:
            return
        try:
            fn()
        except Exception as exc:
            print(f"[lazy:{name}]", exc)

    def _init_ollama(self):
        try:
            self._schedule_ollama_check()
        except Exception as exc:
            print("[ollama]", exc)

    def _show_startup_dashboard(self):
        elapsed = time.time() - self._startup_t0
        self.status_label.setText(f"Ready in {elapsed:.2f}s")
        if self.runtime.logger:
            self.runtime.logger.info("Startup complete in %.2fs", elapsed)
        self._schedule_monitor_refresh()

    def _schedule_monitor_refresh(self):
        self._monitor_timer = QTimer(self)
        self._monitor_timer.setInterval(1500)
        self._monitor_timer.timeout.connect(self._refresh_monitor)
        self._monitor_timer.start()
        self._refresh_monitor()

    def _schedule_resource_refresh(self):
        self._resource_timer = QTimer(self)
        self._resource_timer.setInterval(2000)
        self._resource_timer.timeout.connect(self._refresh_resources)
        self._refresh_resources()
        self._resource_timer.start()

    def _refresh_monitor(self):
        state = self._collect_monitor_state()
        self._queue_ui(self.runtime.monitor_panel._on_state if self.runtime.monitor_panel else lambda s: None, state)

    def _collect_monitor_state(self):
        sections = []
        try:
            if self.runtime.workspace_watcher:
                ctx = self.runtime.workspace_watcher.cached() or self.runtime.workspace_watcher.snapshot()
                sections.append(f"Workspace: {ctx.root or 'None'}")
                sections.append(f"Git: {ctx.git_branch or 'none'} {'dirty' if ctx.dirty else 'clean'}")
                sections.append(f"Modified: {len(ctx.modified_files)}")
                sections.append(f"Languages: {', '.join(list(ctx.languages.keys())[:3]) or 'none'}")
                sections.append(f"Tests: {ctx.test_framework or 'none'}")
        except Exception:
            pass
        try:
            goals = []
            if self.runtime.goal_manager:
                goals = self.runtime.goal_manager.get_active_goals()
            goal = goals[0] if goals else None
            sections.append(f"Goals: {getattr(goal, 'title', 'none')}")
        except Exception:
            sections.append("Goals: none")
        try:
            tasks = []
            if self.runtime.task_queue:
                tasks = self.runtime.task_queue.get_queue()
            sections.append(f"Tasks: {len(tasks)}")
        except Exception:
            sections.append("Tasks: 0")
        try:
            if self.runtime.developer_advisor:
                suggestions = self.runtime.developer_advisor.suggest()
                sections.append(f"Suggestions: {len(suggestions)}")
        except Exception:
            sections.append("Suggestions: 0")
        try:
            mic = "Ready" if getattr(getattr(self.runtime, 'voice', None), '_microphone_enabled', False) else "Unavailable"
            sections.append(f"Microphone: {mic}")
        except Exception:
            sections.append("Microphone: n/a")
        sections.append(self.ollama_indicator.text())
        try:
            if self.runtime.system_awareness:
                snap = self.runtime.system_awareness.snapshot()
                sections.append(f"Resources: CPU {snap.cpu_percent:.0f}% RAM {snap.ram_percent:.0f}%")
                if snap.battery_percent is not None:
                    sections.append(f"Battery: {snap.battery_percent:.0f}%")
                sections.append(f"Internet: {'yes' if snap.internet_available else 'no'}")
                sections.append(f"Disk: {snap.disk_percent:.0f}%" if snap.disk_percent is not None else "Disk: n/a")
                sections.append(f"Ollama: {'online' if snap.ollama_available else 'offline'}")
        except Exception:
            sections.append("Resources: n/a")
        return "\n".join(sections)

    def _refresh_resources(self):
        try:
            if not self.runtime.system_awareness:
                return
            snap = self.runtime.system_awareness.snapshot()
            text = f"CPU {snap.cpu_percent:.0f}% | RAM {snap.ram_percent:.0f}%"
            if snap.battery_percent is not None:
                text += f" | Battery {snap.battery_percent:.0f}%"
            self.status_label.setText(text)
            self._ollama_ok = snap.ollama_available
            self._update_ollama_ui(snap.ollama_available)
        except Exception:
            pass

    def _on_section_changed(self, section: str):
        mapping = {
            "chat": self.chat_panel,
            "goals": self.goal_panel,
            "tasks": self.task_panel,
            "knowledge": self.knowledge_panel,
            "memory": self.memory_panel,
            "activity": self.activity_panel,
            "workspace": self.workspace_panel,
            "monitor": self.runtime.monitor_panel,
            "settings": self.settings_panel,
        }
        widget = mapping.get(section)
        if widget is None:
            return
        if section == "goals":
            self._resolve_pending("goals")
        elif section == "tasks":
            self._resolve_pending("tasks")
        elif section == "knowledge":
            self._resolve_pending("knowledge")
        self.pages.setCurrentWidget(widget)
        if section == "goals":
            self._refresh_goals()
        elif section == "tasks":
            self._refresh_tasks()
        elif section == "knowledge":
            self._refresh_knowledge()
        elif section == "memory":
            self._refresh_memory()
        elif section == "monitor":
            self._refresh_monitor()

    def _on_send_message(self, text: str):
        if getattr(self, "_chat_busy", False):
            return
        self._chat_busy = True
        self._chat_user_text = text
        self.chat_panel.append_user(text)
        self._chat_stream_active = True
        self._chat_stream_buffer = ""
        self._chat_working_widget = self.chat_panel.append_assistant("Thinking...", streaming=True)
        self._chat_prompt_t0 = time.time()
        print(f"[chat] send_message t0={self._chat_prompt_t0:.3f}")
        self.runtime.run_in_thread(self._handle_chat_stream, text, lock=False)

    def _handle_chat_stream(self, prompt: str):
        answer = ""
        final_widget = self._chat_working_widget
        brain = self.runtime.brain
        if brain is None:
            answer = "Brain not initialized."
            self._complete_chat(answer, widget=final_widget)
            return
        try:
            if not self._ollama_ok:
                raise RuntimeError("Ollama unavailable. Retry in background.")
            t1 = time.time()
            print(f"[chat] brain_run_start t1={t1:.3f} elapsed={t1 - getattr(self, '_chat_prompt_t0', t1):.3f}")

            def append_chunk(token: str) -> None:
                self._chat_stream_buffer += token
                QTimer.singleShot(0, self._apply_stream_update, token)

            try:
                answer = brain.run_stream(prompt, on_chunk=append_chunk)
            except Exception:
                answer = brain.run(prompt)

            t2 = time.time()
            print(f"[chat] brain_run_end t2={t2:.3f} elapsed={t2 - t1:.3f}")
            self._chat_stream_active = False
        except Exception as exc:
            answer = f"Error: {exc}"
            print(f"[chat] brain_run_error t1={time.time():.3f} error={exc}")
            self._chat_stream_active = False
        t3 = time.time()
        print(f"[chat] brain_total elapsed={t3 - t1:.3f}")
        self._replace_working(answer or "")

    def _apply_stream_update(self, token: str):
        if not getattr(self, "_chat_stream_active", False):
            return
        w = getattr(self, "_chat_working_widget", None)
        try:
            if w is not None and not getattr(w, "isDeleted", lambda: True)():
                prev = w.toPlainText() if hasattr(w, "toPlainText") else ""
                w.setText(prev + token)
            else:
                self.chat_panel.append_system(token)
        except Exception as exc:
            print("[chat] stream_update_failed:", exc)

    def _replace_working(self, text: str):
        if getattr(self, "_chat_completed", False):
            return
        self._chat_completed = True

        def apply():
            try:
                w = getattr(self, "_chat_working_widget", None)
                is_deleted = getattr(w, "isDeleted", lambda: True)()
                if w is not None and not is_deleted:
                    final = getattr(self, "_chat_stream_buffer", "") or text
                    w.setText(final)
                    w.setStyleSheet(BUBBLE_ASSISTANT)
                else:
                    self.chat_panel.append_assistant(text, streaming=False)
            except Exception as exc:
                print("[chat] replace_working_failed:", exc)
            finally:
                t3 = time.time()
                print(f"[chat] ui_complete t3={t3:.3f} elapsed={t3 - getattr(self, '_chat_prompt_t0', t3):.3f}")
                self._chat_working_widget = None
                self._chat_completed = False
                self._chat_busy = False
        QTimer.singleShot(0, apply)

    def _refresh_goals(self):
        goals = []
        try:
            if self.runtime.goal_manager:
                goals = self.runtime.goal_manager.get_active_goals()
        except Exception:
            pass
        goal = goals[0] if goals else None
        self._queue_ui(self.goal_panel.set_goal, goal)

    def _refresh_tasks(self):
        tasks = []
        try:
            if self.runtime.task_queue:
                tasks = self.runtime.task_queue.get_queue()
        except Exception:
            pass
        self._queue_ui(self.task_panel.set_tasks, tasks)

    def _refresh_knowledge(self):
        docs = []
        try:
            if self.runtime.knowledge_engine:
                for doc_id, meta in self.runtime.knowledge_engine.storage._conn.execute("SELECT doc_id, filename, extension FROM files LIMIT 200").fetchall():
                    docs.append((doc_id, meta[1] if meta[1] else meta[2]))
        except Exception:
            pass
        self._queue_ui(self._hydrate_knowledge, docs)

    def _hydrate_knowledge(self, docs):
        self.knowledge_panel._list.clear()
        for doc_id, title in docs:
            self.knowledge_panel.add_document(doc_id, title or doc_id)

    def _refresh_memory(self):
        items = []
        try:
            if self.runtime.memory:
                for role, content, ts in self.runtime.memory._conn.execute("SELECT role, content, ts FROM messages ORDER BY ts DESC LIMIT 200").fetchall():
                    items.append((str(ts), f"{role}: {content[:120]}"))
        except Exception:
            pass
        self._queue_ui(self._hydrate_memory, items)

    def _hydrate_memory(self, items):
        self.memory_panel._list.clear()
        for mid, text in items:
            self.memory_panel.add_memory(mid, text)

    def _queue_ui(self, fn, *args, **kwargs):
        def wrapper():
            fn(*args, **kwargs)
        QTimer.singleShot(0, wrapper)

    def _append_system(self, text: str):
        self._queue_ui(self.chat_panel.append_system, text)

    def _append_assistant(self, text: str):
        self._queue_ui(self.chat_panel.append_assistant, text)

    def _schedule_ollama_check(self):
        self._ollama_timer = QTimer(self)
        self._ollama_timer.setInterval(1500)
        self._ollama_timer.timeout.connect(self._check_ollama)
        self._ollama_timer.start()

    def _check_ollama(self):
        self.runtime.run_in_thread(self._do_ollama_check)

    def _do_ollama_check(self):
        try:
            if self.runtime.ollama_health is None:
                ok = False
            else:
                snap = self.runtime.ollama_health.refresh()
                ok = snap.state.value
        except Exception:
            ok = False
        self._ollama_ok = bool(ok)
        self._queue_ui(self._update_ollama_ui, ok)

    def _update_ollama_ui(self, state):
        label = str(state)
        color = "#FFB84D"
        if state in {"ready", "loading"}:
            label = f"● Ollama: {label}"
            color = "#3DDC84"
        elif state in {"busy", "slow", "very_slow"}:
            label = f"● Ollama: {label}"
            color = "#FFB84D"
        elif state in {"offline", "unreachable", "model_missing", "error", "degraded"}:
            label = f"● Ollama: {label}"
            color = "#FF5A5A"
        else:
            label = f"● Ollama: {label}"
        self.ollama_indicator.setText(label)
        self.ollama_indicator.setStyleSheet(f"color: {color};")

    def _show_notification(self, message: str, level: str = "info"):
        self.notifications.show_notification(message, level)

    def closeEvent(self, event):
        try:
            # P0-1 graceful shutdown of the shared runtime (reverse-dependency
            # order: proactive, workspace, workflows, habits, knowledge, memory,
            # plugins). Runs before the UI-specific teardown below.
            try:
                rt_stop(self.runtime._ctx)
            except Exception as exc:
                print("[runtime_stop]", exc)
            for attr in ("_ollama_timer", "_resource_timer", "_monitor_timer"):
                timer = getattr(self, attr, None)
                if timer is not None:
                    try:
                        timer.stop()
                    except Exception:
                        pass
            if getattr(self, "_startup_worker", None) is not None:
                worker = self._startup_worker
                try:
                    worker.quit()
                except Exception:
                    pass
                try:
                    worker.wait(5000)
                except Exception:
                    pass
            if self.runtime.memory and hasattr(self.runtime.memory, "shutdown"):
                self.runtime.memory.shutdown()
            if self.runtime.knowledge_engine and hasattr(self.runtime.knowledge_engine, "close"):
                self.runtime.knowledge_engine.close()
            for name in ["goal_manager", "task_queue"]:
                inst = getattr(self.runtime, name, None)
                if inst and hasattr(inst, "storage") and hasattr(inst.storage, "close"):
                    inst.storage.close()
            if self.runtime.workspace_watcher:
                self.runtime.workspace_watcher.stop()
            if self.runtime.session and hasattr(self.runtime.session, "close"):
                self.runtime.session.close()
        except Exception:
            pass
        event.accept()
