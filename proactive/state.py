"""Proactive state models."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Optional


@dataclass
class Suggestion:
    suggestion_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    category: str = "workflow"
    title: str = ""
    body: str = ""
    priority: float = 0.5
    confidence: float = 0.5
    urgency: float = 0.0
    context_relevance: float = 0.5
    expected_usefulness: float = 0.5
    dismissed: bool = False
    dismissed_at: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())
    metadata: dict = field(default_factory=dict)


@dataclass
class Trigger:
    trigger_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = ""
    category: str = "workflow"
    condition: str = ""
    cooldown_s: float = 300.0
    last_fired: Optional[str] = None
    enabled: bool = True
    metadata: dict = field(default_factory=dict)


@dataclass
class NotificationQueueItem:
    item_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    suggestion: Optional[Suggestion] = None
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())
    delivered_at: Optional[str] = None
