"""Event type enums bridging subsystems."""
from __future__ import annotations

from enum import Enum


class PluginEventType(str, Enum):
    PLUGIN_LOADED = "plugin.loaded"
    PLUGIN_ENABLED = "plugin.enabled"
    PLUGIN_DISABLED = "plugin.disabled"
    PLUGIN_ERROR = "plugin.error"


class TaskEventType(str, Enum):
    CREATED = "task_created"
    STARTED = "task_started"
    COMPLETED = "task_completed"
    FAILED = "task_failed"
    CANCELLED = "task_cancelled"
    RETRIED = "task_retried"
    PAUSED = "task_paused"
    RESUMED = "task_resumed"


class GoalEventType(str, Enum):
    CREATED = "goal_created"
    UPDATED = "goal_updated"
    PAUSED = "goal_paused"
    COMPLETED = "goal_completed"
    ARCHIVED = "goal_archived"
    DELETED = "goal_deleted"
