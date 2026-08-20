"""Thin Chat/Brain bridge to the research workflow (Phase 9G).

This module is the SMALLEST possible connector between the existing chat/intent
system (JarvisBrain) and the already-complete research backend (9A-9F):

    ResearchPipeline (9A-9C) -> ResearchPlanner (9D) -> Proposal (9D)
    -> UserDecider (9F gate) -> ProposalExecutor (9E) -> ExecutionAudit

It does NOT duplicate any pipeline / planner / executor / permission logic. It:
  * classifies whether a user request needs the research workflow
    (RESEARCH_ONLY = findings only, RESEARCH_ACTION = research then execute),
  * delegates to the existing ResearchWorkflow for both,
  * renders a plain-text response that PRESERVES citations, the plan, the
    proposal, and the execution/audit result,
  * guarantees no execution without an explicit user decision (the decider),
  * and, on deny/abort, reports that clearly and executes nothing.

The bridge is domain-agnostic and thin: it only routes and renders.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from research.orchestrator import Decision, ResearchWorkflow
from proposal.executor import ExecutionStatus


class ResearchIntent(Enum):
    NONE = "none"                      # ordinary Jarvis request; do not route
    RESEARCH_ONLY = "research_only"    # research + plan, no execution
    RESEARCH_ACTION = "research_action"  # research + plan + execute (after confirm)


# Thin, domain-agnostic keyword signals. Not a full NL model; complements
# (not duplicates) the FastIntentRouter in modules/fast_intent.py.
# Strict ACTION phrases => research + plan + execute (after confirmation).
_ACTION_PHRASES = (
    "what should i do", "what should i", "figure out what to do",
    "figure out what", "how should i", "steps to", "plan to", "create a plan",
    "build a plan", "recommend actions", "recommend what", "and do it",
    "then do it", "take action", "execute", "set up", "configure",
    "research and", "investigate and", "find out and",
)
# Research verbs with NO action phrasing => research + plan only.
_RESEARCH_VERBS = (
    "research", "investigate", "find out", "look into", "dig into",
    "what is the best", "what are the best", "compare", "analyze",
    "summarize the", "tell me about", "learn about",
)


class ResearchBridge:
    """Connects a user request to the existing ResearchWorkflow."""

    def __init__(
        self,
        workflow: ResearchWorkflow,
        *,
        decider: Optional[Any] = None,
    ) -> None:
        self.workflow = workflow
        self.decider = decider

    # ----------------------------------------------------------- classification
    @staticmethod
    def classify(prompt: str) -> ResearchIntent:
        """Decide whether (and how) a request should use the research workflow.

        Returns NONE for ordinary requests so the brain's existing path runs
        unchanged.
        """
        p = (prompt or "").strip().lower()
        if not p:
            return ResearchIntent.NONE
        # Explicit action intent routes to the full research+action workflow.
        if any(phrase in p for phrase in _ACTION_PHRASES):
            return ResearchIntent.RESEARCH_ACTION
        # Otherwise a research verb with no action phrasing => research only.
        if any(v in p for v in _RESEARCH_VERBS):
            return ResearchIntent.RESEARCH_ONLY
        return ResearchIntent.NONE

    # ------------------------------------------------------------------ handle
    def handle(self, prompt: str, intent: Optional[ResearchIntent] = None, *, decider: Optional[Any] = None) -> str:
        """Run the appropriate research flow and return a rendered response.

        For RESEARCH_ONLY, the workflow is told to stop after producing the
        plan (no execution). For RESEARCH_ACTION, the workflow runs through the
        confirmation gate and (on ACCEPT) execution.
        """
        intent = intent or self.classify(prompt)
        if intent == ResearchIntent.NONE:
            raise ValueError("ResearchBridge.handle called with NONE intent")

        effective_decider = decider or self.decider
        if intent == ResearchIntent.RESEARCH_ACTION and effective_decider is None:
            # No autonomous confirmation: require an explicit decider.
            return (
                "I can research this and prepare a plan, but executing actions "
                "requires your explicit confirmation. Please enable confirmation "
                "for research actions."
            )

        if intent == ResearchIntent.RESEARCH_ONLY:
            # Tell the workflow to stop after planning.
            audit = self.workflow.run(prompt, decider=_ResearchOnlyDecider())
        else:
            audit = self.workflow.run(prompt, decider=effective_decider)

        return render_research_response(audit, intent=intent)


class _ResearchOnlyDecider:
    """Internal decider that requests RESEARCH_ONLY (no execution)."""

    def decide(self, objective, plan, proposal):
        return Decision.RESEARCH_ONLY


# --------------------------------------------------------------- rendering
def render_research_response(audit: Any, *, intent: ResearchIntent) -> str:
    """Render an ExecutionAudit into a plain-text Jarvis response.

    Preserves: research citations, the plan (objective + steps w/ risk &
    confirmation), the proposal, and execution results/audit. On deny/abort,
    reports clearly that nothing was executed.
    """
    lines: list[str] = []
    findings = getattr(audit, "research_findings", None)
    plan = getattr(audit, "plan", None)
    proposal = getattr(audit, "proposal", None)
    status = getattr(audit, "final_status", "unknown")

    objective = getattr(audit, "objective", "") or (plan.objective if plan else "")
    lines.append(f"Research: {objective}")

    # Citations / findings.
    if findings is not None:
        try:
            cites = findings.get_citations()
        except Exception:
            cites = []
        if cites:
            lines.append("\nSources:")
            for i, c in enumerate(cites, 1):
                title = c.get("title", "") or c.get("url", "")
                url = c.get("url", "")
                lines.append(f"  {i}. {title} — {url}")
        synthesis = getattr(findings, "synthesis", "")
        if synthesis:
            lines.append(f"\nSummary: {synthesis}")

    # Plan.
    if plan is not None:
        lines.append(f"\nPlan (overall risk: {getattr(plan, 'risk_level', '?')}):")
        for s in plan.steps:
            flag = " [needs confirmation]" if getattr(s, "confirmation_requirement", False) else ""
            lines.append(
                f"  - {s.step_id}: {s.tool} -> {getattr(s, 'expected_result', '')}{flag}"
            )

    # Execution / decision outcome.
    if status == ExecutionStatus.RESEARCH_ONLY.value:
        lines.append("\n(no actions executed — research + plan only, as requested)")
    elif status in (ExecutionStatus.DENIED.value, ExecutionStatus.ABORTED.value):
        lines.append(f"\nNo actions were executed. The request was {status} by the user.")
    elif status == ExecutionStatus.INVALID.value:
        lines.append("\nThe plan could not be validated; nothing was executed.")
    elif status == ExecutionStatus.FAILED.value:
        lines.append("\nExecution stopped after a failure (no further actions run):")
        for s in audit.executed_steps:
            lines.append(f"  - {s.action_id} [{s.status}]: {s.output or s.error}")
    else:  # success / partial
        lines.append("\nResults:")
        for s in audit.executed_steps:
            lines.append(f"  - {s.action_id} [{s.status}]: {s.output or s.error}")

    return "\n".join(lines).strip()


# Local helpers removed; ExecutionStatus imported at module top.
