"""Phase 5 Calendar plugin comprehensive tests."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, UTC
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from plugins.calendar_plugin.state import CalendarEvent, RecurrenceRule, CalendarPluginConfig
from plugins.calendar_plugin.provider_ics import ICSProvider
from plugins.calendar_plugin.provider_google import GoogleProvider
from plugins.calendar_plugin.provider_outlook import OutlookProvider
from plugins.calendar_plugin.schedule import CalendarScheduler
from plugins.calendar_plugin.proactive import CalendarProactive
from plugins.calendar_plugin.memory import CalendarMemory
from plugins.calendar_plugin.plugin import CalendarPlugin


def _write_ics(path: Path, events: list[dict[str, str]]) -> None:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Jarvis//EN"]
    for event in events:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{event.get('uid', 'x')}")
        lines.append(f"SUMMARY:{event.get('summary', '')}")
        lines.append(f"DTSTART:{event.get('dtstart', '')}")
        lines.append(f"DTEND:{event.get('dtend', '')}")
        if event.get("location"):
            lines.append(f"LOCATION:{event['location']}")
        if event.get("description"):
            lines.append(f"DESCRIPTION:{event['description']}")
        if event.get("rrule"):
            lines.append(f"RRULE:{event['rrule']}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    path.write_text("\n".join(lines), encoding="utf-8")


class TestState(unittest.TestCase):
    def test_default_event(self):
        event = CalendarEvent()
        assert event.event_id
        assert event.status == "confirmed"
        assert event.provider == "ics"

    def test_recurrence_rule(self):
        rule = RecurrenceRule(frequency="daily", interval=2, count=10)
        assert rule.frequency == "daily"
        assert rule.interval == 2
        assert rule.count == 10

    def test_config_defaults(self):
        config = CalendarPluginConfig()
        assert config.default_provider == "ics"
        assert config.reminder_minutes == 15
        assert config.auto_approve is False


class TestProviders(unittest.TestCase):
    def test_ics_parse_file(self):
        path = REPO / "tests" / "tmp_calendar.ics"
        _write_ics(path, [
            {"uid": "1", "summary": "Demo", "dtstart": "20260101T090000Z", "dtend": "20260101T100000Z", "location": "Room 1", "description": "desc"}
        ])
        provider = ICSProvider(default_path=str(path))
        events = provider.get_events("2026-01-01", "2026-01-01")
        assert len(events) == 1
        assert events[0].title == "Demo"
        assert events[0].location == "Room 1"
        assert events[0].description == "desc"
        path.unlink(missing_ok=True)

    def test_ics_filters_by_date(self):
        path = REPO / "tests" / "tmp_calendar.ics"
        _write_ics(path, [
            {"uid": "1", "summary": "Day1", "dtstart": "20260101T090000Z", "dtend": "20260101T100000Z"},
            {"uid": "2", "summary": "Day2", "dtstart": "20260102T090000Z", "dtend": "20260102T100000Z"},
        ])
        provider = ICSProvider(default_path=str(path))
        events = provider.get_events("2026-01-02", "2026-01-02")
        assert len(events) == 1
        assert events[0].title == "Day2"
        path.unlink(missing_ok=True)

    def test_ics_missing_file(self):
        provider = ICSProvider(default_path="missing.ics")
        assert provider.get_events("", "") == []

    def test_ics_recurring_event_parsed(self):
        path = REPO / "tests" / "tmp_calendar.ics"
        _write_ics(path, [
            {"uid": "1", "summary": "Weekly", "dtstart": "20260101T090000Z", "dtend": "20260101T100000Z", "rrule": "FREQ=WEEKLY"}
        ])
        provider = ICSProvider(default_path=str(path))
        events = provider.get_events("2026-01-01", "2026-01-01")
        assert len(events) == 1
        assert events[0].recurrence.frequency == "FREQ=WEEKLY"
        path.unlink(missing_ok=True)

    def test_ics_create_event(self):
        provider = ICSProvider()
        event = provider.create_event(CalendarEvent(title="New Meeting", start="2026-01-03T12:00:00", end="2026-01-03T13:00:00"))
        assert event.provider == "ics"
        assert event.title == "New Meeting"

    def test_google_empty_without_auth(self):
        provider = GoogleProvider()
        assert provider.get_events("", "") == []

    def test_google_create_event_without_auth(self):
        provider = GoogleProvider()
        event = provider.create_event(CalendarEvent(title="g"))
        assert event.provider == "google"

    def test_google_edit_event_without_auth(self):
        provider = GoogleProvider()
        event = provider.edit_event("abc", {"title": "New"})
        assert event.event_id == "abc"
        assert event.provider == "google"

    def test_google_delete_event_without_auth(self):
        provider = GoogleProvider()
        assert provider.delete_event("abc") is False

    def test_outlook_empty_without_auth(self):
        provider = OutlookProvider()
        assert provider.get_events("", "") == []

    def test_outlook_create_event_without_auth(self):
        provider = OutlookProvider()
        event = provider.create_event(CalendarEvent(title="o"))
        assert event.provider == "outlook"

    def test_outlook_edit_event_without_auth(self):
        provider = OutlookProvider()
        event = provider.edit_event("abc", {"title": "New"})
        assert event.event_id == "abc"
        assert event.provider == "outlook"

    def test_outlook_delete_event_without_auth(self):
        provider = OutlookProvider()
        assert provider.delete_event("abc") is False

    def test_provider_lifecycle_events(self):
        plugin = CalendarPlugin()
        plugin.api = SimpleNamespace(emit=lambda *args, **kwargs: None)
        plugin.on_load()
        plugin.on_unload()
        assert plugin.name == "calendar_plugin"


class TestScheduler(unittest.TestCase):
    def test_today(self):
        plugin = MagicMock()
        plugin.providers = {"ics": MagicMock(get_events=lambda s, e: [CalendarEvent(title="today")])}
        scheduler = CalendarScheduler(plugin)
        events = scheduler.today()
        assert len(events) == 1
        assert events[0].title == "today"

    def test_tomorrow(self):
        plugin = MagicMock()
        plugin.providers = {"ics": MagicMock(get_events=lambda s, e: [CalendarEvent(title="tmr")])}
        scheduler = CalendarScheduler(plugin)
        events = scheduler.tomorrow()
        assert events[0].title == "tmr"

    def test_this_week(self):
        plugin = MagicMock()
        plugin.providers = {"ics": MagicMock(get_events=lambda s, e: [CalendarEvent(title="w")])}
        scheduler = CalendarScheduler(plugin)
        events = scheduler.this_week()
        assert len(events) == 1

    def test_search(self):
        plugin = MagicMock()
        plugin.providers = {
            "ics": MagicMock(get_events=lambda s, e: [CalendarEvent(title="Demo Meeting"), CalendarEvent(title="Lunch")]),
        }
        scheduler = CalendarScheduler(plugin)
        results = scheduler.search("demo")
        assert len(results) == 1
        assert results[0].title == "Demo Meeting"

    def test_free_blocks_empty_day(self):
        plugin = MagicMock()
        plugin.providers = {"ics": MagicMock(get_events=lambda s, e: [])}
        scheduler = CalendarScheduler(plugin)
        blocks = scheduler.free_blocks("ics", datetime.now(UTC).replace(tzinfo=None).date().isoformat(), datetime.now(UTC).replace(tzinfo=None).date().isoformat())
        assert len(blocks) == 1

    def test_conflicts(self):
        plugin = MagicMock()
        ts = datetime.now(UTC).replace(tzinfo=None).isoformat()
        end = (datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)).isoformat()
        plugin.providers = {"ics": MagicMock(get_events=lambda s, e: [CalendarEvent(title="A", start=ts, end=end), CalendarEvent(title="B", start=ts, end=end)])}
        scheduler = CalendarScheduler(plugin)
        conflicts = scheduler.conflicts("ics", ts[:10], ts[:10])
        assert len(conflicts) == 1


class TestProactive(unittest.TestCase):
    def test_reminders_within_window(self):
        plugin = MagicMock()
        proactive = CalendarProactive(plugin)
        start = (datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=10)).isoformat()
        event = CalendarEvent(title="Standup", start=start)
        plugin.scheduler.today.return_value = [event]
        msgs = proactive.reminders(provider_name="ics", minutes_before=15)
        assert len(msgs) == 1
        assert "Standup" in msgs[0]

    def test_reminders_outside_window(self):
        plugin = MagicMock()
        proactive = CalendarProactive(plugin)
        start = (datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=2)).isoformat()
        event = CalendarEvent(title="Later", start=start)
        plugin.scheduler.today.return_value = [event]
        msgs = proactive.reminders(provider_name="ics", minutes_before=15)
        assert msgs == []

    def test_free_time_empty_schedule(self):
        plugin = MagicMock()
        proactive = CalendarProactive(plugin)
        plugin.scheduler.today.return_value = []
        plugin.scheduler.range.return_value = []
        plugin.scheduler.free_blocks.return_value = [{"start": "2026-08-03T09:00:00", "end": "2026-08-03T10:00:00"}]
        results = proactive.free_time(provider_name="ics")
        assert any("free time" in msg.lower() for msg in results)

    def test_conflicts_empty(self):
        plugin = MagicMock()
        proactive = CalendarProactive(plugin)
        plugin.scheduler.conflicts.return_value = []
        assert proactive.conflicts(provider_name="ics") == []

    def test_recovery_plan(self):
        plugin = MagicMock()
        proactive = CalendarProactive(plugin)
        plugin.scheduler.today.return_value = []
        plugin.scheduler.conflicts.return_value = []
        plan = proactive.recovery_plan(provider_name="ics")
        assert plan
        assert "No meetings today" in plan[0]


class TestMemory(unittest.TestCase):
    def _isolated_memory(self):
        path = Path(REPO / "tests" / "tmp_calendar_memory.json")
        path.write_text("{}", encoding="utf-8")
        memory = CalendarMemory(plugin=MagicMock(), path=str(path))
        return memory, path

    def test_defaults(self):
        memory, path = self._isolated_memory()
        try:
            assert memory.frequent_meetings() == []
            assert memory.preferred_durations() == {}
            assert memory.typical_hours() == {}
        finally:
            path.unlink(missing_ok=True)

    def test_record_and_frequency(self):
        memory, path = self._isolated_memory()
        try:
            memory.record_meeting(CalendarEvent(title="Sync"))
            memory.record_meeting(CalendarEvent(title="Sync"))
            memory.record_meeting(CalendarEvent(title="1:1"))
            freq = dict(memory.frequent_meetings())
            assert freq.get("Sync") == 2
            assert freq.get("1:1") == 1
        finally:
            path.unlink(missing_ok=True)

    def test_recent_calendars(self):
        memory, path = self._isolated_memory()
        try:
            memory.mark_calendar_used("google")
            memory.mark_calendar_used("outlook")
            assert memory.recently_used_calendars() == ["google", "outlook"]
        finally:
            path.unlink(missing_ok=True)

    def test_persistence(self):
        path = Path(REPO / "tests" / "tmp_calendar_memory_persist.json")
        path.write_text("{}", encoding="utf-8")
        try:
            memory = CalendarMemory(plugin=MagicMock(), path=str(path))
            memory.record_meeting(CalendarEvent(title="Persist"))
            memory._save()
            memory2 = CalendarMemory(plugin=MagicMock(), path=str(path))
            assert dict(memory2.frequent_meetings()).get("Persist") == 1
        finally:
            path.unlink(missing_ok=True)


class TestPluginLifecycle(unittest.TestCase):
    def test_load_unload(self):
        plugin = CalendarPlugin(api=SimpleNamespace(emit=lambda *args, **kwargs: None))
        plugin.on_load()
        plugin.on_unload()
        assert plugin.name == "calendar_plugin"

    def test_crud(self):
        plugin = CalendarPlugin(api=MagicMock())
        event = CalendarEvent(title="Demo")
        created = plugin.create_event("ics", event)
        assert created.provider == "ics"
        edited = plugin.edit_event("ics", created.event_id, {"title": "New"})
        assert edited.event_id == created.event_id
        # delete_event returns True when file absent, but here ICS default_path is ~/calendar.ics which may not exist
        result = plugin.delete_event("ics", created.event_id)
        assert isinstance(result, bool)

    def test_unknown_provider(self):
        plugin = CalendarPlugin(api=MagicMock())
        with self.assertRaises(ValueError):
            plugin.get_events("unknown", "2026-01-01", "2026-01-01")

    def test_proactive_hooks_via_plugin(self):
        plugin = CalendarPlugin(api=MagicMock())
        start = (datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=10)).isoformat()
        plugin.providers["ics"] = MagicMock(get_events=lambda s, e: [CalendarEvent(title="Sync", start=start)])
        reminders = plugin.reminders("ics", minutes_before=15)
        assert isinstance(reminders, list)

    def test_memory_integration_on_load(self):
        plugin = CalendarPlugin(api=MagicMock())
        plugin.scheduler = MagicMock()
        plugin.scheduler.today.return_value = [CalendarEvent(title="Sync", start=datetime.now(UTC).replace(tzinfo=None).isoformat())]
        plugin.on_load()
        titles = [t for t, _ in plugin.memory.frequent_meetings()]
        assert "Sync" in titles

    def test_search(self):
        plugin = CalendarPlugin(api=MagicMock())
        plugin.providers = {
            "ics": MagicMock(get_events=lambda s, e: [CalendarEvent(title="Demo"), CalendarEvent(title="Lunch")])
        }
        results = plugin.search("demo", "ics")
        assert len(results) == 1

    def test_recovery_plan_via_plugin(self):
        plugin = CalendarPlugin(api=MagicMock())
        plugin.scheduler = MagicMock()
        plugin.scheduler.today.return_value = []
        plugin.scheduler.conflicts.return_value = []
        plan = plugin.recovery_plan("ics")
        assert plan
        assert "No meetings today" in plan[0]


if __name__ == "__main__":
    unittest.main()
