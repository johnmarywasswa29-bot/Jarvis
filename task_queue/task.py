"""Task model and enums."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from typing import Any, Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Task:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    goal_id: str = ""
    plan_id: str = ""
    step_id: str = ""
    title: str = ""
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.NORMAL
    created_at: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    scheduled_time: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    estimated_duration: float = 0.0
    actual_duration: float = 0.0
    depends_on: list[str] = field(default_factory=list)
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: Optional[str] = None

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC).replace(tzinfo=None).isoformat()
