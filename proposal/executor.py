"""Proposal execution layer (Phase 9E).

Takes a VALIDATED :class:`~proposal.state.Proposal` and executes its steps
through the EXISTING ``ToolRegistry``, gated by the EXISTING
``PermissionManager`` and validated by the EXISTING ``ProposalValidator``.

Safety model (per Phase 9E requirements):
  * A proposal must be VALIDATED before it can run; otherwise execution is
    refused (fail safe).
  * Every consequential action is routed through ``PermissionManager.confirm``
    and the decision is RESPECTED. Confirmation is never bypassed or
    auto-approved. SAFE tools return True from ``confirm`` without prompting,
    so read-only / safe steps run without unnecessary confirmation.
  * Steps execute in dependency order (topological). Unknown tools, missing
    dependencies, or cycles stop execution safely.
  * A FAILED or DENIED step halts the run; later steps are marked SKIPPED.
  * Per-step status, output, error, duration and execution order are collected.
  * The full run is summarized in an :class:`ExecutionAudit` (research
    findings, plan, proposal, confirmation decisions, executed steps, status).

No LLM providers are touched. No LangGraph is used; a simple topological
ordering loop is sufficient.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from modules.permission_manager import PermissionManager
from modules.tools import ToolRegistry
from proposal.state import Proposal, ProposalStatus, ProposedAction
from proposal.validator import ProposalValidator, ProposalValidationError


class StepStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DENIED = "denied"
    EXECUTED = "executed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"      # some steps ran, halted before all
    FAILED = "failed"        # a step failed
    DENIED = "denied"        # a required confirmation was denied
    ABORTED = "aborted"      # user aborted before execution
    RESEARCH_ONLY = "research_only"  # research + plan produced; no execution requested
    INVALID = "invalid"      # proposal not validated
    ERROR = "error"          # execution could not start


@dataclass
class ExecutedStep:
    """Record of a single executed (or skipped) proposal step."""

    action_id: str
    tool: str
    order: int
    status: str = StepStatus.PENDING.value
    confirmation_decision: Optional[bool] = None
    output: str = ""
    error: str = ""
    duration_s: float = 0.0
    parameters: dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass
class ConfirmationDecision:
    action_id: str
    tool: str
    decision: bool
    level: str = ""


@dataclass
class ExecutionAudit:
    """Complete execution / audit result for a proposal run."""

    proposal_id: str = ""
    objective: str = ""
    final_status: str = "pending"
    executed_steps: list[ExecutedStep] = field(default_factory=list)
    confirmation_decisions: list[ConfirmationDecision] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    research_findings: Any = None   # optional: originating ResearchFindings
    plan: Any = None                # optional: originating ResearchPlan
    proposal: Any = None           # the executed Proposal
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        import json as _json
        return {
            "proposal_id": self.proposal_id,
            "objective": self.objective,
            "final_status": self.final_status,
            "executed_steps": [s.__dict__ for s in self.executed_steps],
            "confirmation_decisions": [d.__dict__ for d in self.confirmation_decisions],
            "errors": self.errors,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


class ProposalExecutor:
    """Executes a validated proposal's steps through the ToolRegistry safely."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        permission_manager: PermissionManager,
        proposal_validator: Optional[ProposalValidator] = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.permission_manager = permission_manager
        self.proposal_validator = proposal_validator or ProposalValidator()

    # --------------------------------------------------------------- public API
    def execute(
        self,
        proposal: Proposal,
        *,
        research_findings: Any = None,
        plan: Any = None,
        context: str = "",
        on_step: Any = None,
        confirm_fn: Any = None,
    ) -> ExecutionAudit:
        """Execute a validated proposal, returning a full :class:`ExecutionAudit`.

        ``on_step`` is an optional callback invoked after each step is recorded
        (signature: ``on_step(step: ExecutedStep, audit: ExecutionAudit)``). The
        WebSocket layer uses it to stream per-step progress to the browser.

        ``confirm_fn`` is an optional callable ``confirm_fn(tool) -> bool`` used
        to gate each consequential step. When ``None`` (default), the REAL
        ``PermissionManager.confirm`` is used (interactive stdin prompt, suited
        to the desktop/CLI). Non-interactive callers (e.g. the Web UI, where the
        user's explicit ACCEPT of the whole proposal is the authorization)
        inject a non-blocking confirm that honors that decision. This keeps the
        PermissionManager policy authoritative without blocking on stdin.
        """
        from datetime import datetime, UTC

        audit = ExecutionAudit(
            proposal_id=proposal.proposal_id,
            objective=proposal.objective,
            research_findings=research_findings,
            plan=plan,
            proposal=proposal,
            started_at=datetime.now(UTC).replace(tzinfo=None).isoformat(),
        )

        # 1) Refuse to run any proposal that was NOT already validated when
        #    handed in. Re-validating a DRAFT/REJECTED proposal could promote it
        #    (the validator sets VALIDATED on valid actions), which would
        #    silently bypass the "must be validated first" safety rule. So we
        #    check the incoming status first, then re-validate to catch
        #    expiry/regressions (an expired VALIDATED proposal flips to EXPIRED
        #    and is still refused).
        if proposal.status != ProposalStatus.VALIDATED:
            audit.final_status = ExecutionStatus.INVALID.value
            audit.errors.append(
                f"proposal is not validated (status={proposal.status.value}); refusing to execute"
            )
            audit.completed_at = datetime.now(UTC).replace(tzinfo=None).isoformat()
            return audit

        try:
            self.proposal_validator.validate(proposal)
        except Exception as e:  # pragma: no cover - defensive
            audit.final_status = ExecutionStatus.INVALID.value
            audit.errors.append(f"validation error: {e}")
            audit.completed_at = datetime.now(UTC).replace(tzinfo=None).isoformat()
            return audit

        if proposal.status != ProposalStatus.VALIDATED:
            audit.final_status = ExecutionStatus.INVALID.value
            audit.errors.append(
                f"proposal is not validated after re-validation (status={proposal.status.value}); refusing to execute"
            )
            audit.completed_at = datetime.now(UTC).replace(tzinfo=None).isoformat()
            return audit

        # 2) Topological ordering by dependencies (fail safe on bad graph).
        order = self._order_actions(proposal.proposed_actions, audit)
        if audit.final_status == ExecutionStatus.ERROR.value:
            audit.completed_at = datetime.now(UTC).replace(tzinfo=None).isoformat()
            return audit

        # 3) Execute in order, gated by confirmation, stopping safely on failure.
        executed_by_id: dict[str, ExecutedStep] = {}
        halted = False
        for position, action in enumerate(order, start=1):
            rec = ExecutedStep(
                action_id=action.action_id,
                tool=action.tool,
                order=position,
                parameters=dict(action.parameters),
                description=action.description,
            )

            if halted:
                rec.status = StepStatus.SKIPPED.value
                audit.executed_steps.append(rec)
                continue

            # Tool resolution (fail safe).
            tool = self.tool_registry.get_tool(action.tool)
            if tool is None:
                rec.status = StepStatus.FAILED.value
                rec.error = f"unknown or unsupported tool: {action.tool!r}"
                audit.executed_steps.append(rec)
                audit.errors.append(rec.error)
                audit.final_status = ExecutionStatus.FAILED.value
                halted = True
                continue

            # Confirmation gate — RESPECT the decision; never bypass.
            level = self.permission_manager.get_level(action.tool)
            confirm = confirm_fn if callable(confirm_fn) else self.permission_manager.confirm
            allowed = confirm(
                action.tool,
                details=action.description or f"Execute {action.tool}",
            )
            audit.confirmation_decisions.append(
                ConfirmationDecision(
                    action_id=action.action_id, tool=action.tool,
                    decision=allowed, level=level,
                )
            )
            rec.confirmation_decision = allowed

            if not allowed:
                rec.status = StepStatus.DENIED.value
                rec.error = "confirmation denied"
                audit.executed_steps.append(rec)
                if on_step is not None:
                    try:
                        on_step(rec, audit)
                    except Exception:
                        pass
                audit.errors.append(f"confirmation denied for {action.tool}")
                audit.final_status = ExecutionStatus.DENIED.value
                halted = True
                continue

            rec.status = StepStatus.CONFIRMED.value
            # Execute the tool with the step's explicit parameters.
            t0 = time.perf_counter()
            try:
                result = tool.execute(**action.parameters)
                rec.duration_s = round(time.perf_counter() - t0, 4)
                rec.output = getattr(result, "output", "") or ""
                rec.error = getattr(result, "error", "") or ""
                if getattr(result, "success", False):
                    rec.status = StepStatus.EXECUTED.value
                else:
                    rec.status = StepStatus.FAILED.value
                    audit.errors.append(f"{action.tool} failed: {rec.error}")
                    audit.final_status = ExecutionStatus.FAILED.value
                    halted = True
            except Exception as e:  # tool raised unexpectedly -> fail safe
                rec.duration_s = round(time.perf_counter() - t0, 4)
                rec.status = StepStatus.FAILED.value
                rec.error = f"execution exception: {e}"
                audit.errors.append(rec.error)
                audit.final_status = ExecutionStatus.FAILED.value
                halted = True

            audit.executed_steps.append(rec)
            if on_step is not None:
                try:
                    on_step(rec, audit)
                except Exception:
                    pass

        # Final status resolution.
        if audit.final_status in (ExecutionStatus.SUCCESS.value, "pending"):
            # No failure/denial recorded -> all ran.
            ran = [s for s in audit.executed_steps if s.status == StepStatus.EXECUTED.value]
            if ran and all(
                s.status == StepStatus.EXECUTED.value for s in audit.executed_steps
            ):
                audit.final_status = ExecutionStatus.SUCCESS.value
            elif ran:
                audit.final_status = ExecutionStatus.PARTIAL.value
            else:
                audit.final_status = ExecutionStatus.PARTIAL.value

        audit.completed_at = datetime.now(UTC).replace(tzinfo=None).isoformat()
        return audit

    # ----------------------------------------------------------- ordering helper
    def _order_actions(
        self, actions: list[ProposedAction], audit: ExecutionAudit
    ) -> list[ProposedAction]:
        """Return actions in dependency order (Kahn's algorithm).

        On a missing dependency or cycle, records an error and sets
        ``audit.final_status = ERROR`` so the caller refuses to run.
        """
        by_id = {a.action_id: a for a in actions}
        indeg = {a.action_id: 0 for a in actions}
        dependents: dict[str, list[str]] = {a.action_id: [] for a in actions}

        for a in actions:
            for dep in a.dependencies:
                if dep not in by_id:
                    audit.final_status = ExecutionStatus.ERROR.value
                    audit.errors.append(
                        f"action {a.action_id} depends on unknown id {dep!r}"
                    )
                    return []
                indeg[a.action_id] += 1
                dependents[dep].append(a.action_id)

        # Seed with zero-indegree actions, preserving original order.
        queue = [a.action_id for a in actions if indeg[a.action_id] == 0]
        ordered: list[ProposedAction] = []
        while queue:
            nid = queue.pop(0)
            ordered.append(by_id[nid])
            for nxt in dependents[nid]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    queue.append(nxt)

        if len(ordered) != len(actions):
            # Cycle detected (some nodes never reached indegree 0).
            audit.final_status = ExecutionStatus.ERROR.value
            audit.errors.append("dependency cycle detected; cannot order steps")
            return []

        return ordered
