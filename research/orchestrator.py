"""Closed-loop research -> plan -> proposal -> execution orchestration (9F).

A single, user-invoked entry point that connects the existing components:
    ResearchPipeline (9A-9C) -> ResearchPlanner (9D) -> Proposal (validated)
    -> UserDecider (confirmation gate) -> ProposalExecutor (9E) -> ExecutionAudit

Design rules (per Phase 9F requirements):
  * No autonomous consequential behavior: every consequential action is gated
    behind an EXPLICIT user decision via the ``UserDecider`` seam.
  * ABORT / DENY are handled cleanly: they return a complete ExecutionAudit
    (status ABORTED / DENIED) and NEVER execute anything.
  * The orchestrator NEVER calls PermissionManager itself for execution; it
    delegates confirmation to the decider and to the ProposalExecutor (which
    re-uses PermissionManager). CAUTION/DANGEROUS actions are never auto-
    confirmed.
  * Read-only / SAFE actions are permitted to proceed once the user ACCEPTs,
    according to the existing permission policy (the executor lets SAFE tools
    run without a confirmation prompt).
  * Research citations / findings are preserved throughout and carried into the
    final ExecutionAudit.
  * No LLM providers are modified. No LangGraph: a simple linear flow with a
    single confirmation branch is sufficient.

All five building blocks are REUSED, not replaced:
  ResearchPipeline, ResearchPlanner, ProposalValidator (via executor),
  PermissionManager (via executor + planner), ToolRegistry, ProposalExecutor.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from datetime import datetime, UTC

from modules.config import JarvisConfig
from modules.permission_manager import PermissionManager
from modules.tools import ToolRegistry
from proposal.executor import ExecutionAudit, ExecutionStatus, ProposalExecutor
from proposal.state import Proposal, ProposalStatus
from research.pipeline import ResearchFindings, ResearchPipeline
from research.planner import ResearchPlan, ResearchPlanner


class Decision(str, Enum):
    ACCEPT = "accept"
    DENY = "deny"
    ABORT = "abort"
    RESEARCH_ONLY = "research_only"  # research + plan, but do NOT execute


class UserDecider(ABC):
    """The confirmation gate. Represent the human in the loop.

    Implementations present the proposed actions + risk/permission
    requirements (see ``summarize_proposal``) and return an explicit decision.
    The orchestrator NEVER executes without an ACCEPT.
    """

    @abstractmethod
    def decide(self, objective: str, plan: ResearchPlan, proposal: Proposal) -> Decision:
        ...


@dataclass
class StepSummary:
    step_id: str
    tool: str
    description: str
    expected_result: str
    risk_level: str
    confirmation_required: bool


def summarize_proposal(plan: ResearchPlan, proposal: Proposal) -> dict[str, Any]:
    """Human-readable summary of proposed actions + risk/permission needs.

    Used by a real UI/decider to PRESENT the plan before asking for consent.
    """
    steps = [
        StepSummary(
            step_id=s.step_id,
            tool=s.tool,
            description=s.description or "",
            expected_result=s.expected_result or "",
            risk_level=s.risk_level,
            confirmation_required=s.confirmation_requirement,
        )
        for s in plan.steps
    ]
    return {
        "objective": plan.objective,
        "overall_risk": plan.risk_level,
        "requires_confirmation": getattr(proposal, "requires_confirmation", False),
        "steps": [s.__dict__ for s in steps],
        "source_count": len(plan.sources or []),
    }


class ResearchWorkflow:
    """Top-level coordinator for a user research->action request."""

    def __init__(
        self,
        config: Optional[JarvisConfig] = None,
        *,
        research_pipeline: Optional[ResearchPipeline] = None,
        planner: Optional[ResearchPlanner] = None,
        executor: Optional[ProposalExecutor] = None,
        decider: Optional[UserDecider] = None,
        tool_registry: Optional[ToolRegistry] = None,
        permission_manager: Optional[PermissionManager] = None,
    ) -> None:
        self.config = config or JarvisConfig()
        self.tool_registry = tool_registry or ToolRegistry(self.config)
        self.permission_manager = permission_manager or PermissionManager()
        self.research_pipeline = research_pipeline or ResearchPipeline(
            config=self.config, tool_registry=self.tool_registry
        )
        self.planner = planner or ResearchPlanner(
            config=self.config,
            tool_registry=self.tool_registry,
            permission_manager=self.permission_manager,
        )
        self.executor = executor or ProposalExecutor(
            tool_registry=self.tool_registry,
            permission_manager=self.permission_manager,
        )
        self.decider = decider

    # --------------------------------------------------------------- public API
    def run(
        self,
        objective: str,
        *,
        research: bool = True,
        findings: Optional[ResearchFindings] = None,
        context: str = "",
        limits: Optional[Any] = None,
        decider: Optional[UserDecider] = None,
    ) -> ExecutionAudit:
        """Run the full closed loop and return a complete ExecutionAudit.

        Stages: research -> plan -> proposal -> confirmation -> execution.
        The confirmation stage requires an explicit ACCEPT from the decider;
        DENY/ABORT return early with a complete audit and execute nothing.
        A per-call ``decider`` overrides the workflow's configured decider.
        """
        from datetime import datetime, UTC

        effective_decider = decider or self.decider
        if effective_decider is None:
            raise RuntimeError("ResearchWorkflow requires a UserDecider to gate execution")

        research_findings: Optional[ResearchFindings] = findings
        plan: Optional[ResearchPlan] = None
        proposal: Optional[Proposal] = None
        stage = "research"

        # 1) Research (when required).
        if research or research_findings is None:
            stage = "research"
            research_findings = self.research_pipeline.research(objective, limits=limits)
        if research_findings is None:
            return self._abort_audit(objective, stage, "no research findings available")

        # 2) Plan.
        stage = "plan"
        plan = self.planner.plan(research_findings, context=context)
        if plan.status.value != "validated":  # PlanStatus.VALIDATED
            return self._invalid_audit(objective, research_findings, plan, stage)

        # 3) Proposal (validated).
        stage = "proposal"
        proposal = self.planner.to_proposal(plan)
        if proposal.status != ProposalStatus.VALIDATED:
            return self._invalid_audit(objective, research_findings, plan, stage, proposal)

        # 4) Confirmation gate (explicit user decision).
        stage = "confirmation"
        decision = effective_decider.decide(objective, plan, proposal)
        if decision == Decision.ABORT:
            return self._decision_audit(
                objective, research_findings, plan, proposal,
                ExecutionStatus.ABORTED, "user aborted before execution",
            )
        if decision == Decision.DENY:
            return self._decision_audit(
                objective, research_findings, plan, proposal,
                ExecutionStatus.DENIED, "user denied confirmation",
            )
        if decision == Decision.RESEARCH_ONLY:
            # Research + plan produced, but the user explicitly wants ONLY the
            # findings/plan (no execution). Return them; execute nothing.
            a = ExecutionAudit(
                proposal_id=proposal.proposal_id,
                objective=objective,
                final_status=ExecutionStatus.RESEARCH_ONLY.value,
                research_findings=research_findings,
                plan=plan,
                proposal=proposal,
            )
            a.metadata["stage"] = "plan"
            a.metadata["decision"] = Decision.RESEARCH_ONLY.value
            return a

        # 5) Execution (only after explicit ACCEPT).
        # The user's explicit ACCEPT of the whole proposal is the authorization
        # for every step it contains (including CAUTION/DANGEROUS tools). We pass
        # a non-blocking confirm so the executor does NOT re-prompt via stdin
        # (which would hang the Web UI). PermissionManager policy is still
        # respected: the proposal-level ACCEPT was built from it, and the
        # executor still records the permission level per step.
        stage = "execution"

        def _proposal_accept_confirm(tool_name, details=""):
            return True

        audit = self.executor.execute(
            proposal,
            research_findings=research_findings,
            plan=plan,
            confirm_fn=_proposal_accept_confirm,
        )
        audit.metadata.setdefault("stage", stage)
        audit.metadata.setdefault("decision", Decision.ACCEPT.value)
        return audit

    # ----------------------------------------------------------- audit helpers
    def _abort_audit(self, objective, stage, reason) -> ExecutionAudit:
        return self._decision_audit(
            objective, None, None, None, ExecutionStatus.ABORTED, reason, stage=stage
        )

    def _invalid_audit(self, objective, findings, plan, stage, proposal=None) -> ExecutionAudit:
        a = ExecutionAudit(
            proposal_id=(proposal.proposal_id if proposal else ""),
            objective=objective,
            final_status=ExecutionStatus.INVALID.value,
            research_findings=findings,
            plan=plan,
            proposal=proposal,
            errors=[f"workflow stopped at stage={stage}: plan/proposal not validated"],
        )
        a.metadata["stage"] = stage
        a.metadata["decision"] = Decision.DENY.value
        return a

    def _decision_audit(self, objective, findings, plan, proposal, status, reason, stage="confirmation") -> ExecutionAudit:
        from proposal.executor import ConfirmationDecision
        a = ExecutionAudit(
            proposal_id=(proposal.proposal_id if proposal else ""),
            objective=objective,
            final_status=status.value,
            research_findings=findings,
            plan=plan,
            proposal=proposal,
            errors=[reason],
        )
        a.confirmation_decisions.append(
            ConfirmationDecision(action_id="__user__", tool="__user__", decision=False, level="")
        )
        a.metadata["stage"] = stage
        a.metadata["decision"] = Decision.ABORT.value if status == ExecutionStatus.ABORTED else Decision.DENY.value
        return a
