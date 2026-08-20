"""Phase 9G deterministic tests: thin Chat/Brain bridge to the research workflow.

Exercises ResearchBridge (the connector) offline using injected fakes:
  * FakeResearchWorkflow  -> returns a scripted ExecutionAudit
  * FakeDecider           -> ACCEPT / DENY / ABORT
  * a real ResearchWorkflow with fakes is also used for end-to-end routing

Covered (all required scenarios):
  * routing: research-only request
  * routing: research + action request
  * routing: ordinary request -> NONE (not routed)
  * confirmation accepted   (research+action -> execute)
  * confirmation denied
  * confirmation aborted
  * workflow failure
  * existing ordinary Jarvis requests continue working unchanged (brain hook)

The bridge does NOT duplicate pipeline/planner/executor/permission logic; it
only routes + renders.
"""

from __future__ import annotations

import pytest

from research.bridge import ResearchBridge, ResearchIntent, render_research_response
from research.orchestrator import Decision
from proposal.executor import ExecutionAudit, ExecutionStatus, StepStatus

from tests.research.test_orchestrator_phase9f import (
    _findings,
    FakeResearchPipeline,
    FakePlanSynthesizer,
    FakeDecider,
)
from research.planner import ResearchPlan, PlanStep, PlanStatus
from tests.proposal.test_executor_phase9e import (
    _StubTool,
    _StubRegistry,
    _StubPermissionManager,
)
from research.pipeline import ResearchFindings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_audit(final_status, *, with_steps=True, with_findings=True, with_plan=True):
    # Build an ExecutionAudit directly.
    from proposal.executor import ExecutedStep
    audit = ExecutionAudit(
        proposal_id="p1",
        objective="Research X",
        final_status=final_status.value if hasattr(final_status, "value") else final_status,
    )
    if with_findings:
        audit.research_findings = _findings()
    if with_plan:
        audit.plan = ResearchPlan(
            objective="Research X",
            steps=[PlanStep(step_id="s1", tool="calculator", expected_result="42", rationale="r")],
            status=PlanStatus.VALIDATED,
        )
    if with_steps:
        audit.executed_steps = [
            ExecutedStep(action_id="s1", tool="calculator", order=1,
                         status=StepStatus.EXECUTED.value, output="42", confirmation_decision=True),
        ]
    return audit


class FakeResearchWorkflow:
    """Returns a scripted ExecutionAudit (no real research/plan/exec)."""

    def __init__(self, audit, *, prompt_seen=None):
        self._audit = audit
        self.prompt_seen = prompt_seen if prompt_seen is not None else []

    def run(self, objective, *, research=True, findings=None, context="", limits=None, decider=None):
        self.prompt_seen.append(objective)
        self.last_decider = decider
        return self._audit


# ---------------------------------------------------------------------------
# Routing classification
# ---------------------------------------------------------------------------
@pytest.mark.offline
class TestRouting:
    def test_research_only_request_routed(self):
        assert ResearchBridge.classify("Research the best laptops under $1000") == ResearchIntent.RESEARCH_ONLY

    def test_research_action_request_routed(self):
        assert ResearchBridge.classify("Research X and figure out what I should do") == ResearchIntent.RESEARCH_ACTION
        assert ResearchBridge.classify("Investigate and recommend actions to speed up my PC") == ResearchIntent.RESEARCH_ACTION

    def test_ordinary_request_not_routed(self):
        assert ResearchBridge.classify("What is 2+2?") == ResearchIntent.NONE
        assert ResearchBridge.classify("Hello, how are you?") == ResearchIntent.NONE
        assert ResearchBridge.classify("Open Chrome") == ResearchIntent.NONE


# ---------------------------------------------------------------------------
# Bridge handle behavior
# ---------------------------------------------------------------------------
@pytest.mark.offline
class TestBridgeHandle:
    def test_research_only_returns_findings_plan_no_execution(self):
        audit = _make_audit(ExecutionStatus.RESEARCH_ONLY, with_steps=False)
        wf = FakeResearchWorkflow(audit)
        bridge = ResearchBridge(wf)
        out = bridge.handle("Research the best laptops", ResearchIntent.RESEARCH_ONLY)
        assert "no actions executed" in out
        assert "Sources:" in out  # citations preserved
        assert "Plan" in out
        # No execution happened (workflow returned research_only audit).
        assert wf.last_decider is not None  # internal research-only decider used

    def test_confirmation_accepted_executes(self):
        audit = _make_audit(ExecutionStatus.SUCCESS)
        wf = FakeResearchWorkflow(audit)
        decider = FakeDecider(Decision.ACCEPT)
        bridge = ResearchBridge(wf, decider=decider)
        out = bridge.handle("Research X and what should I do", ResearchIntent.RESEARCH_ACTION, decider=decider)
        assert "Results:" in out
        assert "42" in out  # execution output preserved

    def test_confirmation_denied_executes_nothing(self):
        audit = _make_audit(ExecutionStatus.DENIED, with_steps=False)
        wf = FakeResearchWorkflow(audit)
        decider = FakeDecider(Decision.DENY)
        bridge = ResearchBridge(wf, decider=decider)
        out = bridge.handle("Research X and what should I do", ResearchIntent.RESEARCH_ACTION, decider=decider)
        assert "No actions were executed" in out
        assert "denied" in out

    def test_confirmation_aborted_executes_nothing(self):
        audit = _make_audit(ExecutionStatus.ABORTED, with_steps=False)
        wf = FakeResearchWorkflow(audit)
        decider = FakeDecider(Decision.ABORT)
        bridge = ResearchBridge(wf, decider=decider)
        out = bridge.handle("Research X and what should I do", ResearchIntent.RESEARCH_ACTION, decider=decider)
        assert "No actions were executed" in out
        assert "aborted" in out

    def test_workflow_failure_reported(self):
        audit = _make_audit(ExecutionStatus.FAILED)
        wf = FakeResearchWorkflow(audit)
        decider = FakeDecider(Decision.ACCEPT)
        bridge = ResearchBridge(wf, decider=decider)
        out = bridge.handle("Research X and what should I do", ResearchIntent.RESEARCH_ACTION, decider=decider)
        assert "stopped after a failure" in out
        assert "42" in out  # the failed step's output/error still shown

    def test_no_decider_for_action_request_is_safe(self):
        # Without a decider, research+action must NOT auto-confirm; it returns a
        # safe message rather than executing.
        audit = _make_audit(ExecutionStatus.SUCCESS)
        wf = FakeResearchWorkflow(audit)
        bridge = ResearchBridge(wf)  # no decider
        out = bridge.handle("Research X and what should I do", ResearchIntent.RESEARCH_ACTION)
        assert "explicit confirmation" in out
        # The workflow was never invoked (no autonomous execution).
        assert wf.prompt_seen == []


# ---------------------------------------------------------------------------
# Brain hook: ordinary requests unchanged + research routed
# ---------------------------------------------------------------------------
@pytest.mark.offline
class TestBrainHook:
    def _make_brain(self, *, research_decider=None, provider_answer="plain answer"):
        from modules.config import JarvisConfig
        from modules.brain import JarvisBrain

        config = JarvisConfig()
        # Minimal tool registry stub.
        reg = _StubRegistry([_StubTool("calculator", output="1")])
        pm = _StubPermissionManager(allow=True, level="SAFE")

        # Lightweight fake memory (brain.run calls memory.get_recent_context).
        memory = _FakeMemory()

        # Build a real ResearchWorkflow with fakes so the bridge works offline.
        findings = _findings()
        plan = ResearchPlan(
            objective="o", steps=[PlanStep(step_id="s1", tool="calculator", expected_result="1", rationale="r")],
            status=PlanStatus.VALIDATED,
        )
        from research.orchestrator import ResearchWorkflow
        wf = ResearchWorkflow(
            config=config,
            research_pipeline=FakeResearchPipeline(findings=findings),
            planner=_make_planner(plan),
            executor=_make_executor(reg, pm, findings, plan),
            decider=research_decider,
            tool_registry=reg,
            permission_manager=pm,
        )
        brain = JarvisBrain(config=config, tools=reg, memory=memory, research_decider=research_decider)
        # Inject our workflow into the brain's bridge (skip lazy build / network).
        from research.bridge import ResearchBridge
        brain._research_bridge = ResearchBridge(wf, decider=research_decider)
        # Stub the LLM provider so ordinary requests don't hit the network.
        brain.llm_provider = _FakeProvider(provider_answer)
        brain._healthy = lambda: True
        return brain

    def test_ordinary_request_unchanged(self):
        brain = self._make_brain()
        # "What is 2+2?" is classified NONE -> ordinary path -> provider answer.
        out = brain.run("What is 2+2?")
        assert out == "plain answer"

    def test_research_request_routed_through_bridge(self):
        brain = self._make_brain(research_decider=FakeDecider(Decision.ACCEPT))
        out = brain.run("Research the best laptops and figure out what I should do")
        # Routed to the workflow, not the plain LLM.
        assert "Research:" in out
        assert "Plan" in out


# ---------------------------------------------------------------------------
# Planner/executor fakes for the brain hook
# ---------------------------------------------------------------------------
def _make_planner(plan):
    from research.planner import ResearchPlanner
    synth = FakePlanSynthesizer(plan)
    return ResearchPlanner(config=None, synthesizer=synth)


def _make_executor(reg, pm, findings, plan):
    from proposal.executor import ProposalExecutor
    return ProposalExecutor(tool_registry=reg, permission_manager=pm)


class _FakeProvider:
    def __init__(self, answer):
        self._answer = answer

    def is_available(self):
        return True

    def chat(self, messages, *, stream=False, **kwargs):
        return self._answer

    def stream_chat(self, messages, **kwargs):
        yield self._answer


class _FakeMemory:
    """Minimal memory stub so JarvisBrain.run() works offline."""
    def get_recent_context(self):
        return ""
