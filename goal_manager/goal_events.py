"""Goal events."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from typing import Any, Callable, Iterable


class GoalEventType(str, Enum):
    CREATED = "goal_created"
    UPDATED = "goal_updated"
    PAUSED = "goal_paused"
    COMPLETED = "goal_completed"
    ARCHIVED = "goal_archived"
    DELETED = "goal_deleted"


@dataclass
class GoalEvent:
    event_type: GoalEventType
    goal_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())


Subscriber = Callable[[GoalEvent], None]


class GoalEventBus:
    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, fn: Subscriber) -> None:
        if fn not in self._subscribers:
            self._subscribers.append(fn)

    def unsubscribe(self, fn: Subscriber) -> None:
        if fn in self._subscribers:
            self._subscribers.remove(fn)

    def publish(self, event: GoalEvent) -> None:
        for fn in list(self._subscribers):
            try:
                fn(event)
            except Exception:
                pass
