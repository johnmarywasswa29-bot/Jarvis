import os, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from core.events import EventBus, Event, EventType, TelemetryManager, EventFilter

bus = EventBus()
bus.subscribe("user.message", lambda e: None)

t0 = time.perf_counter()
for _ in range(10000):
    bus.publish(Event(event_type=EventType.USER_MESSAGE, source="bench", payload={}))
publish_us = ((time.perf_counter() - t0) / 10000) * 1_000_000
print(f"publish_avg_us: {publish_us:.2f}")

tm = TelemetryManager()
t0 = time.perf_counter()
for i in range(5000):
    tm.record("mod", duration_ms=i % 5, success=i % 7 != 0)
    tm.record_retry("mod")
telemetry_us = ((time.perf_counter() - t0) / 5000) * 1_000_000
print(f"telemetry_avg_us: {telemetry_us:.2f}")

flt = EventFilter(types=[EventType.USER_MESSAGE], search="search")
matched = sum(1 for _ in range(5000) if flt.matches(Event(event_type=EventType.USER_MESSAGE, source="x", payload={"q": "search me"})))
print(f"filter_matched: {matched}")

print("BENCHMARK_OK")
