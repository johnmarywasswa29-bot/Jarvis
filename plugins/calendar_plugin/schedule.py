"""CalendarScheduler: today/tomorrow/week/conflicts/free time/search."""
from __future__ import annotations

from datetime import datetime, timedelta, UTC
from typing import Optional

from plugins.calendar_plugin.state import CalendarEvent


class CalendarScheduler:
    def __init__(self, plugin: object) -> None:
        self.plugin = plugin

    def _provider(self, name: str) -> object:
        return self.plugin.providers[name]

    def today(self, provider_name: str = "ics") -> list[CalendarEvent]:
        now = datetime.now(UTC).replace(tzinfo=None).date().isoformat()
        return self._provider(provider_name).get_events(now, now)

    def tomorrow(self, provider_name: str = "ics") -> list[CalendarEvent]:
        tomorrow = (datetime.now(UTC).replace(tzinfo=None).date() + timedelta(days=1)).isoformat()
        return self._provider(provider_name).get_events(tomorrow, tomorrow)

    def this_week(self, provider_name: str = "ics") -> list[CalendarEvent]:
        now = datetime.now(UTC).replace(tzinfo=None).date()
        start = now.isoformat()
        end = (now + timedelta(days=7)).isoformat()
        return self._provider(provider_name).get_events(start, end)

    def range(self, provider_name: str, start: str, end: str) -> list[CalendarEvent]:
        return self._provider(provider_name).get_events(start, end)

    def search(self, query: str, provider_name: str = "ics") -> list[CalendarEvent]:
        all_events = self._provider(provider_name).get_events("", "")
        q = query.lower()
        return [e for e in all_events if q in e.title.lower() or q in (e.description or "").lower()]

    def free_blocks(self, provider_name: str = "ics", start: str = "", end: str = "") -> list[dict]:
        if not start or not end:
            start_dt = datetime.now(UTC).replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = start_dt + timedelta(days=1)
        else:
            try:
                start_dt = datetime.fromisoformat(start)
                end_dt = datetime.fromisoformat(end)
            except Exception:
                return []
        events = self._provider(provider_name).get_events(
            start_dt.date().isoformat(), end_dt.date().isoformat()
        )
        busy = []
        for event in events:
            try:
                e_start = datetime.fromisoformat(event.start)
                e_end = datetime.fromisoformat(event.end or event.start)
                if e_end <= start_dt or e_start >= end_dt:
                    continue
                busy.append((max(e_start, start_dt), min(e_end, end_dt)))
            except Exception:
                pass
        if not busy:
            return [{"start": start_dt.isoformat(), "end": end_dt.isoformat()}]
        busy.sort()
        blocks = []
        current = start_dt
        for b_start, b_end in busy:
            if b_start > current:
                blocks.append({"start": current.isoformat(), "end": b_start.isoformat()})
            current = max(current, b_end)
        if current < end_dt:
            blocks.append({"start": current.isoformat(), "end": end_dt.isoformat()})
        return blocks

    def conflicts(self, provider_name: str = "ics", start: str = "", end: str = "") -> list[dict]:
        if not start or not end:
            start = datetime.now(UTC).replace(tzinfo=None).date().isoformat()
            end = start
        events = self._provider(provider_name).get_events(start, end)
        times = []
        conflicts = []
        for event in events:
            try:
                start_dt = datetime.fromisoformat(event.start)
                end_dt = datetime.fromisoformat(event.end or event.start)
                times.append((start_dt, end_dt, event.event_id or event.title))
            except Exception:
                pass
        times.sort(key=lambda x: (x[0], x[1], x[2]))
        for i in range(len(times)):
            for j in range(i + 1, len(times)):
                s1, e1, _ = times[i]
                s2, e2, _ = times[j]
                if s2 < e1:
                    conflicts.append({"events": [times[i][2], times[j][2]], "overlap": True})
        return conflicts
