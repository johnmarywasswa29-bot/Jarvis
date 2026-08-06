"""Calendar plugin state models."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RecurrenceRule:
    frequency: str = "none"
    interval: int = 1
    count: Optional[int] = None
    until: Optional[str] = None


@dataclass
class CalendarEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    start: str = ""
    end: str = ""
    location: str = ""
    description: str = ""
    attendees: list[str] = field(default_factory=list)
    status: str = "confirmed"
    recurrence: RecurrenceRule = field(default_factory=RecurrenceRule)
    provider: str = "ics"
    calendar_id: str = ""


@dataclass
class CalendarPluginConfig:
    default_provider: str = "ics"
    reminder_minutes: int = 15
    timezone: str = "UTC"
    auto_approve: bool = False
    max_results: int = 50
    working_hours_start: str = "09:00"
    working_hours_end: str = "17:00"
