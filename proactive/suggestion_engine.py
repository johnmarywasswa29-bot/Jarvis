"""SuggestionEngine: ranks, filters, deduplicates, and rate-limits suggestions."""
from __future__ import annotations

import time
from typing import Any, Optional

from proactive.state import Suggestion


class DismissalMemory:
    def __init__(self, history: Any = None) -> None:
        self.history = history
        self._recent_dismissals: dict[str, float] = {}
        self._max_recent = 200

    def record_dismissal(self, suggestion: Suggestion) -> None:
        self._recent_dismissals[suggestion.suggestion_id] = time.time()
        if self.history:
            try:
                self.history.dismiss(suggestion.suggestion_id)
            except Exception:
                pass
        # keep bounded
        if len(self._recent_dismissals) > self._max_recent:
            self._recent_dismissals = dict(list(self._recent_dismissals.items())[-self._max_recent:])

    def is_dismissed(self, suggestion: Suggestion) -> bool:
        return suggestion.suggestion_id in self._recent_dismissals

    def decay_score(self, suggestion: Suggestion) -> float:
        if not self.is_dismissed(suggestion):
            return suggestion.priority
        last = self._recent_dismissals.get(suggestion.suggestion_id, 0)
        age = time.time() - last
        # reduce repeated suggestion weight by time decay
        return max(0.0, suggestion.priority * max(0.0, 1.0 - age / 86400.0))


class NotificationQueue:
    def __init__(self, max_size: int = 50) -> None:
        self.max_size = max_size
        self._items: list[dict[str, Any]] = []

    def enqueue(self, suggestion: Suggestion) -> None:
        self._items.append({"suggestion": suggestion, "queued_at": time.time()})
        if len(self._items) > self.max_size:
            self._items = self._items[-self.max_size:]

    def drain(self, limit: int = 5) -> list[Suggestion]:
        out = [item["suggestion"] for item in self._items[:limit]]
        self._items = self._items[limit:]
        return out

    def size(self) -> int:
        return len(self._items)


class SuggestionEngine:
    def __init__(self, dismissal: Optional[DismissalMemory] = None, max_queue: int = 50) -> None:
        self.dismissal = dismissal or DismissalMemory()
        self.queue = NotificationQueue(max_size=max_queue)
        self._last_user_suggestion_ts = 0.0
        self._user_suggestion_interval_s = 60.0

    def rank(self, suggestions: list[Suggestion]) -> list[Suggestion]:
        scored = []
        for s in suggestions:
            score = (
                0.35 * s.priority
                + 0.25 * s.confidence
                + 0.15 * s.urgency
                + 0.15 * s.context_relevance
                + 0.10 * s.expected_usefulness
            )
            if self.dismissal.is_dismissed(s):
                score *= 0.2
            scored.append((score, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored]

    def enqueue(self, suggestions: list[Suggestion]) -> list[Suggestion]:
        ranked = self.rank(suggestions)
        accepted = []
        for s in ranked:
            if self.dismissal.is_dismissed(s):
                continue
            if self.queue.size() >= self.queue.max_size:
                break
            self.queue.enqueue(s)
            accepted.append(s)
        return accepted

    def notify(self, limit: int = 3) -> list[Suggestion]:
        return self.queue.drain(limit)

    def record_user_suggestion_sent(self) -> None:
        self._last_user_suggestion_ts = time.time()

    def can_suggest(self) -> bool:
        return (time.time() - self._last_user_suggestion_ts) >= self._user_suggestion_interval_s

    def dismiss(self, suggestion: Suggestion) -> None:
        self.dismissal.record_dismissal(suggestion)
