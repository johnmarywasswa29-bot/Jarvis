"""TriggerEngine: evaluates conditions and fires suggestions with cooldown."""
from __future__ import annotations

import time
from typing import Any, Optional

from proactive.state import Trigger, Suggestion


class TriggerEngine:
    def __init__(self, history: Any = None) -> None:
        self.history = history
        self._triggers: list[Trigger] = []

    def register(self, trigger: Trigger) -> None:
        self._triggers.append(trigger)
        if self.history:
            try:
                self.history.save_trigger(trigger)
            except Exception:
                pass

    def evaluate(self, context: dict[str, Any]) -> list[Suggestion]:
        now = time.time()
        out: list[Suggestion] = []
        for trigger in self._triggers:
            if not trigger.enabled:
                continue
            last = trigger.last_fired
            if last is not None and (now - last) < trigger.cooldown_s:
                continue
            try:
                suggestion = self._match(trigger, context)
                if suggestion:
                    trigger.last_fired = now
                    if self.history:
                        try:
                            self.history.save_trigger(trigger)
                        except Exception:
                            pass
                    out.append(suggestion)
            except Exception:
                continue
        return out

    def _match(self, trigger: Trigger, context: dict[str, Any]) -> Optional[Suggestion]:
        cond = trigger.condition
        workspace = context.get("workspace") or {}
        project = context.get("project") or {}
        # Built-in triggers
        if cond == "git_dirty":
            if project.get("git_repo") and context.get("workspace"):
                return Suggestion(
                    category=trigger.category,
                    title="Uncommitted changes",
                    body=f"You haven't committed changes in {project.get('name') or project.get('path')}.",
                    priority=0.7,
                    confidence=0.8,
                    urgency=0.6,
                    context_relevance=0.9,
                    expected_usefulness=0.7,
                    metadata={"trigger": trigger.trigger_id},
                )
        if cond == "continue_project":
            if project.get("name") and workspace.get("active_application"):
                return Suggestion(
                    category=trigger.category,
                    title=f"Continue {project.get('name')}?",
                    body=f"You were working on {project.get('name')}.",
                    priority=0.6,
                    confidence=0.7,
                    urgency=0.4,
                    context_relevance=0.8,
                    expected_usefulness=0.8,
                    metadata={"trigger": trigger.trigger_id},
                )
        if cond == "habit_suggestion":
            habits = context.get("habits") or []
            if habits:
                habit = habits[0]
                return Suggestion(
                    category="habit",
                    title="Suggested workflow",
                    body=f"I noticed you often {habit.get('name')}. Want to run it?",
                    priority=0.5,
                    confidence=float(habit.get("confidence") or 0.5),
                    urgency=0.2,
                    context_relevance=0.7,
                    expected_usefulness=0.6,
                    metadata={"trigger": trigger.trigger_id, "habit": habit},
                )
        if cond == "rag_summarize":
            docs = context.get("rag") or []
            if docs:
                return Suggestion(
                    category="document",
                    title="Summarize recent documents?",
                    body=f"I found {len(docs)} related documents. Would you like a summary?",
                    priority=0.4,
                    confidence=0.6,
                    urgency=0.2,
                    context_relevance=0.7,
                    expected_usefulness=0.7,
                    metadata={"trigger": trigger.trigger_id},
                )
        return None
