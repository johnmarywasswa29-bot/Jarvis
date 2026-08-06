"""CalendarProactive: meeting reminders, free time, conflicts, recovery plan."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, UTC
from typing import Optional

from plugins.calendar_plugin.state import CalendarEvent

logger = logging.getLogger(__name__)


class CalendarProactive:
    def __init__(self, plugin: object) -> None:
        self.plugin = plugin

    def reminders(self, provider_name: str = "ics", minutes_before: int = 15) -> list[str]:
        try:
            today = self.plugin.scheduler.today(provider_name=provider_name)
        except Exception:
            today = []
        results: list[str] = []
        now = datetime.now(UTC).replace(tzinfo=None)
        for event in today:
            try:
                start = datetime.fromisoformat(event.start)
                delta = (start - now).total_seconds() / 60.0
                if 0 <= delta <= minutes_before:
                    results.append(f"Meeting starts in {int(delta)} minutes: {event.title}")
            except Exception:
                pass
        return results

    def free_time(self, provider_name: str = "ics", target_hours: Optional[str] = None) -> list[str]:
        try:
            now = datetime.now(UTC).replace(tzinfo=None)
            start = now.date().isoformat()
            end = (now + timedelta(days=1)).date().isoformat()
            events = self.plugin.scheduler.range(provider_name, start, end)
            blocks = self.plugin.scheduler.free_blocks(provider_name, start, end)
        except Exception:
            blocks = []
        results: list[str] = []
        for block in blocks[:3]:
            try:
                b_start = datetime.fromisoformat(block["start"])
                b_end = datetime.fromisoformat(block["end"])
                duration_min = (b_end - b_start).total_seconds() / 60.0
                if duration_min > 0:
                    results.append(f"Free time {b_start.strftime('%H:%M')}-{b_end.strftime('%H:%M')}")
            except Exception:
                pass
        return results

    def conflicts(self, provider_name: str = "ics") -> list[str]:
        try:
            today = datetime.now(UTC).replace(tzinfo=None).date().isoformat()
            conflicts = self.plugin.scheduler.conflicts(provider_name, today, today)
        except Exception:
            conflicts = []
        return [f"Conflict detected between {item['events'][0]} and {item['events'][1]}" for item in conflicts]

    def recovery_plan(self, provider_name: str = "ics") -> list[str]:
        suggestions: list[str] = []
        try:
            today = self.plugin.scheduler.today(provider_name)
        except Exception:
            today = []
        if today:
            suggestions.append(f"You have {len(today)} meeting(s) today.")
        else:
            suggestions.append("No meetings today.")
        suggestions.extend(self.free_time(provider_name)[:3])
        suggestions.extend(self.conflicts(provider_name)[:3])
        return suggestions
