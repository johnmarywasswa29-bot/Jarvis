"""Workflow state and step models."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from typing import Any, Optional


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"


@dataclass
class WorkflowStep:
    uuid: str = field(default_factory=lambda: uuid.uuid4().hex)
    description: str = ""
    intent: str = ""
    tool: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    status: StepStatus = StepStatus.PENDING
    retry_count: int = 0
    execution_time_s: float = 0.0
    dependencies: list[str] = field(default_factory=list)
    result: Optional[dict[str, Any]] = None
    error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())
    requires_confirmation: bool = False
    confirmation_token: str = ""


@dataclass
class WorkflowState:
    workflow_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = ""
    description: str = ""
    steps: list[WorkflowStep] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    current_step_index: int = 0
    context: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)
