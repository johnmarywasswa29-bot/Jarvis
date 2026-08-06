"""Core event bus, publisher, subscriber, dispatcher, and filter."""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Pattern

from core.events import Event, EventType, Severity


Subscriber = Callable[[Event], None]


class EventFilter:
    def __init__(
        self,
        types: Optional[Iterable[EventType]] = None,
        sources: Optional[Iterable[str]] = None,
        targets: Optional[Iterable[str]] = None,
        min_severity: Severity = Severity.DEBUG,
        search: str = "",
        regex: Optional[Pattern[str]] = None,
    ) -> None:
        self.types = set(types or [])
        self.sources = set(sources or [])
        self.targets = set(targets or [])
        self.min_severity = min_severity
        self.search = search.lower()
        self.regex = regex

    def _payload_blob(self, event: Event) -> str:
        try:
            return json.dumps(event.payload, ensure_ascii=False).lower()
        except Exception:
            return str(event.payload).lower()

    def matches(self, event: Event) -> bool:
        if self.types and event.event_type not in self.types:
            return False
        if self.sources and event.source not in self.sources:
            return False
        if self.targets and (event.target or "") not in self.targets:
            return False
        severity_order = [Severity.DEBUG, Severity.INFO, Severity.WARNING, Severity.ERROR, Severity.CRITICAL]
        if severity_order.index(event.severity) < severity_order.index(self.min_severity):
            return False
        blob = self._payload_blob(event)
        if self.search and self.search not in blob:
            return False
        if self.regex is not None and not self.regex.search(blob):
            return False
        return True


class EventPublisher:
    def __init__(self, bus: "EventBus") -> None:
        self.bus = bus

    def publish(self, event: Event) -> None:
        self.bus.publish(event)


class EventSubscriber:
    def __init__(self, bus: "EventBus") -> None:
        self.bus = bus

    def subscribe(self, event_type: str, handler: Subscriber) -> None:
        self.bus.subscribe(event_type, handler)

    def unsubscribe(self, event_type: str, handler: Subscriber) -> None:
        self.bus.unsubscribe(event_type, handler)


class EventDispatcher:
    def __init__(self, bus: "EventBus", filter: Optional[EventFilter] = None) -> None:
        self.bus = bus
        self.filter = filter
        self.dispatched: list[Event] = []

    def dispatch(self, event: Event) -> list[Event]:
        if self.filter and not self.filter.matches(event):
            return []
        self.dispatched.append(event)
        self.bus.publish(event)
        return self.bus.processed


class EventBus:
    def __init__(self) -> None:
        self._listeners: Dict[str, List[Subscriber]] = {}
        self._lock = threading.RLock()
        self.processed: List[Event] = []
        self._metrics: Dict[str, int] = {}

    def _key(self, event_type: Any) -> str:
        if isinstance(event_type, EventType):
            return event_type.value
        return str(event_type)

    def subscribe(self, event_type: Any, handler: Subscriber) -> None:
        with self._lock:
            self._listeners.setdefault(self._key(event_type), []).append(handler)

    def unsubscribe(self, event_type: Any, handler: Subscriber) -> None:
        with self._lock:
            handlers = self._listeners.get(self._key(event_type), [])
            try:
                handlers.remove(handler)
            except ValueError:
                pass

    def publish(self, event: Event) -> None:
        start = time.perf_counter()
        with self._lock:
            handlers = list(self._listeners.get(self._key(event.event_type), []))
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                pass
        duration_ms = (time.perf_counter() - start) * 1000
        event.duration_ms = duration_ms
        with self._lock:
            self.processed.append(event)
            self._metrics[self._key(event.event_type)] = self._metrics.get(self._key(event.event_type), 0) + 1

    def reset(self) -> None:
        with self._lock:
            self.processed = []
            self._metrics = {}
