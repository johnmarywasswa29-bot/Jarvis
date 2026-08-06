"""Adapters bridging legacy event systems into the central event bus."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from core.events import Event, EventBus, EventType

logger = logging.getLogger(__name__)

_PLUGIN_EVENT_TYPES = [
    "plugin_loaded",
    "plugin_enabled",
    "plugin_disabled",
    "plugin_reloaded",
    "plugin_unloaded",
    "plugin_error",
    "plugin_installed",
    "plugin_updated",
    "plugin_uninstalled",
]

_LEGACY_TYPE_MAP: Dict[str, EventType] = {
    "plugin_loaded": EventType.PLUGIN_LOADED,
    "plugin_enabled": EventType.PLUGIN_ENABLED,
    "plugin_disabled": EventType.PLUGIN_DISABLED,
    "plugin_reloaded": EventType.PLUGIN_RELOADED,
    "plugin_unloaded": EventType.PLUGIN_UNLOADED,
    "plugin_error": EventType.PLUGIN_ERROR,
    "plugin_installed": EventType.PLUGIN_LOADED,
    "plugin_uninstalled": EventType.PLUGIN_UNLOADED,
    "plugin_updated": EventType.PLUGIN_RELOADED,
    "task_created": EventType.CUSTOM,
    "task_started": EventType.CUSTOM,
    "task_completed": EventType.CUSTOM,
    "task_failed": EventType.CUSTOM,
    "task_cancelled": EventType.CUSTOM,
    "task_retried": EventType.CUSTOM,
    "task_paused": EventType.CUSTOM,
    "task_resumed": EventType.CUSTOM,
    "goal_created": EventType.CUSTOM,
    "goal_updated": EventType.CUSTOM,
    "goal_paused": EventType.CUSTOM,
    "goal_completed": EventType.CUSTOM,
    "goal_archived": EventType.CUSTOM,
    "goal_deleted": EventType.CUSTOM,
}


def _legacy_to_event(source: str, event: object) -> Event:
    payload: Dict[str, Any] = {}
    legacy_type = getattr(event, "event_type", EventType.CUSTOM)
    if hasattr(event, "payload"):
        payload = event.payload  # type: ignore[union-attr]
    elif hasattr(event, "data"):
        payload = event.data  # type: ignore[union-attr]
    mapped = EventType.CUSTOM
    try:
        if isinstance(legacy_type, EventType):
            mapped = legacy_type
        elif isinstance(legacy_type, str):
            mapped = EventType(legacy_type) if legacy_type in EventType._value2member_map_ else _LEGACY_TYPE_MAP.get(legacy_type, EventType.CUSTOM)
    except Exception:
        pass
    return Event(event_type=mapped, source=source, payload=payload, metadata={"legacy_type": str(legacy_type)})


def bridge_plugin_events(bus: EventBus, legacy: Any) -> Callable[..., None]:
    handlers: list[tuple[str, Callable[..., None]]] = []

    def make_handler(event_type: str) -> Callable[..., None]:
        mapped = _LEGACY_TYPE_MAP.get(event_type, EventType.CUSTOM)

        def handler(event: Any) -> None:
            try:
                payload = event.data if hasattr(event, "data") else getattr(event, "payload", {})
                plugin_id = getattr(event, "plugin_id", None)
                bus.publish(Event(event_type=mapped, source="plugin_sdk", payload=payload, metadata={"plugin_id": plugin_id}))
            except Exception as exc:
                logger.debug("Plugin bridge error for %s: %s", event_type, exc)

        return handler

    for event_type in _PLUGIN_EVENT_TYPES:
        try:
            h = make_handler(event_type)
            legacy.subscribe(event_type, h)
            handlers.append((event_type, h))
        except Exception as exc:
            logger.debug("Failed to bridge plugin event %s: %s", event_type, exc)

    def unsubscribe_all() -> None:
        for event_type, h in handlers:
            try:
                legacy.unsubscribe(event_type, h)
            except Exception:
                pass

    return unsubscribe_all


def bridge_task_events(bus: EventBus, legacy: Any) -> Callable[..., None]:
    def handler(event: object) -> None:
        mapped = _legacy_to_event("task_queue", event)
        bus.publish(mapped)
    legacy.subscribe(handler)
    return handler


def bridge_goal_events(bus: EventBus, legacy: Any) -> Callable[..., None]:
    def handler(event: object) -> None:
        mapped = _legacy_to_event("goal_manager", event)
        bus.publish(mapped)
    legacy.subscribe(handler)
    return handler
