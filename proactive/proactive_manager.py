"""ProactiveManager: orchestrates context analysis, triggers, suggestions, and notifications."""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

from proactive.state import Suggestion, Trigger
from proactive.history import ProactiveHistory
from proactive.context_analyzer import ContextAnalyzer
from proactive.trigger_engine import TriggerEngine
from proactive.suggestion_engine import SuggestionEngine, DismissalMemory


class ProactiveManager:
    def __init__(
        self,
        history: Optional[ProactiveHistory] = None,
        context_analyzer: Optional[ContextAnalyzer] = None,
        trigger_engine: Optional[TriggerEngine] = None,
        suggestion_engine: Optional[SuggestionEngine] = None,
        memory: Any = None,
        habits: Any = None,
        rag: Any = None,
        workflow_manager: Any = None,
        workspace_manager: Any = None,
        intent_analyzer: Any = None,
    ) -> None:
        self.history = history or ProactiveHistory()
        self.context_analyzer = context_analyzer or ContextAnalyzer(
            memory=memory,
            habits=habits,
            rag=rag,
            workflow_manager=workflow_manager,
            workspace_manager=workspace_manager,
            intent_analyzer=intent_analyzer,
        )
        self.trigger_engine = trigger_engine or TriggerEngine(history=self.history)
        self.suggestion_engine = suggestion_engine or SuggestionEngine()
        self._lock = threading.RLock()
        self._last_analysis: dict[str, Any] = {}
        self._default_triggers = [
            Trigger(name="git_dirty", category="workspace", condition="git_dirty", cooldown_s=600.0),
            Trigger(name="continue_project", category="workflow", condition="continue_project", cooldown_s=1800.0),
            Trigger(name="habit_suggestion", category="habit", condition="habit_suggestion", cooldown_s=3600.0),
            Trigger(name="rag_summarize", category="document", condition="rag_summarize", cooldown_s=7200.0),
        ]

    def start(self) -> None:
        with self._lock:
            for trigger in self._default_triggers:
                self.trigger_engine.register(trigger)

    def analyze(self, prompt: Optional[str] = None) -> list[Suggestion]:
        with self._lock:
            context = self.context_analyzer.analyze(prompt)
            self._last_analysis = context
            triggered = self.trigger_engine.evaluate(context)
            accepted = self.suggestion_engine.enqueue(triggered)
            for s in accepted:
                self.history.save_suggestion(s)
            return accepted

    def notify(self, limit: int = 3) -> list[Suggestion]:
        with self._lock:
            suggestions = self.suggestion_engine.notify(limit)
            for s in suggestions:
                item = self._queue_item(s)
                self.history.enqueue(item)
            if suggestions:
                self.suggestion_engine.record_user_suggestion_sent()
            return suggestions

    def dismiss(self, suggestion: Suggestion) -> None:
        with self._lock:
            self.suggestion_engine.dismiss(suggestion)

    def history(self) -> list[Suggestion]:
        with self._lock:
            return self.history.recent_suggestions()

    def _queue_item(self, suggestion: Suggestion) -> Any:
        try:
            from proactive.state import NotificationQueueItem
            return NotificationQueueItem(suggestion=suggestion, status="delivered")
        except Exception:
            return None

    def close(self) -> None:
        self.history.close()

    def __enter__(self) -> "ProactiveManager":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
