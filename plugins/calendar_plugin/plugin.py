"""Calendar plugin entry point."""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from plugins.calendar_plugin.provider_ics import ICSProvider
from plugins.calendar_plugin.provider_google import GoogleProvider
from plugins.calendar_plugin.provider_outlook import OutlookProvider
from plugins.calendar_plugin.state import CalendarEvent, CalendarPluginConfig
from plugins.calendar_plugin.schedule import CalendarScheduler
from plugins.calendar_plugin.proactive import CalendarProactive
from plugins.calendar_plugin.memory import CalendarMemory

logger = logging.getLogger(__name__)


class CalendarPlugin:
    name = "calendar_plugin"
    version = "1.0.0"

    def __init__(self, api: Any = None) -> None:
        self.api = api
        self.config = CalendarPluginConfig()
        self.providers: dict[str, object] = {
            "ics": ICSProvider(),
            "google": GoogleProvider(),
            "outlook": OutlookProvider(),
        }
        self.scheduler = CalendarScheduler(self)
        self.proactive = CalendarProactive(self)
        self.memory = CalendarMemory(
            plugin=self,
            path=os.path.join(os.path.dirname(__file__), "calendar_memory.json"),
        )

    def on_load(self) -> None:
        try:
            provider = self._resolve_provider()
            events = self.scheduler.today(provider_name=provider)
            for event in events:
                self.memory.record_meeting(event)
        except Exception:
            pass
        if self.api:
            try:
                self.api.emit("plugin_loaded", {"plugin_id": "calendar_plugin"})
            except Exception:
                pass

    def on_unload(self) -> None:
        if self.api:
            try:
                self.api.emit("plugin_unloaded", {"plugin_id": "calendar_plugin"})
            except Exception:
                pass

    def get_events(self, provider_name: str, start: str, end: str) -> list[CalendarEvent]:
        provider = self._resolve_provider(provider_name)
        events = self.providers[provider].get_events(start, end)
        for event in events:
            self.memory.record_meeting(event)
        return events

    def create_event(self, provider_name: str, event: CalendarEvent) -> CalendarEvent:
        provider = self._resolve_provider(provider_name)
        return self.providers[provider].create_event(event)

    def edit_event(self, provider_name: str, event_id: str, updates: dict) -> CalendarEvent:
        provider = self._resolve_provider(provider_name)
        return self.providers[provider].edit_event(event_id, updates)

    def delete_event(self, provider_name: str, event_id: str) -> bool:
        provider = self._resolve_provider(provider_name)
        return self.providers[provider].delete_event(event_id)

    def reminders(self, provider_name: Optional[str] = None, minutes_before: int = 15) -> list[str]:
        provider = self._resolve_provider(provider_name)
        return self.proactive.reminders(provider_name=provider, minutes_before=minutes_before)

    def free_time(self, provider_name: Optional[str] = None) -> list[str]:
        provider = self._resolve_provider(provider_name)
        return self.proactive.free_time(provider_name=provider)

    def conflicts(self, provider_name: Optional[str] = None) -> list[str]:
        provider = self._resolve_provider(provider_name)
        return self.proactive.conflicts(provider_name=provider)

    def recovery_plan(self, provider_name: Optional[str] = None) -> list[str]:
        provider = self._resolve_provider(provider_name)
        return self.proactive.recovery_plan(provider_name=provider)

    def search(self, query: str, provider_name: Optional[str] = None) -> list[CalendarEvent]:
        provider = self._resolve_provider(provider_name)
        return self.scheduler.search(query=query, provider_name=provider)

    def _resolve_provider(self, provider_name: Optional[str] = None) -> str:
        provider = provider_name or self.config.default_provider
        if provider not in self.providers:
            raise ValueError(f"Unknown provider: {provider}")
        self.memory.mark_calendar_used(provider)
        return provider
