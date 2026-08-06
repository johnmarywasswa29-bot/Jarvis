"""PluginAPI: stable facade exposed to plugins."""
from __future__ import annotations

from typing import Any, Optional


class PluginAPI:
    def __init__(
        self,
        memory: Any = None,
        rag: Any = None,
        workflow_manager: Any = None,
        workspace_manager: Any = None,
        intent_analyzer: Any = None,
        habit_manager: Any = None,
        tool_registry: Any = None,
        notification: Any = None,
        events: Any = None,
    ) -> None:
        self.memory = memory
        self.rag = rag
        self.workflow_manager = workflow_manager
        self.workspace_manager = workspace_manager
        self.intent_analyzer = intent_analyzer
        self.habit_manager = habit_manager
        self.tool_registry = tool_registry
        self.notification = notification
        self.events = events

    def emit(self, event_type: str, data: Optional[dict] = None) -> None:
        if self.events is None:
            return
        event = __import__("plugins.sdk.state", fromlist=["PluginEvent"]).PluginEvent(
            event_type=event_type, data=data or {}
        )
        self.events.publish(event)
