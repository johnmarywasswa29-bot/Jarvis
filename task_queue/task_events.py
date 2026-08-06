"""Task events."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from typing import Any, Callable, Iterable


class TaskEventType(str, Enum):
    CREATED = "task_created"
    STARTED = "task_started"
    COMPLETED = "task_completed"
    FAILED = "task_failed"
    CANCELLED = "task_cancelled"
    RETRIED = "task_retried"
    PAUSED = "task_paused"
    RESUMED = "task_resumed"


@dataclass
class TaskEvent:
    event_type: TaskEventType
    task_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())


Subscriber = Callable[[TaskEvent], None]


class TaskEventBus:
    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, fn: Subscriber) -> None:
        if fn not in self._subscribers:
            self._subscribers.append(fn)

    def unsubscribe(self, fn: Subscriber) -> None:
        if fn in self._subscribers:
            self._subscribers.remove(fn)

    def publish(self, event: TaskEvent) -> None:
        for fn in list(self._subscribers):
            try:
                fn(event)
            except Exception:
                pass
