"""P0 Central Event Bus & Telemetry System tests."""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from core.events.event_bus import EventBus, EventDispatcher, EventFilter, EventPublisher, EventSubscriber
from core.events import Event, EventType, Severity
from core.events.telemetry import TelemetryManager
from core.events.logger import EventLogger
from core.events.adapters import bridge_plugin_events, bridge_task_events, bridge_goal_events
from plugins.sdk.events import PluginEvents, PluginEvent
from task_queue.task_events import TaskEventBus, TaskEvent, TaskEventType
from goal_manager.goal_events import GoalEventBus, GoalEvent, GoalEventType


class TestEventModel(unittest.TestCase):
    def test_defaults(self):
        event = Event(event_type=EventType.USER_MESSAGE, source="voice")
        assert event.event_id
        assert event.source == "voice"
        assert event.payload == {}
        assert event.correlation_id is None
        assert event.success is True
        assert event.duration_ms is None
        assert event.target is None
        assert event.severity == Severity.INFO
        assert event.metadata == {}

    def test_to_dict_roundtrip(self):
        original = Event(
            event_type=EventType.TOOL_STARTED,
            source="tool_registry",
            payload={"tool": "search"},
            target="search",
            correlation_id="abc",
            severity=Severity.DEBUG,
            success=False,
            duration_ms=1.23,
            metadata={"version": "1"},
        )
        data = original.to_dict()
        restored = Event.from_dict(data)
        assert restored.event_type == original.event_type
        assert restored.source == original.source
        assert restored.payload == original.payload
        assert restored.target == original.target
        assert restored.correlation_id == original.correlation_id
        assert restored.severity == original.severity
        assert restored.success == original.success
        assert restored.duration_ms == original.duration_ms
        assert restored.metadata == original.metadata


class TestEventFilter(unittest.TestCase):
    def test_matches_type(self):
        flt = EventFilter(types=[EventType.USER_MESSAGE])
        assert flt.matches(Event(event_type=EventType.USER_MESSAGE, source="x")) is True
        assert flt.matches(Event(event_type=EventType.ASSISTANT_RESPONSE, source="x")) is False

    def test_matches_source(self):
        flt = EventFilter(sources=["voice"])
        assert flt.matches(Event(event_type=EventType.USER_MESSAGE, source="voice")) is True
        assert flt.matches(Event(event_type=EventType.USER_MESSAGE, source="text")) is False

    def test_matches_target(self):
        flt = EventFilter(targets=["rag"])
        assert flt.matches(Event(event_type=EventType.RAG_SEARCH_STARTED, source="brain", target="rag")) is True
        assert flt.matches(Event(event_type=EventType.RAG_SEARCH_STARTED, source="brain", target="memory")) is False

    def test_matches_severity(self):
        flt = EventFilter(min_severity=Severity.WARNING)
        assert flt.matches(Event(event_type=EventType.USER_MESSAGE, source="x", severity=Severity.ERROR)) is True
        assert flt.matches(Event(event_type=EventType.USER_MESSAGE, source="x", severity=Severity.INFO)) is False

    def test_matches_search(self):
        flt = EventFilter(search="search")
        assert flt.matches(Event(event_type=EventType.RAG_SEARCH_STARTED, source="brain", payload={"query": "search docs"})) is True
        assert flt.matches(Event(event_type=EventType.RAG_SEARCH_STARTED, source="brain", payload={"query": "find"})) is False

    def test_matches_regex(self):
        flt = EventFilter(regex=re.compile(r"doc"))
        assert flt.matches(Event(event_type=EventType.RAG_SEARCH_STARTED, source="brain", payload={"query": "document"})) is True
        assert flt.matches(Event(event_type=EventType.RAG_SEARCH_STARTED, source="brain", payload={"query": "search"})) is False


class TestEventBus(unittest.TestCase):
    def test_publish_subscribe(self):
        bus = EventBus()
        received = []
        bus.subscribe("user.message", lambda e: received.append(e))
        event = Event(event_type=EventType.USER_MESSAGE, source="voice", payload={"text": "hi"})
        bus.publish(event)
        assert len(received) == 1
        assert received[0].payload["text"] == "hi"

    def test_unsubscribe(self):
        bus = EventBus()
        received = []
        def h(e): received.append(e)
        bus.subscribe("user.message", h)
        bus.unsubscribe("user.message", h)
        bus.publish(Event(event_type=EventType.USER_MESSAGE, source="x", payload={}))
        assert received == []

    def test_multiple_subscribers(self):
        bus = EventBus()
        a, b = [], []
        bus.subscribe("user.message", lambda e: a.append(e))
        bus.subscribe("user.message", lambda e: b.append(e))
        bus.publish(Event(event_type=EventType.USER_MESSAGE, source="x", payload={}))
        assert len(a) == 1 and len(b) == 1

    def test_failing_subscriber_is_isolated(self):
        bus = EventBus()
        received = []
        def bad(e): raise RuntimeError("boom")
        def good(e): received.append(e)
        bus.subscribe("user.message", bad)
        bus.subscribe("user.message", good)
        bus.publish(Event(event_type=EventType.USER_MESSAGE, source="x", payload={}))
        assert len(received) == 1

    def test_processed_and_metrics(self):
        bus = EventBus()
        bus.subscribe("user.message", lambda e: None)
        bus.publish(Event(event_type=EventType.USER_MESSAGE, source="voice", payload={}))
        assert len(bus.processed) == 1
        assert bus._metrics[EventType.USER_MESSAGE.value] == 1
        bus.reset()
        assert bus.processed == []
        assert bus._metrics == {}

    def test_duration_recorded(self):
        bus = EventBus()
        def slow(e):
            time.sleep(0.01)
        bus.subscribe("user.message", slow)
        event = Event(event_type=EventType.USER_MESSAGE, source="x", payload={})
        bus.publish(event)
        assert event.duration_ms is not None
        assert event.duration_ms >= 5.0

    def test_thread_safety(self):
        bus = EventBus()
        received = []
        bus.subscribe("user.message", lambda e: received.append(e))
        def publish_many():
            for i in range(500):
                bus.publish(Event(event_type=EventType.USER_MESSAGE, source="t", payload={"i": i}))
        threads = [threading.Thread(target=publish_many) for _ in range(4)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert len(received) == 2000

    def test_subscribe_after_publish(self):
        bus = EventBus()
        bus.publish(Event(event_type=EventType.USER_MESSAGE, source="x", payload={}))
        received = []
        bus.subscribe("user.message", lambda e: received.append(e))
        assert len(received) == 0


class TestPublisherSubscriberDispatcher(unittest.TestCase):
    def test_publisher(self):
        bus = EventBus()
        pub = EventPublisher(bus)
        received = []
        bus.subscribe("user.message", lambda e: received.append(e))
        pub.publish(Event(event_type=EventType.USER_MESSAGE, source="x", payload={}))
        assert len(received) == 1

    def test_subscriber(self):
        bus = EventBus()
        sub = EventSubscriber(bus)
        received = []
        handler = lambda e: received.append(e)
        sub.subscribe("user.message", handler)
        bus.publish(Event(event_type=EventType.USER_MESSAGE, source="x", payload={}))
        assert len(received) == 1
        sub.unsubscribe("user.message", handler)
        bus.publish(Event(event_type=EventType.USER_MESSAGE, source="x", payload={}))
        assert len(received) == 1

    def test_dispatcher_filter(self):
        bus = EventBus()
        flt = EventFilter(types=[EventType.USER_MESSAGE])
        dispatcher = EventDispatcher(bus, filter=flt)
        received = []
        bus.subscribe("user.message", lambda e: received.append(e))
        dispatcher.dispatch(Event(event_type=EventType.USER_MESSAGE, source="x", payload={}))
        assert len(dispatcher.dispatched) == 1
        dispatcher.dispatch(Event(event_type=EventType.ASSISTANT_RESPONSE, source="x", payload={}))
        assert len(dispatcher.dispatched) == 1
        assert len(received) == 1


class TestTelemetry(unittest.TestCase):
    def test_record_and_snapshot(self):
        tm = TelemetryManager()
        tm.record("memory", duration_ms=1.2, success=True)
        tm.record("memory", duration_ms=2.4, success=False)
        tm.record_retry("memory")
        snap = tm.snapshot()
        assert "memory" in snap
        assert snap["memory"]["event_count"] == 2
        assert snap["memory"]["failure_count"] == 1
        assert snap["memory"]["failure_rate"] == 0.5
        assert snap["memory"]["retry_count"] == 1
        assert abs(snap["memory"]["avg_latency_ms"] - 1.8) < 1e-9

    def test_multiple_modules(self):
        tm = TelemetryManager()
        tm.record("rag", duration_ms=5.0)
        tm.record("workflow", duration_ms=2.0)
        snap = tm.snapshot()
        assert "rag" in snap and "workflow" in snap

    def test_reset(self):
        tm = TelemetryManager()
        tm.record("x")
        tm.reset()
        assert tm.snapshot() == {}


class TestEventLogger(unittest.TestCase):
    def test_log_and_tail(self):
        path = REPO / "tests" / "tmp_events.jsonl"
        try:
            path.write_text("", encoding="utf-8")
            logger = EventLogger(log_dir=str(REPO / "tests"))
            logger._file_path = str(path)
            logger.log(Event(event_type=EventType.USER_MESSAGE, source="voice", payload={"text": "hi"}))
            tail = logger.tail(10)
            assert len(tail) == 1
            assert tail[0]["source"] == "voice"
        finally:
            path.unlink(missing_ok=True)

    def test_query(self):
        path = REPO / "tests" / "tmp_events_query.jsonl"
        try:
            path.write_text("", encoding="utf-8")
            logger = EventLogger(log_dir=str(REPO / "tests"))
            logger._file_path = str(path)
            logger.log(Event(event_type=EventType.USER_MESSAGE, source="voice", payload={"text": "hello"}))
            logger.log(Event(event_type=EventType.ASSISTANT_RESPONSE, source="brain", payload={"text": "ok"}))
            results = logger.query(types=[EventType.USER_MESSAGE.value])
            assert len(results) == 1
            assert results[0]["source"] == "voice"
        finally:
            path.unlink(missing_ok=True)


class TestAdapters(unittest.TestCase):
    def test_plugin_bridge(self):
        bus = EventBus()
        received = []
        bus.subscribe("plugin.loaded", lambda e: received.append(e))
        legacy = PluginEvents()
        cleanup = bridge_plugin_events(bus, legacy)
        legacy.publish(PluginEvent(event_type="plugin_loaded", data={"plugin_id": "p"}, plugin_id="p"))
        assert len(received) == 1
        assert received[0].source == "plugin_sdk"
        assert received[0].metadata.get("plugin_id") == "p"
        cleanup()

    def test_plugin_bridge_all_lifecycle_events(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.PLUGIN_LOADED, lambda e, et="plugin.loaded": received.append((et, e)))
        bus.subscribe(EventType.PLUGIN_ENABLED, lambda e, et="plugin.enabled": received.append((et, e)))
        bus.subscribe(EventType.PLUGIN_DISABLED, lambda e, et="plugin.disabled": received.append((et, e)))
        bus.subscribe(EventType.PLUGIN_RELOADED, lambda e, et="plugin.reloaded": received.append((et, e)))
        bus.subscribe(EventType.PLUGIN_UNLOADED, lambda e, et="plugin.unloaded": received.append((et, e)))
        bus.subscribe(EventType.PLUGIN_ERROR, lambda e, et="plugin.error": received.append((et, e)))
        legacy = PluginEvents()
        cleanup = bridge_plugin_events(bus, legacy)
        for et in [
            "plugin_loaded",
            "plugin_enabled",
            "plugin_disabled",
            "plugin_reloaded",
            "plugin_unloaded",
            "plugin_error",
            "plugin_installed",
            "plugin_updated",
            "plugin_uninstalled",
        ]:
            legacy.publish(PluginEvent(event_type=et, data={"plugin_id": "p"}, plugin_id="p"))
        assert len(received) == 9
        assert all(e.source == "plugin_sdk" for _, e in received)
        assert all(e.metadata.get("plugin_id") == "p" for _, e in received)
        cleanup()

    def test_plugin_bridge_isolation(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.PLUGIN_ERROR, lambda e: received.append(e))
        legacy = PluginEvents()
        cleanup = bridge_plugin_events(bus, legacy)
        def bad(e): raise RuntimeError("boom")
        legacy.subscribe("plugin_loaded", bad)
        legacy.publish(PluginEvent(event_type="plugin_error", data={"plugin_id": "p"}, plugin_id="p"))
        assert len(received) == 1
        cleanup()

    def test_plugin_bridge_unsubscribe(self):
        bus = EventBus()
        received = []
        bus.subscribe("plugin.loaded", lambda e: received.append(e))
        legacy = PluginEvents()
        cleanup = bridge_plugin_events(bus, legacy)
        legacy.publish(PluginEvent(event_type="plugin_loaded", data={"plugin_id": "p"}, plugin_id="p"))
        assert len(received) == 1
        cleanup()
        legacy.publish(PluginEvent(event_type="plugin_loaded", data={"plugin_id": "p"}, plugin_id="p"))
        assert len(received) == 1

    def test_plugin_bridge_with_telemetry_and_logger(self):
        path = REPO / "tests" / "tmp_plugin_events.jsonl"
        try:
            path.write_text("", encoding="utf-8")
            logger = EventLogger(log_dir=str(REPO / "tests"))
            logger._file_path = str(path)
            tm = TelemetryManager()
            received = []
            bus = EventBus()
            bus.subscribe("plugin.loaded", lambda e: (received.append(e), tm.record("plugin", duration_ms=1.0), logger.log(e)))
            legacy = PluginEvents()
            cleanup = bridge_plugin_events(bus, legacy)
            legacy.publish(PluginEvent(event_type="plugin_loaded", data={"plugin_id": "p"}, plugin_id="p"))
            assert len(received) == 1
            assert tm.snapshot()["plugin"]["event_count"] == 1
            assert len(logger.tail(10)) == 1
            cleanup()
        finally:
            path.unlink(missing_ok=True)

    def test_task_bridge(self):
        bus = EventBus()
        received = []
        bus.subscribe("custom", lambda e: received.append(e))
        legacy = TaskEventBus()
        bridge_task_events(bus, legacy)
        legacy.publish(TaskEvent(event_type=TaskEventType.CREATED, task_id="t1"))
        assert len(received) == 1
        assert received[0].source == "task_queue"

    def test_goal_bridge(self):
        bus = EventBus()
        received = []
        bus.subscribe("custom", lambda e: received.append(e))
        legacy = GoalEventBus()
        bridge_goal_events(bus, legacy)
        legacy.publish(GoalEvent(event_type=GoalEventType.CREATED, goal_id="g1"))
        assert len(received) == 1
        assert received[0].source == "goal_manager"


class TestPerformance(unittest.TestCase):
    def test_publish_latency(self):
        bus = EventBus()
        bus.subscribe("user.message", lambda e: None)
        start = time.perf_counter()
        for _ in range(1000):
            bus.publish(Event(event_type=EventType.USER_MESSAGE, source="bench", payload={}))
        elapsed_us = ((time.perf_counter() - start) / 1000) * 1_000_000
        assert elapsed_us < 500, f"publish latency too high: {elapsed_us:.2f} us"

    def test_dispatch_latency(self):
        bus = EventBus()
        dispatcher = EventDispatcher(bus)
        start = time.perf_counter()
        for _ in range(200):
            dispatcher.dispatch(Event(event_type=EventType.USER_MESSAGE, source="bench", payload={}))
        elapsed_us = ((time.perf_counter() - start) / 200) * 1_000_000
        assert elapsed_us < 1000, f"dispatch latency too high: {elapsed_us:.2f} us"

    def test_memory_overhead(self):
        bus = EventBus()
        for i in range(1000):
            bus.publish(Event(event_type=EventType.CUSTOM, source=f"s{i}", payload={"i": i}))
        assert len(bus.processed) == 1000


if __name__ == "__main__":
    unittest.main()
