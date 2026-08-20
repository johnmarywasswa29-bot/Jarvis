"""Phase 9F deterministic tests: closed-loop research -> plan -> proposal -> exec.

Exercises ResearchWorkflow (the coordinator) offline with injected fakes:
  * FakeResearchSynthesizer  -> deterministic ResearchFindings with a source
  * FakePlanSynthesizer       -> deterministic validated ResearchPlan
  * stub ToolRegistry + PermissionManager (from 9E patterns)
  * FakeDecider               -> ACCEPT / DENY / ABORT

Covered (all required scenarios):
  * research -> plan -> proposal -> execution (accept)
  * confirmation accepted
  * confirmation denied
  * invalid proposal (plan not validated)
  * failed execution (tool fails)
  * dependency ordering
  * safe / read-only workflow (SAFE tool runs without extra confirmation)
  * audit preservation (findings, plan, proposal carried through)
  * abort before execution
"""

from __future__ import annotations

import pytest

from research.orchestrator import (
    Decision,
    ResearchWorkflow,
    UserDecider,
    summarize_proposal,
)
from research.pipeline import (
    ResearchFindings,
    ResearchSource,
    ResearchLimits,
    ResearchPipeline,
    ResearchSynthesizer,
)
from research.planner import (
    ResearchPlan,
    ResearchPlanner,
    PlanStep,
    PlanSynthesizer,
    PlanStatus,
)
from proposal.executor import ExecutionStatus, StepStatus
from proposal.state import ProposalStatus

from tests.proposal.test_executor_phase9e import (
    _StubTool,
    _StubRegistry,
    _StubPermissionManager,
)


# -----------------------------------------------------------------------------
# Fakes
# -----------------------------------------------------------------------------
class FakeResearchSynthesizer(ResearchSynthesizer):
    """Deterministic findings: one successful cited source."""

    def research(self, query, limits=None):
        raise NotImplementedError

    def identify_gaps(self, question, sources):
        return []

    def synthesize(self, question, sources, gaps=None):
        return "Synthesized answer. [Source 1]"


class FakePlanSynthesizer(PlanSynthesizer):
    def __init__(self, plan):
        self._plan = plan

    def synthesize_plan(self, findings, context=""):
        return self._plan


class FakeDecider(UserDecider):
    def __init__(self, choice: Decision):
        self.choice = choice
        self.last_plan = None
        self.last_proposal = None

    def decide(self, objective, plan, proposal):
        self.last_plan = plan
        self.last_proposal = proposal
        return self.choice


def _findings():
    src = ResearchSource(
        title="T", url="https://x.com/1", search_query="q",
        fetch_status="success", extracted_text="evidence",
    )
    return ResearchFindings(
        query="obj", sources=[src], synthesis="Synthesized answer. [Source 1]",
        confidence=0.8,
    )


class FakeResearchPipeline:
    """Deterministic research step: returns fixed findings (9A-9C tested real)."""

    def __init__(self, findings=None):
        self._findings = findings or _findings()

    def research(self, query, limits=None):
        return self._findings


def _plan(steps, objective="obj"):
    return ResearchPlan(objective=objective, steps=steps, status=PlanStatus.VALIDATED)


def _workflow(plan_steps, decider_choice, *, tool_behavior=None, allow=True, level="SAFE"):
    """Build a ResearchWorkflow with fakes wired end-to-end."""
    findings = _findings()
    # The planner's synthesizer returns our fixed validated plan.
    planner_synth = FakePlanSynthesizer(_plan(plan_steps))
    # The pipeline's synthesizer returns deterministic findings.
    pipeline_synth = FakeResearchSynthesizer()

    # Build tools from plan steps.
    tools = []
    for s in plan_steps:
        tb = tool_behavior.get(s.tool) if tool_behavior else None
        if tb is None:
            tb = {"success": True, "output": f"out-{s.tool}", "error": ""}
        tools.append(_StubTool(s.tool, **tb))
    reg = _StubRegistry(tools)
    pm = _StubPermissionManager(allow=allow, level=level)

    pipeline = FakeResearchPipeline(findings=findings)
    findings_to_use = findings
    planner = ResearchPlanner(
        config=None, tool_registry=reg, permission_manager=pm, synthesizer=planner_synth
    )
    # Executor uses the same reg/pm (reused).
    from proposal.executor import ProposalExecutor
    executor = ProposalExecutor(tool_registry=reg, permission_manager=pm)
    decider = FakeDecider(decider_choice)
    wf = ResearchWorkflow(
        config=None, research_pipeline=pipeline, planner=planner,
        executor=executor, decider=decider, tool_registry=reg, permission_manager=pm,
    )
    return wf, decider, findings


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestFullLoop:
    def test_research_to_plan_to_proposal_to_execution(self):
        steps = [PlanStep(step_id="s1", tool="calculator",
                          parameters={"expression": "6*7"},
                          expected_result="42", rationale="r")]
        wf, decider, _ = _workflow(steps, Decision.ACCEPT, level="SAFE")
        audit = wf.run("Compute 6*7")
        assert audit.final_status == ExecutionStatus.SUCCESS.value
        assert audit.executed_steps[0].output == "out-calculator"
        # Decider was asked.
        assert decider.last_plan is not None
        assert decider.last_proposal is not None

    def test_confirmation_accepted(self):
        steps = [PlanStep(step_id="s1", tool="calculator",
                          parameters={"expression": "1+1"},
                          expected_result="2", rationale="r")]
        wf, decider, _ = _workflow(steps, Decision.ACCEPT, level="SAFE")
        audit = wf.run("x")
        assert audit.final_status == ExecutionStatus.SUCCESS.value
        assert audit.metadata.get("decision") == Decision.ACCEPT.value

    def test_confirmation_denied(self):
        steps = [PlanStep(step_id="s1", tool="calculator",
                          parameters={"expression": "1+1"},
                          expected_result="2", rationale="r")]
        wf, decider, _ = _workflow(steps, Decision.DENY, level="SAFE")
        audit = wf.run("x")
        assert audit.final_status == ExecutionStatus.DENIED.value
        # Nothing executed.
        assert audit.executed_steps == []
        assert audit.confirmation_decisions[0].decision is False

    def test_abort_before_execution(self):
        steps = [PlanStep(step_id="s1", tool="calculator",
                          parameters={"expression": "1+1"},
                          expected_result="2", rationale="r")]
        wf, decider, _ = _workflow(steps, Decision.ABORT, level="SAFE")
        audit = wf.run("x")
        assert audit.final_status == ExecutionStatus.ABORTED.value
        assert audit.executed_steps == []
        assert audit.metadata.get("decision") == Decision.ABORT.value

    def test_no_autonomous_execution_without_decider(self):
        wf, _, _ = _workflow([PlanStep(step_id="s1", tool="calculator",
                                       parameters={"expression": "1"},
                                       expected_result="1", rationale="r")],
                             Decision.ACCEPT, level="SAFE")
        wf.decider = None
        with pytest.raises(RuntimeError):
            wf.run("x")


@pytest.mark.offline
class TestInvalidAndFailed:
    def test_invalid_proposal_returns_invalid_audit(self):
        # A plan that fails validation: duplicate step ids.
        steps = [
            PlanStep(step_id="s1", tool="calculator", expected_result="1", rationale="r"),
            PlanStep(step_id="s1", tool="calculator", expected_result="1", rationale="r"),
        ]
        wf, decider, _ = _workflow(steps, Decision.ACCEPT, level="SAFE")
        audit = wf.run("x")
        # Duplicate ids -> plan rejected -> INVALID audit, nothing executed.
        assert audit.final_status == ExecutionStatus.INVALID.value
        assert audit.executed_steps == []

    def test_failed_execution_propagates(self):
        steps = [PlanStep(step_id="s1", tool="calculator",
                          parameters={"expression": "1/0"},
                          expected_result="err", rationale="r")]
        wf, decider, _ = _workflow(
            steps, Decision.ACCEPT, level="SAFE",
            tool_behavior={"calculator": {"success": False, "error": "div by zero"}},
        )
        audit = wf.run("x")
        assert audit.final_status == ExecutionStatus.FAILED.value
        assert audit.executed_steps[0].status == StepStatus.FAILED.value


@pytest.mark.offline
class TestOrderingAndSafe:
    def test_dependency_ordering_preserved(self):
        steps = [
            PlanStep(step_id="s1", tool="web_search",
                     parameters={"query": "q"}, expected_result="r1", rationale="r"),
            PlanStep(step_id="s2", tool="calculator", dependencies=["s1"],
                     parameters={"expression": "2"}, expected_result="r2", rationale="r"),
        ]
        wf, decider, _ = _workflow(steps, Decision.ACCEPT, level="SAFE")
        audit = wf.run("x")
        assert audit.final_status == ExecutionStatus.SUCCESS.value
        assert [s.action_id for s in audit.executed_steps] == ["s1", "s2"]

    def test_safe_read_only_workflow_no_extra_confirmation(self):
        # SAFE tool: the executor lets it run without a confirmation prompt.
        steps = [PlanStep(step_id="s1", tool="calculator",
                          parameters={"expression": "3*3"},
                          expected_result="9", rationale="r")]
        wf, decider, _ = _workflow(steps, Decision.ACCEPT, level="SAFE")
        audit = wf.run("x")
        assert audit.final_status == ExecutionStatus.SUCCESS.value
        assert audit.executed_steps[0].status == StepStatus.EXECUTED.value


@pytest.mark.offline
class TestAuditPreservation:
    def test_findings_plan_proposal_preserved(self):
        steps = [PlanStep(step_id="s1", tool="calculator",
                          parameters={"expression": "1"},
                          expected_result="1", rationale="r")]
        wf, decider, findings = _workflow(steps, Decision.ACCEPT, level="SAFE")
        audit = wf.run("x")
        # Citations / findings carried through the whole loop.
        assert audit.research_findings is not None
        assert audit.research_findings.get_citations()
        assert audit.plan is not None
        assert audit.proposal is not None
        assert audit.proposal.status == ProposalStatus.VALIDATED

    def test_summarize_proposal_surface(self):
        steps = [
            PlanStep(step_id="s1", tool="calculator",
                     expected_result="1", rationale="r",
                     risk_level="low", confirmation_requirement=False),
            PlanStep(step_id="s2", tool="code_execution",
                     expected_result="x", rationale="r",
                     risk_level="high", confirmation_requirement=True),
        ]
        plan = _plan(steps)
        wf, _, _ = _workflow(steps, Decision.ACCEPT, level="SAFE")
        proposal = wf.planner.to_proposal(plan)
        summary = summarize_proposal(plan, proposal)
        assert summary["objective"] == "obj"
        assert len(summary["steps"]) == 2
        tools = {s["tool"] for s in summary["steps"]}
        assert tools == {"calculator", "code_execution"}
        # Risk/permission requirements are surfaced for the user to review.
        ce = [s for s in summary["steps"] if s["tool"] == "code_execution"][0]
        assert ce["risk_level"] == "high"
        assert ce["confirmation_required"] is True
