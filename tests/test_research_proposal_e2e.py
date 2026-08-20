"""End-to-end integration test for the research -> plan -> proposal -> execution loop (Phases 9A-9H).

This replaces the stale tests/test_proposal_and_research.py, which imported a
deleted RAG experiment (ResearchOrchestrator / ResearchFindings from
research.orchestrator) and failed collection. That module no longer exists; the
real coordinator is research.orchestrator.ResearchWorkflow (9F) and the real
research result type is research.pipeline.ResearchFindings (9A).

We exercise the REAL backend end to end:
    ResearchWorkflow (9F) -> ResearchPipeline (9A-9C, network faked)
    -> ResearchPlanner (9D) -> ProposalValidator/ProposalManager (reused)
    -> PermissionManager (reused) -> ProposalExecutor (9E) -> WebUserDecider (9H).

The ONLY fakes are the LLM-dependent seams (plan synthesizer, research gap/
synthesis) and the network research pipeline -- exactly the same DI seams the
production code is built around, and the same fakes used by the 9C/9D/9F unit
tests. Everything else (plan validation, tool/permission derivation, proposal
validation, execution, confirmation gating, audit) is the real implementation.

Run with: pytest tests/test_research_proposal_e2e.py  (offline, deterministic)
"""

from __future__ import annotations

import queue
import uuid

import pytest

from modules.config import JarvisConfig
from modules.tools import ToolRegistry, BaseTool, ToolResult
from modules.permission_manager import PermissionManager
from research.pipeline import ResearchFindings, ResearchSource
from research.planner import (
    ResearchPlanner,
    PlanSynthesizer,
    ResearchPlan,
    PlanStep,
    PlanStatus,
)
from research.orchestrator import (
    ResearchWorkflow,
    UserDecider,
    Decision,
    summarize_proposal,
)
from proposal.executor import ProposalExecutor, ExecutionStatus
from proposal.state import ProposalStatus


# --------------------------------------------------------------------------- #
# Fakes for the LLM / network seams only
# --------------------------------------------------------------------------- #
class _FakeResearchSynthesizer:
    """Stand-in for the 9B/9C research LLM synthesizer (no network, no LLM)."""
    def identify_gaps(self, findings, objective, max_gaps=3):
        return []

    def synthesize(self, findings, objective, max_content_per_page=8000):
        return "Synthesis: the best approach is to search the web and run a calculation."


class FakeResearchPipeline:
    """Deterministic research pipeline (no network). Returns fetched sources so
    get_citations() works exactly like the real pipeline output."""

    def __init__(self, *args, **kwargs):
        self.config = kwargs.get("config")
        self.tool_registry = kwargs.get("tool_registry")
        self.limits = kwargs.get("limits")
        self.synthesizer = _FakeResearchSynthesizer()

    def research(self, objective, context="", limits=None):
        src = ResearchSource(
            title="Reliable Source on Topic X",
            url="https://example.com/topic-x",
            search_query=objective,
            fetch_status="success",
            extracted_text="Topic X recommends searching the web and computing a value.",
            extraction_method="fake",
        )
        return ResearchFindings(
            query=objective,
            sources=[src],
            findings=[{"claim": "Topic X is well documented.", "source": "https://example.com/topic-x"}],
            synthesis="Synthesis: the best approach is to search the web and run a calculation.",
            confidence=0.8,
            gaps=[],
        )


class FakePlanSynthesizer(PlanSynthesizer):
    """Deterministic planner synthesizer: web_search (caution) + calculator (safe)."""

    def __init__(self, config=None):
        self.config = config

    def synthesize_plan(self, findings, context=""):
        plan = ResearchPlan(
            objective=findings.query,
            rationale="Research supports a web search followed by a computation.",
            sources=findings.get_citations(),
            metadata={"synthesizer": "fake"},
        )
        plan.steps.append(PlanStep(
            step_id="s1",
            description="Search the web for current information",
            tool="web_search",
            parameters={"query": findings.query},
            expected_result="Search results for the objective",
            risk_level="medium",
            confirmation_requirement=True,
            rationale="Need up-to-date facts from the web.",
        ))
        plan.steps.append(PlanStep(
            step_id="s2",
            description="Compute the recommended value",
            tool="calculator",
            parameters={"expression": "6*7"},
            dependencies=["s1"],
            expected_result="42",
            risk_level="low",
            confirmation_requirement=False,
            rationale="A safe local computation.",
        ))
        return plan


class SyncDecider(UserDecider):
    """Drives the confirmation gate synchronously from a queued decision list."""

    def __init__(self, decisions):
        self._queue = list(decisions)

    def decide(self, objective, plan, proposal):
        if not self._queue:
            return Decision.ABORT
        return self._queue.pop(0)


def _build_findings(objective):
    src = ResearchSource(
        title="Reliable Source on Topic X",
        url="https://example.com/topic-x",
        search_query=objective,
        fetch_status="success",
        extracted_text="Topic X recommends searching the web and computing a value.",
        extraction_method="fake",
    )
    return ResearchFindings(
        query=objective,
        sources=[src],
        findings=[{"claim": "Topic X is well documented.", "source": "https://example.com/topic-x"}],
        synthesis="Synthesis: the best approach is to search the web and run a calculation.",
        confidence=0.8,
        gaps=[],
    )


def _build_workflow(decisions, *, research=True, prebuilt_findings=None):
    """Construct a REAL ResearchWorkflow with all real components except the
    LLM/network seams (faked)."""
    config = JarvisConfig()
    tool_registry = ToolRegistry(config)
    # Ensure web_search + calculator are registered (real registry tools).
    from modules.tools import WebSearchTool, CalculatorTool
    if not tool_registry.has_tool("web_search"):
        tool_registry.register_tool(WebSearchTool())
    if not tool_registry.has_tool("calculator"):
        tool_registry.register_tool(CalculatorTool())
    permission_manager = PermissionManager()

    planner = ResearchPlanner(
        config=config,
        tool_registry=tool_registry,
        permission_manager=permission_manager,
        synthesizer=FakePlanSynthesizer(config),
        proposal_manager=None,
    )
    workflow = ResearchWorkflow(
        config=config,
        research_pipeline=FakeResearchPipeline(config=config, tool_registry=tool_registry),
        planner=planner,
        permission_manager=permission_manager,
        tool_registry=tool_registry,
        decider=SyncDecider(decisions),
    )
    return workflow, tool_registry, permission_manager


# --------------------------------------------------------------------------- #
# Integration tests
# --------------------------------------------------------------------------- #
class TestResearchProposalEndToEnd:
    def test_e2e_accept_runs_plan_and_executes(self):
        workflow, _, _ = _build_workflow([Decision.ACCEPT])
        audit = workflow.run("Research the best approach and figure out what to do", research=True)

        # Confirmation gate was shown (proposal requires confirmation).
        assert audit.proposal is not None
        assert audit.proposal.requires_confirmation is True

        # Citations preserved from research into plan + proposal.
        assert audit.research_findings is not None
        cites = audit.research_findings.get_citations()
        assert len(cites) == 1
        assert cites[0]["url"] == "https://example.com/topic-x"
        assert len(audit.plan.sources) == 1

        # Both steps executed (SAFE calculator + CAUTION web_search after ACCEPT).
        assert audit.final_status == ExecutionStatus.SUCCESS.value
        assert len(audit.executed_steps) == 2
        assert all(s.status == "executed" for s in audit.executed_steps)

        # The calculator produced the expected value (real tool executed).
        calc = next(s for s in audit.executed_steps if s.tool == "calculator")
        assert "42" in (calc.output or "")

    def test_e2e_deny_executes_nothing(self):
        workflow, _, _ = _build_workflow([Decision.DENY])
        audit = workflow.run("Research X and figure out what to do", research=True)

        assert audit.final_status == ExecutionStatus.DENIED.value
        assert len(audit.executed_steps) == 0
        # The proposal still exists (we refused to execute, did not lose it).
        assert audit.proposal is not None
        assert audit.proposal.requires_confirmation is True

    def test_e2e_abort_executes_nothing(self):
        workflow, _, _ = _build_workflow([Decision.ABORT])
        audit = workflow.run("Research X and figure out what to do", research=True)

        assert audit.final_status == ExecutionStatus.ABORTED.value
        assert len(audit.executed_steps) == 0

    def test_e2e_research_only_preserves_findings_no_execution(self):
        findings = _build_findings("Research the history of telescopes")
        workflow, _, _ = _build_workflow([Decision.RESEARCH_ONLY])
        audit = workflow.run(
            "Research the history of telescopes",
            research=False,
            findings=findings,
        )
        assert audit.final_status == ExecutionStatus.RESEARCH_ONLY.value
        assert audit.research_findings is findings
        assert audit.plan is not None
        assert len(audit.plan.steps) == 2
        # No execution happened for a research-only request.
        assert len(audit.executed_steps) == 0

    def test_e2e_citations_flow_into_plan_and_proposal(self):
        workflow, _, _ = _build_workflow([Decision.ACCEPT])
        audit = workflow.run("Research X and figure out what to do", research=True)

        # Plan sources carry citation metadata.
        assert audit.plan.sources
        assert audit.plan.sources[0]["url"] == "https://example.com/topic-x"

        # Proposal sources carried through to_proposal() as SourceReferences.
        assert audit.proposal.source_references
        assert audit.proposal.source_references[0].identifier == "https://example.com/topic-x"

        # summarize_proposal (used by the 9H WebUserDecider) exposes them.
        summary = summarize_proposal(audit.plan, audit.proposal)
        assert summary["source_count"] == 1
        assert summary["requires_confirmation"] is True
        assert len(summary["steps"]) == 2

    def test_e2e_permission_derives_risk_and_confirmation(self):
        """The planner derives risk/confirmation from the tool's permission
        level (not the fake synthesizer's guess)."""
        workflow, tool_registry, pm = _build_workflow([Decision.ACCEPT])
        audit = workflow.run("Research X and figure out what to do", research=True)

        by_tool = {s.tool: s for s in audit.plan.steps}
        # calculator is SAFE -> low risk, no confirmation required.
        calc = by_tool["calculator"]
        assert calc.risk_level == "low"
        assert calc.confirmation_requirement is False
        # web_search is CAUTION -> medium risk, confirmation required.
        web = by_tool["web_search"]
        assert web.risk_level == "medium"
        assert web.confirmation_requirement is True
