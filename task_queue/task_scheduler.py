"""Task scheduler: delayed execution support."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable
from typing import Any, Callable, Iterable


@dataclass
class ScheduledTask:
    task_id: str
    run_at: str
    callback: Callable[[str], None] = field(default=lambda tid: None)
    cancelled: bool = False


class TaskScheduler:
    def __init__(self) -> None:
        self._scheduled: list[ScheduledTask] = []
        self._lock = threading.RLock()

    def schedule(self, task_id: str, run_at: str, callback: Callable[[str], None] | None = None) -> None:
        with self._lock:
            self._scheduled.append(ScheduledTask(task_id=task_id, run_at=run_at, callback=callback or (lambda tid: None)))

    def cancel(self, task_id: str) -> None:
        with self._lock:
            for item in self._scheduled:
                if item.task_id == task_id:
                    item.cancelled = True

    def due(self) -> list[ScheduledTask]:
        with self._lock:
            now = TaskScheduler._now()
            due = [item for item in self._scheduled if not item.cancelled and item.run_at <= now]
            self._scheduled = [item for item in self._scheduled if not item.cancelled and item.run_at > now]
            return due

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S")
