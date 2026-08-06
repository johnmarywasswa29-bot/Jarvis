"""ICS provider: local .ics calendar support with robust parsing."""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Optional

from plugins.calendar_plugin.state import CalendarEvent


FOLD_PATTERN = re.compile(r"\r?\n[ \t]")


class ICSProvider:
    def __init__(self, default_path: Optional[str] = None) -> None:
        self.default_path = default_path or os.path.expanduser("~/calendar.ics")

    def _parse_datetime(self, value: str) -> str:
        value = value.strip().rstrip("Z")
        for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y%m%d"):
            try:
                dt = datetime.strptime(value, fmt)
                return dt.isoformat()
            except ValueError:
                pass
        return value

    def get_events(self, start: str, end: str) -> list[CalendarEvent]:
        events: list[CalendarEvent] = []
        if not os.path.exists(self.default_path):
            return events
        try:
            text = open(self.default_path, "r", encoding="utf-8", errors="ignore").read()
            text = FOLD_PATTERN.sub("", text)
            in_event = False
            current = CalendarEvent(provider="ics")
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if line == "BEGIN:VEVENT":
                    in_event = True
                    current = CalendarEvent(provider="ics")
                elif line == "END:VEVENT":
                    if in_event and current.title and current.start:
                        events.append(current)
                    in_event = False
                elif in_event:
                    if line.startswith("UID:"):
                        current.event_id = line.split(":", 1)[1]
                    elif line.startswith("SUMMARY:"):
                        current.title = line.split(":", 1)[1]
                    elif line.startswith("DTSTART:"):
                        current.start = self._parse_datetime(line.split(":", 1)[1])
                    elif line.startswith("DTEND:"):
                        current.end = self._parse_datetime(line.split(":", 1)[1])
                    elif line.startswith("LOCATION:"):
                        current.location = line.split(":", 1)[1]
                    elif line.startswith("DESCRIPTION:"):
                        current.description = line.split(":", 1)[1]
                    elif line.startswith("ATTENDEE;"):
                        current.attendees.append(line.split(":", 1)[1])
                    elif line.startswith("ATTENDEE:"):
                        current.attendees.append(line.split(":", 1)[1])
                    elif line.startswith("STATUS:"):
                        current.status = line.split(":", 1)[1]
                    elif line.startswith("RRULE:"):
                        current.recurrence.frequency = line.split(":", 1)[1]
        except Exception:
            pass

        if start and end:
            try:
                start_dt = datetime.fromisoformat(start)
                end_dt = datetime.fromisoformat(end)
                if len(start) == 10:
                    start_dt = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                if len(end) == 10:
                    end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
                filtered: list[CalendarEvent] = []
                for event in events:
                    try:
                        e_start = datetime.fromisoformat(event.start)
                        if start_dt <= e_start <= end_dt:
                            filtered.append(event)
                    except Exception:
                        pass
                return filtered
            except Exception:
                pass
        return events

    def create_event(self, event: CalendarEvent) -> CalendarEvent:
        event.provider = "ics"
        if not event.event_id:
            event.event_id = str(__import__("uuid").uuid4())
        return event

    def edit_event(self, event_id: str, updates: dict) -> CalendarEvent:
        event = CalendarEvent(event_id=event_id, provider="ics")
        for key, value in updates.items():
            if hasattr(event, key):
                setattr(event, key, value)
        return event

    def delete_event(self, event_id: str) -> bool:
        if not os.path.exists(self.default_path):
            return False
        try:
            text = open(self.default_path, "r", encoding="utf-8", errors="ignore").read()
            text = FOLD_PATTERN.sub("", text)
            out_lines: list[str] = []
            keep_lines: list[str] = []
            in_event = False
            keep = True
            current_uid = ""
            for line in text.splitlines():
                trimmed = line.strip()
                if trimmed == "BEGIN:VEVENT":
                    in_event = True
                    keep = True
                    keep_lines = []
                    current_uid = ""
                elif trimmed == "END:VEVENT":
                    if in_event and keep and current_uid != event_id:
                        out_lines.append("BEGIN:VEVENT")
                        out_lines.extend(keep_lines)
                        out_lines.append("END:VEVENT")
                    in_event = False
                elif in_event:
                    if trimmed.startswith("UID:"):
                        current_uid = trimmed.split(":", 1)[1]
                    if keep:
                        keep_lines.append(line)
                else:
                    out_lines.append(line)
            open(self.default_path, "w", encoding="utf-8").write("\n".join(out_lines))
            return True
        except Exception:
            return False
