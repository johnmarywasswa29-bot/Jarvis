"""Phase C — Replanner foundation.

Diagnostic planning component. Consumes the original objective, the executed
proposal, the :class:`ExecutionAudit`, and the :class:`VerificationResult`, and
produces a NEW validated recovery :class:`Proposal`.

Safety invariants (HARD):
  * The Replanner NEVER executes anything. It receives only ``allowed_tools``
    (a list of names) — it holds no ToolRegistry and cannot call execute().
  * It NEVER calls PermissionManager.confirm / approve, and never self-approves.
  * Every generated proposal is validated by ProposalValidator.
  * Proposals referencing UNKNOWN tools (not in ``allowed_tools``) are rejected.
  * Malformed or unsafe proposals stop (return failure/inconclusive); they are
    never returned for execution.
  * If no safe recovery can be determined, it returns INCONCLUSIVE and leaves
    the decision to the outer orchestrator / user.

The returned proposal is the END of the Phase C flow. The caller decides
whether/when to execute it (AgentLoop is NOT authorized in Phase C).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from typing import Any, Callable, Optional

from proposal.state import Proposal, ProposedAction, ProposalStatus
from proposal.validator import ProposalValidator
from proposal.verification import VerificationResult, VerificationStatus, PhaseCEvent


class ReplanStatus(str, Enum):
    SUCCESS = "success"          # a safe, validated recovery proposal was produced
    FAILURE = "failure"          # could not produce a safe proposal (rejected)
    INCONCLUSIVE = "inconclusive"  # needs user/outer-orchestrator decision


@dataclass
class ReplanResult:
    status: ReplanStatus
    diagnosis: str = ""
    recovery_strategy: str = ""
    proposal: Optional[Proposal] = None
    notes: str = ""
    iteration: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "diagnosis": self.diagnosis,
            "recovery_strategy": self.recovery_strategy,
            "proposal_id": self.proposal.proposal_id if self.proposal else None,
            "notes": self.notes,
            "iteration": self.iteration,
        }


class Replanner:
    def __init__(self, event_bus: Any | None = None) -> None:
        # NOTE: only a list of allowed tool NAMES is accepted. The Replanner
        # holds no ToolRegistry and therefore cannot execute anything.
        self.event_bus = event_bus

    # ------------------------------------------------------------------ #
    def replan(
        self,
        objective: str,
        original_proposal: Proposal,
        audit: Any,
        verification: VerificationResult,
        *,
        allowed_tools: list[str],
        llm: Optional[Callable[[str, str, list], dict]] = None,
        observations: Optional[dict[str, Any]] = None,
        iteration: int = 0,
    ) -> ReplanResult:
        observations = observations or {}

        # If the objective was already achieved, no replan is needed.
        if verification.status == VerificationStatus.SUCCESS:
            result = ReplanResult(
                status=ReplanStatus.INCONCLUSIVE,
                diagnosis="objective already achieved; no recovery needed",
                recovery_strategy="none",
                proposal=None,
                notes="verification succeeded",
                iteration=iteration,
            )
            self._publish("replan.completed", result=result)
            return result

        diagnosis = self._diagnose(objective, audit, verification)

        # Attempt to generate a recovery proposal (deterministic by default;
        # an injected `llm` may augment but the output is always validated).
        candidate = self._build_recovery(
            objective, original_proposal, verification, allowed_tools, llm=llm
        )
        if candidate is None:
            result = ReplanResult(
                status=ReplanStatus.FAILURE,
                diagnosis=diagnosis,
                recovery_strategy="none",
                proposal=None,
                notes="no safe recovery strategy could be determined",
                iteration=iteration,
            )
            self._publish("replan.completed", result=result)
            return result

        # REJECT unknown tools up front (defense in depth beyond validator).
        for action in candidate.proposed_actions:
            if action.tool not in allowed_tools:
                result = ReplanResult(
                    status=ReplanStatus.FAILURE,
                    diagnosis=diagnosis,
                    recovery_strategy="rejected unknown tool",
                    proposal=None,
                    notes=f"unknown tool rejected: {action.tool}",
                    iteration=iteration,
                )
                self._publish("replan.completed", result=result)
                return result

        # Validate the generated proposal.
        validator = ProposalValidator()
        validator.validate(candidate)
        if candidate.status != ProposalStatus.VALIDATED:
            result = ReplanResult(
                status=ReplanStatus.FAILURE,
                diagnosis=diagnosis,
                recovery_strategy="proposal rejected by validator",
                proposal=None,
                notes="; ".join(candidate.validation_errors) or "invalid proposal",
                iteration=iteration,
            )
            self._publish("replan.completed", result=result)
            return result

        result = ReplanResult(
            status=ReplanStatus.SUCCESS,
            diagnosis=diagnosis,
            recovery_strategy=self._strategy_text(verification),
            proposal=candidate,
            notes="recovery proposal validated; awaiting executor decision",
            iteration=iteration,
        )
        self._publish("replan.completed", result=result)
        return result

    # ------------------------------------------------------------------ #
    def _diagnose(self, objective: str, audit: Any, verification: VerificationResult) -> str:
        parts = [f"objective: {objective}"]
        if verification.failed_steps:
            for fs in verification.failed_steps:
                parts.append(f"failed step {fs.get('order')} ({fs.get('tool')}): {fs.get('error')}")
        if verification.diagnostics:
            parts.append(f"verifier: {verification.diagnostics}")
        return " | ".join(parts)

    def _strategy_text(self, verification: VerificationResult) -> str:
        if verification.failed_steps:
            tools = {fs.get("tool") for fs in verification.failed_steps}
            return f"re-run/inspect failed step(s) via: {', '.join(sorted(tools))}"
        return "re-verify objective with available read-only tools"

    def _build_recovery(
        self,
        objective: str,
        original: Proposal,
        verification: VerificationResult,
        allowed_tools: list[str],
        *,
        llm: Optional[Callable[[str, str, list], dict]] = None,
    ) -> Optional[Proposal]:
        # If an LLM seam is provided, let it propose actions; otherwise use a
        # deterministic recovery strategy from the failed-step evidence.
        actions: list[ProposedAction] = []
        if llm is not None:
            try:
                spec = llm(objective, verification.diagnostics, verification.failed_steps)
                if isinstance(spec, dict) and "proposed_actions" in spec:
                    for a in spec["proposed_actions"]:
                        actions.append(ProposedAction(
                            tool=a.get("tool", ""), description=a.get("description", ""),
                            parameters=a.get("parameters", {}),
                        ))
            except Exception:
                actions = []
        else:
            # Deterministic recovery: for each failed step, propose re-running
            # the SAME (allowed) tool to re-verify / recover. This is a proposal
            # only — execution remains gated by confirmation.
            for fs in verification.failed_steps:
                tool = fs.get("tool")
                if tool in allowed_tools:
                    actions.append(ProposedAction(
                        tool=tool,
                        description=f"recover step {fs.get('order')} ({tool})",
                        parameters=dict(fs.get("parameters", {})),
                    ))
            # If no failed steps but inconclusive, propose a read-only re-verify
            # using a test/git tool if available.
            if not actions:
                if "test" in (objective or "").lower() and "test_execution" in allowed_tools:
                    actions.append(ProposedAction(
                        tool="test_execution", description="re-verify tests",
                        parameters={},
                    ))
                elif "build" in (objective or "").lower() and "build" in allowed_tools:
                    actions.append(ProposedAction(
                        tool="build", description="re-verify build", parameters={},
                    ))
                elif "git" in (objective or "").lower() and "git" in allowed_tools:
                    actions.append(ProposedAction(
                        tool="git", description="re-check repository state",
                        parameters={"command": "status"},
                    ))
        if not actions:
            return None
        return Proposal(
            objective=f"Recovery: {objective}",
            proposed_actions=actions,
            requires_confirmation=True,
        )

    # ------------------------------------------------------------------ #
    def _publish(self, etype: str, **data: Any) -> None:
        if self.event_bus is not None:
            try:
                self.event_bus.publish(PhaseCEvent(etype, **data))
            except Exception:
                pass
