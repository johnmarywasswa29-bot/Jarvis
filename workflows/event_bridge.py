"""Event bridge for the research→proposal→confirm→execute workflow."""
from __future__ import annotations

from typing import Any, Optional

from core.events.event_bus import EventBus
from core.events import Event, EventType


class WorkflowEventBridge:
    def __init__(self, bus: Optional[EventBus] = None) -> None:
        self.bus = bus or EventBus()

    def publish(self, event_type: EventType, payload: dict[str, Any]) -> None:
        try:
            event = Event(event_type=event_type, source="workflow", payload=payload)
            self.bus.publish(event)
        except Exception:
            pass

    def research_completed(self, findings: Any) -> None:
        self.publish(EventType.RESEARCH_COMPLETED, {"query": getattr(findings, "query", ""), "result_count": len(getattr(findings, "structured", []))})

    def proposal_created(self, proposal: Any) -> None:
        self.publish(EventType.TRADE_PROPOSED, {"proposal_id": getattr(proposal, "proposal_id", ""), "objective": getattr(proposal, "objective", ""), "risk_level": getattr(proposal, "risk_level", "").value if hasattr(getattr(proposal, "risk_level", ""), "value") else str(getattr(proposal, "risk_level", ""))})

    def proposal_validated(self, proposal: Any) -> None:
        self.publish(EventType.TRADE_VALIDATED, {"proposal_id": getattr(proposal, "proposal_id", ""), "status": getattr(proposal, "status", "").value if hasattr(getattr(proposal, "status", ""), "value") else str(getattr(proposal, "status", "")), "validation_errors": getattr(proposal, "validation_errors", [])})

    def confirmation_required(self, step: Any, proposal: Any) -> None:
        self.publish(EventType.TRADE_CONFIRMATION_REQUIRED, {"step_uuid": getattr(step, "uuid", ""), "proposal_id": getattr(proposal, "proposal_id", ""), "token": getattr(step, "confirmation_token", "")})

    def confirmation_result(self, step: Any, approved: bool) -> None:
        self.publish(EventType.TRADE_CONFIRMED if approved else EventType.TRADE_REJECTED, {"step_uuid": getattr(step, "uuid", ""), "status": getattr(step, "status", "").value if hasattr(getattr(step, "status", ""), "value") else str(getattr(step, "status", ""))})

    def executed(self, order: Any, result: Any) -> None:
        self.publish(EventType.TRADE_EXECUTED, {"order_id": getattr(order, "order_id", ""), "status": getattr(order, "status", "").value if hasattr(getattr(order, "status", ""), "value") else str(getattr(order, "status", "")), "filled_price": getattr(order, "filled_price", 0.0)})

    def closed(self, order: Any, outcome: dict[str, Any]) -> None:
        self.publish(EventType.TRADE_CLOSED, {"order_id": getattr(order, "order_id", ""), "realized_pnl": outcome.get("realized_pnl"), "fees": outcome.get("fees")})
