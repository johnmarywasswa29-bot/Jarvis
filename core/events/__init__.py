"""Central Event Model."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from typing import Any, Optional


class Severity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class EventType(str, Enum):
    APP_STARTED = "app.started"
    APP_CLOSED = "app.closed"
    USER_MESSAGE = "user.message"
    ASSISTANT_RESPONSE = "assistant.response"
    INTENT_DETECTED = "intent.detected"
    INTENT_CONFIDENCE_CALCULATED = "intent.confidence_calculated"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_STEP_STARTED = "workflow.step.started"
    WORKFLOW_STEP_COMPLETED = "workflow.step.completed"
    WORKFLOW_FAILED = "workflow.failed"
    RESEARCH_COMPLETED = "research.completed"
    RESEARCH_FAILED = "research.failed"
    PROPOSAL_CREATED = "proposal.created"
    PROPOSAL_VALIDATED = "proposal.validated"
    PROPOSAL_REJECTED = "proposal.rejected"
    CONFIRMATION_REQUIRED = "confirmation.required"
    CONFIRMATION_APPROVED = "confirmation.approved"
    CONFIRMATION_REJECTED = "confirmation.rejected"
    EXECUTION_STARTED = "execution.started"
    EXECUTION_COMPLETED = "execution.completed"
    TRADE_PROPOSED = "trade.proposed"
    TRADE_VALIDATED = "trade.validated"
    TRADE_CONFIRMATION_REQUIRED = "trade.confirmation_required"
    TRADE_CONFIRMED = "trade.confirmed"
    TRADE_REJECTED = "trade.rejected"
    TRADE_EXECUTED = "trade.executed"
    TRADE_CLOSED = "trade.closed"
    MEMORY_ADDED = "memory.added"
    MEMORY_UPDATED = "memory.updated"
    MEMORY_RETRIEVED = "memory.retrieved"
    HABIT_LEARNED = "habit.learned"
    HABIT_TRIGGERED = "habit.triggered"
    WORKSPACE_CHANGED = "workspace.changed"
    WORKSPACE_SNAPSHOT_CREATED = "workspace.snapshot_created"
    RAG_SEARCH_STARTED = "rag.search.started"
    RAG_SEARCH_COMPLETED = "rag.search.completed"
    PLUGIN_LOADED = "plugin.loaded"
    PLUGIN_ENABLED = "plugin.enabled"
    PLUGIN_DISABLED = "plugin.disabled"
    PLUGIN_RELOADED = "plugin.reloaded"
    PLUGIN_UNLOADED = "plugin.unloaded"
    PLUGIN_ERROR = "plugin.error"
    CALENDAR_EVENT_CREATED = "calendar.event.created"
    CALENDAR_REMINDER_FIRED = "calendar.reminder.fired"
    PROACTIVE_SUGGESTION_GENERATED = "proactive.suggestion.generated"
    ERROR_LOGGED = "log.error"
    WARNING_LOGGED = "log.warning"
    CUSTOM = "custom"
    # AgentLoop (bounded execution/recovery orchestration)
    AGENT_ITERATION_STARTED = "agent.iteration.started"
    AGENT_EXECUTION_COMPLETED = "agent.execution.completed"
    AGENT_VERIFICATION_COMPLETED = "agent.verification.completed"
    AGENT_REPLAN_COMPLETED = "agent.replan.completed"
    AGENT_COMPLETED = "agent.completed"
    AGENT_ABORTED = "agent.aborted"


@dataclass
class Event:
    event_type: EventType
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())
    target: Optional[str] = None
    correlation_id: Optional[str] = None
    severity: Severity = Severity.INFO
    success: bool = True
    duration_ms: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type.value if isinstance(self.event_type, EventType) else str(self.event_type),
            "source": self.source,
            "target": self.target,
            "payload": self.payload,
            "correlation_id": self.correlation_id,
            "severity": self.severity.value if isinstance(self.severity, Severity) else str(self.severity),
            "success": self.success,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        return cls(
            event_type=EventType(data["event_type"]) if data.get("event_type") else EventType.CUSTOM,
            source=data.get("source", ""),
            payload=data.get("payload", {}),
            event_id=data.get("event_id", ""),
            timestamp=data.get("timestamp", ""),
            target=data.get("target"),
            correlation_id=data.get("correlation_id"),
            severity=Severity(data.get("severity", Severity.INFO)) if data.get("severity") else Severity.INFO,
            success=data.get("success", True),
            duration_ms=data.get("duration_ms"),
            metadata=data.get("metadata", {}),
        )


from core.events.event_bus import EventBus, EventDispatcher, EventFilter, EventPublisher, EventSubscriber  # noqa: E402
from core.events.telemetry import TelemetryManager  # noqa: E402
from core.events.logger import EventLogger  # noqa: E402

__all__ = [
    "Severity",
    "EventType",
    "Event",
    "EventBus",
    "EventDispatcher",
    "EventFilter",
    "EventPublisher",
    "EventSubscriber",
    "TelemetryManager",
    "EventLogger",
]
