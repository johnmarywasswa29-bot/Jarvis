"""Goal class and enums."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from typing import Any, Optional


class GoalStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class GoalPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class PlanAttachment:
    plan_id: str
    plan_dict: dict[str, Any] = field(default_factory=dict)
    attached_at: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())


class Goal:
    def __init__(
        self,
        title: str,
        description: str = "",
        *,
        status: GoalStatus = GoalStatus.ACTIVE,
        priority: GoalPriority = GoalPriority.NORMAL,
        owner: str = "",
        category: str = "",
        tags: list[str] | None = None,
        notes: str = "",
        goal_id: str | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
        completed_at: str | None = None,
        progress: float = 0.0,
        depends_on: list[str] | None = None,
    ) -> None:
        self.id = goal_id or str(uuid.uuid4())
        self.title = title
        self.description = description
        self.status = GoalStatus(status) if isinstance(status, str) else status
        self.priority = GoalPriority(priority) if isinstance(priority, str) else priority
        self.created_at = created_at or datetime.now(UTC).replace(tzinfo=None).isoformat()
        self.updated_at = updated_at or self.created_at
        self.completed_at = completed_at
        self.progress = max(0.0, min(100.0, float(progress)))
        self.owner = owner
        self.category = category
        self.tags = list(tags or [])
        self.notes = notes
        self.depends_on = list(depends_on or [])
        self.plans: list[PlanAttachment] = []

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC).replace(tzinfo=None).isoformat()

    def attach_plan(self, plan: PlanAttachment) -> None:
        self.plans.append(plan)
        self.touch()

    def add_dependency(self, goal_id: str) -> None:
        if goal_id not in self.depends_on:
            self.depends_on.append(goal_id)
            self.touch()

    def remove_dependency(self, goal_id: str) -> None:
        if goal_id in self.depends_on:
            self.depends_on.remove(goal_id)
            self.touch()
