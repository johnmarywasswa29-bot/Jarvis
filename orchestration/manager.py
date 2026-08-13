"""End-to-end orchestration layer connecting research, proposals, confirmation, execution."""
from __future__ import annotations

import time
from datetime import datetime, UTC
from typing import Any, Optional

from research.orchestrator import ResearchOrchestrator, ResearchFindings
from proposal.manager import ProposalManager
from proposal.state import Proposal, ProposalStatus, ProposedAction, SourceReference, ProposalRiskLevel
from proposal.validator import ProposalValidator
from workflows.state import WorkflowState, WorkflowStep, StepStatus
from workflows.executor import WorkflowExecutor
from workflows.event_bridge import WorkflowEventBridge
from simulation.state import SimulationState, Order, OrderStatus, OrderType, Direction
from simulation.engine import SimulationEngine


class Orchestrator:
    def __init__(
        self,
        rag: Any = None,
        web_search: Any = None,
        tool_registry: Any = None,
        event_bus: Any = None,
        *,
        max_order_value: float = 100000.0,
        max_position_size: float = 100000.0,
        max_open_positions: int = 10,
        allowed_instruments: Optional[list[str]] = None,
        max_daily_loss: float = 50000.0,
        max_trades_per_session: int = 100,
        min_available_balance: float = 1000.0,
        require_confirmation: bool = True,
    ) -> None:
        self.research = ResearchOrchestrator(rag=rag, web_search=web_search)
        self.proposal_manager = ProposalManager()
        self.executor = WorkflowExecutor(tool_registry=tool_registry)
        self.events = WorkflowEventBridge(bus=event_bus)
        self.simulation = SimulationEngine()
        self.risk_limits = {
            "max_order_value": max_order_value,
            "max_position_size": max_position_size,
            "max_open_positions": max_open_positions,
            "allowed_instruments": set(allowed_instruments or []),
            "max_daily_loss": max_daily_loss,
            "max_trades_per_session": max_trades_per_session,
            "min_available_balance": min_available_balance,
        }
        self.require_confirmation = require_confirmation
        self._session_trade_count = 0
        self._active_workflows: dict[str, WorkflowState] = {}

    def start_workflow(self, proposal: Proposal) -> WorkflowState:
        state = self.build_workflow(proposal)
        step = state.steps[0] if state.steps else None
        if step:
            self.events.confirmation_required(step, proposal)
        result = self.executor.execute(state)
        if result.status == StepStatus.WAITING_FOR_CONFIRMATION:
            self._active_workflows[proposal.proposal_id] = result
        return result

    def get_pending_confirmations(self) -> list[dict[str, Any]]:
        pending: list[dict[str, Any]] = []
        for proposal_id, state in list(self._active_workflows.items()):
            for step in state.steps:
                if step.status != StepStatus.WAITING_FOR_CONFIRMATION:
                    continue
                proposal = self.proposal_manager.get(proposal_id)
                pending.append(
                    {
                        "confirmation_id": step.confirmation_token,
                        "proposal_id": proposal_id,
                        "step_uuid": step.uuid,
                        "objective": getattr(proposal, "objective", state.description),
                        "risk_level": proposal.risk_level.value if proposal and hasattr(proposal.risk_level, "value") else str(getattr(proposal, "risk_level", "")),
                        "action_tool": step.tool or step.description,
                        "action_description": step.description,
                        "action_parameters": step.parameters,
                        "source_references": [
                            {
                                "source_type": sr.source_type,
                                "identifier": sr.identifier,
                                "excerpt": (sr.excerpt or "")[:200],
                            }
                            for sr in getattr(proposal, "source_references", [])
                        ],
                        "validation_errors": list(getattr(proposal, "validation_errors", [])),
                        "workflow_status": state.status.value if hasattr(state.status, "value") else str(state.status),
                        "step_status": step.status.value if hasattr(step.status, "value") else str(step.status),
                        "created_at": step.created_at,
                        "expires_at": getattr(proposal, "expires_at", ""),
                    }
                )
        return pending

    def confirm(self, proposal_id: str, step_uuid: str, token: str, approved: bool = True) -> WorkflowState:
        state = self._active_workflows.get(proposal_id)
        if not state:
            for s in list(self._active_workflows.values()):
                for step in s.steps:
                    if step.uuid == step_uuid:
                        state = s
                        break
                if state:
                    break
        if not state:
            step = WorkflowStep(uuid=step_uuid, status=StepStatus.FAILED, error="no pending confirmation found")
            return WorkflowState(steps=[step], status=StepStatus.FAILED)

        step = next((s for s in state.steps if s.uuid == step_uuid), None)
        if step and step.status in {StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.REJECTED, StepStatus.CANCELLED, StepStatus.SKIPPED}:
            return state

        result = self.executor.confirm_step(state, step_uuid, approved=approved, token=token)
        proposal = self.proposal_manager.get(proposal_id)
        if proposal:
            if approved and result.status == StepStatus.COMPLETED:
                proposal.status = ProposalStatus.CONFIRMED
            elif not approved:
                proposal.status = ProposalStatus.CANCELLED if result.status == StepStatus.CANCELLED else ProposalStatus.REJECTED

        terminal_statuses = {
            StepStatus.COMPLETED,
            StepStatus.FAILED,
            StepStatus.REJECTED,
            StepStatus.CANCELLED,
            StepStatus.SKIPPED,
        }
        if all(s.status in terminal_statuses for s in result.steps):
            self._active_workflows.pop(proposal_id, None)

        updated = next((s for s in result.steps if s.uuid == step_uuid), None)
        if updated:
            self.events.confirmation_result(updated, approved=approved)
        return result

    def cancel(self, proposal_id: str, step_uuid: str, token: str) -> WorkflowState:
        return self.confirm(proposal_id, step_uuid, token, approved=False)

    def research_then_propose(self, query: str, *, persist: bool = False) -> tuple[ResearchFindings, Proposal]:
        findings = self.research.research(query, persist=persist)
        self.events.research_completed(findings)
        if not findings.structured:
            proposal = Proposal(objective=query, proposed_actions=[], source_references=[], risk_level=ProposalRiskLevel.LOW, requires_confirmation=False, status=ProposalStatus.REJECTED, validation_errors=["research returned no structured findings"])
            self.events.proposal_created(proposal)
            self.events.proposal_validated(proposal)
            return findings, proposal
        top = findings.structured[0]
        action = ProposedAction(tool="paper_executor", description=f"Execute simulated action for: {query}", parameters={"instrument": top.get("source", "UNKNOWN"), "quantity": 1, "order_type": "market"})
        proposal = self.proposal_manager.create_proposal(objective=query, actions=[{"tool": action.tool, "description": action.description, "parameters": action.parameters, "dependencies": action.dependencies}], sources=[{"source_type": "rag", "identifier": top.get("source", ""), "excerpt": top.get("text", "")[:200]}], risk_level=ProposalRiskLevel.MEDIUM, requires_confirmation=self.require_confirmation)
        self.events.proposal_created(proposal)
        self.events.proposal_validated(proposal)
        return findings, proposal

    def validate_risk(self, proposal: Proposal) -> tuple[bool, list[str]]:
        errors: list[str] = []
        for action in proposal.proposed_actions:
            qty = float((action.parameters or {}).get("quantity", 0))
            instrument = (action.parameters or {}).get("instrument", "")
            if self.risk_limits["allowed_instruments"] and instrument not in self.risk_limits["allowed_instruments"]:
                errors.append(f"instrument not allowed: {instrument}")
            if qty <= 0:
                errors.append("quantity must be positive")
        if self._session_trade_count >= self.risk_limits["max_trades_per_session"]:
            errors.append("max trades per session reached")
        if self.simulation.state.account.available_funds < self.risk_limits["min_available_balance"]:
            errors.append("available funds below minimum")
        approved = not errors
        return approved, errors

    def build_workflow(self, proposal: Proposal) -> WorkflowState:
        steps = []
        for idx, action in enumerate(proposal.proposed_actions):
            step = WorkflowStep(description=action.description, tool=action.tool, parameters=action.parameters, requires_confirmation=proposal.requires_confirmation, dependencies=action.dependencies)
            steps.append(step)
        state = WorkflowState(name=f"execute:{proposal.proposal_id}", description=proposal.objective, steps=steps, context={"proposal_id": proposal.proposal_id, "risk_level": proposal.risk_level.value if hasattr(proposal.risk_level, "value") else str(proposal.risk_level)})
        return state

    def execute_after_confirmation(self, proposal: Proposal, approved: bool = True) -> WorkflowState:
        state = self.build_workflow(proposal)
        step = state.steps[0] if state.steps else None
        if step:
            self.events.confirmation_required(step, proposal)
        result = self.executor.execute(state)
        if result.status == StepStatus.WAITING_FOR_CONFIRMATION and approved:
            result = self.executor.confirm_step(result, step.uuid, approved=True, token=step.confirmation_token)
        elif result.status == StepStatus.WAITING_FOR_CONFIRMATION and not approved:
            result = self.executor.confirm_step(result, step.uuid, approved=False, token=step.confirmation_token)
        if step:
            self.events.confirmation_result(step, approved=approved)
        return result

    def run_paper_execution(self, proposal: Any, *, approved: bool = True) -> dict[str, Any]:
        if isinstance(proposal, str):
            _, proposal_obj = self.research_then_propose(proposal, persist=False)
        elif hasattr(proposal, "proposed_actions") and proposal.proposed_actions:
            proposal_obj = proposal
        else:
            _, proposal_obj = self.research_then_propose(getattr(proposal, "objective", "") or str(proposal), persist=False)
        if proposal_obj.status != ProposalStatus.VALIDATED:
            return {"status": "rejected", "errors": proposal_obj.validation_errors, "proposal_id": proposal_obj.proposal_id}
        approved_risk, risk_errors = self.validate_risk(proposal_obj)
        if not approved_risk:
            proposal_obj.status = ProposalStatus.REJECTED
            proposal_obj.validation_errors.extend(risk_errors)
            self.events.proposal_validated(proposal_obj)
            return {"status": "rejected", "errors": risk_errors, "proposal_id": proposal_obj.proposal_id}
        exec_result = self.execute_after_confirmation(proposal_obj, approved=approved)
        final_step = exec_result.steps[0] if exec_result.steps else None
        if final_step and final_step.status == StepStatus.COMPLETED and final_step.result:
            try:
                order = self.simulation.submit_order(self._order_from_proposal(proposal_obj))
                self._session_trade_count += 1
                self.events.executed(order, final_step.result)
                return {"status": "executed", "order_id": order.order_id, "filled_price": order.filled_price, "error": order.error, "proposal_id": proposal_obj.proposal_id}
            except Exception as exc:
                return {"status": "error", "error": str(exc), "proposal_id": proposal_obj.proposal_id}
        return {"status": exec_result.status.value if isinstance(exec_result.status, StepStatus) else str(exec_result.status), "error": final_step.error if final_step else "", "proposal_id": proposal_obj.proposal_id}

    def _order_from_proposal(self, proposal: Proposal) -> Order:
        action = proposal.proposed_actions[0] if proposal.proposed_actions else ProposedAction()
        params = action.parameters or {}
        instrument = str(params.get("instrument", "UNKNOWN"))
        quantity = float(params.get("quantity", 1))
        order_type = OrderType(params.get("order_type", "market")) if isinstance(params.get("order_type"), str) else OrderType.MARKET
        return Order(proposal_id=proposal.proposal_id, instrument=instrument, direction=Direction.BUY, order_type=order_type, quantity=quantity)
