import os, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from plugins.calendar_plugin.plugin import CalendarPlugin
from plugins.calendar_plugin.state import CalendarEvent
from unittest.mock import MagicMock

plugin = CalendarPlugin(api=MagicMock())

# Load benchmark
t0 = time.perf_counter()
plugin.on_load()
load_latency = time.perf_counter() - t0
print(f"load_latency_ms: {load_latency*1000:.2f}")

# Query benchmark
ics = plugin.providers["ics"]
path = REPO / "tests" / "bench_calendar.ics"
path.write_text(
    "BEGIN:VEVENT\nUID:1\nSUMMARY:Demo\nDTSTART:20260101T090000Z\nDTEND:20260101T100000Z\nEND:VEVENT\n",
    encoding="utf-8",
)
ics.default_path = str(path)
t0 = time.perf_counter()
for _ in range(1000):
    ics.get_events("2026-01-01", "2026-01-01")
query_latency = (time.perf_counter() - t0) / 1000
print(f"query_latency_avg_us: {query_latency*1_000_000:.2f}")

# Scheduler
t0 = time.perf_counter()
events = plugin.scheduler.today()
scheduler_latency = time.perf_counter() - t0
print(f"scheduler_latency_ms: {scheduler_latency*1000:.2f}")

# Proactive reminders using current API
plugin.providers["ics"] = MagicMock(get_events=lambda s, e: [CalendarEvent(title="Standup", start=(__import__('datetime').datetime.now(UTC).replace(tzinfo=None) + __import__('datetime').timedelta(minutes=10)).isoformat())])
t0 = time.perf_counter()
for _ in range(200):
    plugin.proactive.reminders(provider_name="ics", minutes_before=15)
reminder_latency = (time.perf_counter() - t0) / 200
print(f"reminder_latency_avg_ms: {reminder_latency*1000:.2f}")

path.unlink(missing_ok=True)
print("BENCHMARK_OK")
