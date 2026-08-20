"""Phase D — bounded AgentLoop.

Orchestration-only component that connects the already-verified Phase C
Verifier + Replanner to the existing Research/Proposal/Execution pipeline:

    Objective -> ProposalValidator -> Confirmation gate ->
    ProposalExecutor -> Verifier -> (SUCCESS -> DONE) |
    (FAILURE/INCONCLUSIVE -> Replanner -> NEW validated Proposal ->
     returned through the SAME confirmation/execution gate).

CRITICAL SAFETY BOUNDARY (enforced by construction):
  * Every consequential action flows through ProposalValidator ->
    PermissionManager/confirm_fn -> ProposalExecutor -> ExecutionAudit.
  * AgentLoop NEVER calls ToolRegistry.execute / tool.execute directly.
  * AgentLoop NEVER calls PermissionManager.approve_once /
    approve_permanently, and NEVER manufactures a True confirmation.
  * A replan produces a NEW Proposal only. AgentLoop returns it through the
    existing confirmation gate; it does not self-approve or auto-execute
    without the gate.
  * Hard iteration limit (default 5, hard cap 20) bounds the loop. Reaching
    the limit stops and reports, without executing another proposal.
  * Never reports SUCCESS unless the Verifier returns observable SUCCESS.

AgentLoop is orchestration only. It does NOT rewrite ProposalExecutor,
PermissionManager, Verifier, or Replanner, and introduces no second execution
engine. EventBus integration uses real core.events.Event instances on the
existing bus (agent.iteration.started / agent.execution.completed /
agent.verification.completed / agent.replan.completed / agent.completed /
agent.aborted) without breaking existing consumers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from typing import Any, Callable, Optional

from proposal.executor import ProposalExecutor, ExecutionAudit, ExecutionStatus
from proposal.state import Proposal, ProposalStatus
from proposal.validator import ProposalValidator
from proposal.verification import Verifier, VerificationResult, VerificationStatus
from proposal.replanner import Replanner, ReplanResult, ReplanStatus
from core.events import EventType


class AgentLoopStatus(str, Enum):
    DONE = "done"                      # verifier returned observable SUCCESS
    STOPPED_DENIED = "stopped_denied"          # confirmation denied / disconnect
    STOPPED_LIMIT = "stopped_limit"            # iteration limit reached
    STOPPED_REPLAN_FAILED = "stopped_replan_failed"  # replanner could not recover
    STOPPED_INVALID = "stopped_invalid"        # proposal not validated / expired
    STOPPED_ABORTED = "stopped_aborted"        # user abort / disconnect


# Event types published as real core.events.Event objects on the existing
# EventBus (Phase E: first-class EventType members).
AGENT_ITERATION_STARTED = EventType.AGENT_ITERATION_STARTED
AGENT_EXECUTION_COMPLETED = EventType.AGENT_EXECUTION_COMPLETED
AGENT_VERIFICATION_COMPLETED = EventType.AGENT_VERIFICATION_COMPLETED
AGENT_REPLAN_COMPLETED = EventType.AGENT_REPLAN_COMPLETED
AGENT_COMPLETED = EventType.AGENT_COMPLETED
AGENT_ABORTED = EventType.AGENT_ABORTED

# Hard safety bound: callers may request any max_iterations, but it is always
# clamped into [1, HARD_MAX_ITERATIONS] so the loop can never be effectively
# infinite.
HARD_MAX_ITERATIONS = 20


def _effective_max(max_iterations: int) -> int:
    try:
        n = int(max_iterations)
    except (TypeError, ValueError):
        n = 5
    return max(1, min(n, HARD_MAX_ITERATIONS))


@dataclass
class IterationRecord:
    iteration: int
    objective: str
    proposal_id: str
    execution_status: str
    verification_status: str
    confirmation_decisions: list[dict[str, Any]] = field(default_factory=list)
    failed_steps: list[dict[str, Any]] = field(default_factory=list)
    replan_status: Optional[str] = None
    replan_proposal_id: Optional[str] = None
    diagnostics: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "objective": self.objective,
            "proposal_id": self.proposal_id,
            "execution_status": self.execution_status,
            "verification_status": self.verification_status,
            "confirmation_decisions": self.confirmation_decisions,
            "failed_steps": self.failed_steps,
            "replan_status": self.replan_status,
            "replan_proposal_id": self.replan_proposal_id,
            "diagnostics": self.diagnostics,
        }


@dataclass
class AgentLoopResult:
    status: AgentLoopStatus
    objective: str = ""
    iterations: list[IterationRecord] = field(default_factory=list)
    final_proposal: Optional[Proposal] = None
    final_audit: Optional[ExecutionAudit] = None
    final_verification: Optional[VerificationResult] = None
    aborted: bool = False
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "objective": self.objective,
            "iterations": [i.to_dict() for i in self.iterations],
            "final_proposal_id": self.final_proposal.proposal_id if self.final_proposal else None,
            "final_verification_status": self.final_verification.status.value if self.final_verification else None,
            "final_execution_status": self.final_audit.final_status if self.final_audit else None,
            "aborted": self.aborted,
            "message": self.message,
            "metadata": self.metadata,
        }


class AgentLoop:
    """Bounded orchestration of execute -> verify -> (replan) cycles.

    The loop never executes tools itself; it delegates execution to the
    injected/created ProposalExecutor, which enforces the confirmation gate.
    """

    def __init__(
        self,
        tool_registry: Any,
        permission_manager: Any,
        *,
        executor: Optional[ProposalExecutor] = None,
        verifier: Optional[Verifier] = None,
        replanner: Optional[Replanner] = None,
        proposal_validator: Optional[ProposalValidator] = None,
        event_bus: Any | None = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.permission_manager = permission_manager
        self.proposal_validator = proposal_validator or ProposalValidator()
        self._executor = executor or ProposalExecutor(
            tool_registry=tool_registry,
            permission_manager=permission_manager,
            proposal_validator=self.proposal_validator,
        )
        # NOTE: Verifier/Replanner are constructed WITHOUT an EventBus to avoid
        # their internal PhaseCEvent interface clashing with core.events.Event.
        # AgentLoop emits the authoritative agent.* events itself.
        self._verifier = verifier or Verifier()
        self._replanner = replanner or Replanner()
        self._event_bus = event_bus

    # ------------------------------------------------------------------ #
    def run(
        self,
        objective: str,
        proposal: Proposal,
        *,
        confirm_fn: Optional[Callable[[str, str], bool]] = None,
        research_findings: Any = None,
        plan: Any = None,
        context: str = "",
        max_iterations: int = 5,
        replan_kwargs: Optional[dict[str, Any]] = None,
    ) -> AgentLoopResult:
        limit = _effective_max(max_iterations)
        iterations: list[IterationRecord] = []
        current = proposal
        aborted = False

        for i in range(1, limit + 1):
            self._emit(
                AGENT_ITERATION_STARTED,
                {"iteration": i, "objective": objective, "max_iterations": limit},
            )

            # Re-validate through the existing ProposalValidator. Expired or
            # malformed proposals are refused (fail safe).
            self.proposal_validator.validate(current)
            if current.status != ProposalStatus.VALIDATED:
                return self._finish(
                    AgentLoopStatus.STOPPED_INVALID,
                    objective, iterations, current, None, None,
                    aborted=aborted,
                    message=f"proposal not validated (status={current.status.value}); refusing",
                )

            # Execute through ProposalExecutor (confirmation-gated). AgentLoop
            # does NOT execute anything itself.
            audit = self._executor.execute(
                current,
                confirm_fn=confirm_fn,
                research_findings=research_findings,
                plan=plan,
                context=context,
            )
            self._emit(
                AGENT_EXECUTION_COMPLETED,
                {"iteration": i, "proposal_id": current.proposal_id,
                 "final_status": audit.final_status},
            )

            if audit.final_status in (ExecutionStatus.DENIED.value, ExecutionStatus.ABORTED.value):
                aborted = True
                # Requirement #17: every attempted iteration must be present in
                # the audit, including a denied/aborted one (no further verify).
                iterations.append(IterationRecord(
                    iteration=i,
                    objective=objective,
                    proposal_id=current.proposal_id,
                    execution_status=audit.final_status,
                    verification_status="n/a",
                    confirmation_decisions=[d.__dict__ for d in audit.confirmation_decisions],
                    failed_steps=[],
                ))
                return self._finish(
                    AgentLoopStatus.STOPPED_DENIED,
                    objective, iterations, current, audit, None,
                    aborted=aborted,
                    message=f"confirmation {audit.final_status}; stopping without further execution",
                    emit_aborted=True,
                )

            # Verify with observable evidence (never trust final_status alone).
            verification = self._verifier.verify(objective, audit)
            self._emit(
                AGENT_VERIFICATION_COMPLETED,
                {"iteration": i, "proposal_id": current.proposal_id,
                 "verification_status": verification.status.value},
            )

            rec = IterationRecord(
                iteration=i,
                objective=objective,
                proposal_id=current.proposal_id,
                execution_status=audit.final_status,
                verification_status=verification.status.value,
                confirmation_decisions=[d.__dict__ for d in audit.confirmation_decisions],
                failed_steps=verification.failed_steps,
                diagnostics=verification.diagnostics,
            )
            iterations.append(rec)

            if verification.status == VerificationStatus.SUCCESS:
                return self._finish(
                    AgentLoopStatus.DONE,
                    objective, iterations, current, audit, verification,
                    aborted=aborted,
                    message="objective verified as achieved",
                )

            # FAILURE or INCONCLUSIVE -> attempt a single recovery replan, then
            # either continue (next iteration executes the new proposal through
            # the gate) or stop. Never auto-execute without the gate.
            replan = self._replanner.replan(
                objective, current, audit, verification,
                allowed_tools=self._allowed_tools(),
                **(replan_kwargs or {}),
            )
            rec.replan_status = replan.status.value
            if replan.proposal is not None:
                rec.replan_proposal_id = replan.proposal.proposal_id
            self._emit(
                AGENT_REPLAN_COMPLETED,
                {"iteration": i, "replan_status": replan.status.value,
                 "replan_proposal_id": rec.replan_proposal_id},
            )

            if replan.status != ReplanStatus.SUCCESS or replan.proposal is None:
                return self._finish(
                    AgentLoopStatus.STOPPED_REPLAN_FAILED,
                    objective, iterations, current, audit, verification,
                    aborted=aborted,
                    message=f"replanner could not produce a safe recovery proposal: {replan.notes}",
                )

            # Hand the new validated proposal back to the SAME gate for the next
            # iteration. It will be re-validated and confirmation-gated again.
            current = replan.proposal

            if i == limit:
                # Reached the hard limit: stop, do NOT execute another proposal.
                return self._finish(
                    AgentLoopStatus.STOPPED_LIMIT,
                    objective, iterations, current, audit, verification,
                    aborted=aborted,
                    message="iteration limit reached; returning accumulated state",
                )

        # Defensive: should be unreachable due to the in-loop limit check.
        return self._finish(
            AgentLoopStatus.STOPPED_LIMIT,
            objective, iterations, current, None, None,
            aborted=aborted,
            message="iteration limit reached",
        )

    # ------------------------------------------------------------------ #
    def _allowed_tools(self) -> list[str]:
        try:
            return list(self.tool_registry.tool_names())
        except Exception:
            return []

    def _finish(
        self,
        status: AgentLoopStatus,
        objective: str,
        iterations: list[IterationRecord],
        final_proposal: Optional[Proposal],
        final_audit: Optional[ExecutionAudit],
        final_verification: Optional[VerificationResult],
        *,
        aborted: bool = False,
        message: str = "",
        emit_aborted: bool = False,
    ) -> AgentLoopResult:
        if emit_aborted:
            self._emit(AGENT_ABORTED, {"objective": objective, "status": status.value,
                                        "message": message})
        self._emit(AGENT_COMPLETED, {"objective": objective, "status": status.value,
                                     "iterations": len(iterations), "message": message})
        return AgentLoopResult(
            status=status,
            objective=objective,
            iterations=iterations,
            final_proposal=final_proposal,
            final_audit=final_audit,
            final_verification=final_verification,
            aborted=aborted,
            message=message,
        )

    # ------------------------------------------------------------------ #
    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_bus is None:
            return
        try:
            from core.events import Event, Severity
            event = Event(
                event_type=event_type,  # real core.events.EventType member
                source="agent_loop",
                payload=payload,
                severity=Severity.INFO,
            )
            self._event_bus.publish(event)
        except Exception:
            # Never let event emission break the loop or leak side effects.
            pass
