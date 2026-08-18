"""PluginEvents: typed event bus."""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List

from plugins.sdk.state import PluginEvent, PluginEventType


EventHandler = Callable[[PluginEvent], None]


class PluginEvents:
    def __init__(self) -> None:
        self._listeners: Dict[str, List[EventHandler]] = {}
        self._lock = threading.RLock()

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        with self._lock:
            self._listeners.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        with self._lock:
            handlers = self._listeners.get(event_type, [])
            try:
                handlers.remove(handler)
            except ValueError:
                pass

    def publish(self, event: PluginEvent) -> None:
        with self._lock:
            handlers = list(self._listeners.get(event.event_type, []))
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                pass