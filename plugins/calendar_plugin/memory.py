"""CalendarMemory: lightweight plugin-local memory for meetings and preferences."""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime
from typing import Any, Optional

from plugins.calendar_plugin.state import CalendarEvent


class CalendarMemory:
    def __init__(self, plugin: Any, path: Optional[str] = None) -> None:
        self.plugin = plugin
        self.path = path or os.path.join(os.path.dirname(__file__), "calendar_memory.json")
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        try:
            if os.path.exists(self.path):
                text = open(self.path, "r", encoding="utf-8").read()
                if text.strip():
                    self._data = json.loads(text)
        except Exception:
            self._data = {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            open(self.path, "w", encoding="utf-8").write(json.dumps(self._data, ensure_ascii=False, indent=2))
        except Exception:
            pass

    def record_meeting(self, event: CalendarEvent) -> None:
        try:
            titles = self._data.setdefault("titles", [])
            titles.append(event.title or "(untitled)")
            self._data["titles"] = titles[-200:]
            if event.start:
                start_dt = datetime.fromisoformat(event.start)
                self._data.setdefault("last_seen", {})[event.event_id or event.title] = start_dt.isoformat()
            self._save()
        except Exception:
            pass

    def record_search(self, query: str) -> None:
        try:
            queries = self._data.setdefault("searches", [])
            queries.append(query)
            self._data["searches"] = queries[-200:]
            self._save()
        except Exception:
            pass

    def frequent_meetings(self, limit: int = 20) -> list[tuple[str, int]]:
        try:
            counts = Counter(self._data.get("titles", []))
            return counts.most_common(limit)
        except Exception:
            return []

    def preferred_durations(self) -> dict[str, int]:
        try:
            return self._data.get("preferred_durations", {})
        except Exception:
            return {}

    def typical_hours(self) -> dict[str, str]:
        try:
            return self._data.get("typical_hours", {})
        except Exception:
            return {}

    def recently_used_calendars(self, limit: int = 10) -> list[str]:
        try:
            return self._data.get("recent_calendars", [])[-limit:]
        except Exception:
            return []

    def mark_calendar_used(self, provider_name: str) -> None:
        try:
            recent = self._data.setdefault("recent_calendars", [])
            recent.append(provider_name)
            self._data["recent_calendars"] = recent[-50:]
            self._save()
        except Exception:
            pass
