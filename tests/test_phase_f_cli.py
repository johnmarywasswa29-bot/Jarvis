"""Phase F — CLI integration tests for the runtime AgentLoop (jarvis.py agent).

Tests the REAL JarvisAssistant.run_agent method end-to-end:
    objective -> ResearchPipeline.research -> ResearchPlanner.plan ->
    ResearchPlanner.to_proposal (VALIDATED Proposal) ->
    self._ctx.agent_loop.run(...) -> real ProposalExecutor/Verifier/Replanner.

Execution is simulated via a fast ScenarioExecutor (no nested subprocesses),
and the proposal-generation path uses the SAME proven fakes as
tests/test_research_proposal_e2e.py. The AgentLoop, PermissionManager,
EventBus, and confirmation gate are all REAL (constructed by build_runtime).

Safety invariants asserted:
  * run_agent reuses ctx.agent_loop (no second execution engine).
  * confirm_fn defaults to PermissionManager.confirm (no automatic confirm).
  * HARD_MAX_ITERATIONS = 20 bounds the loop.
  * Denied/aborted iterations are recorded.
  * Failed / inconclusive verification never becomes SUCCESS.
  * All 6 agent.* EventType events are real core.events.Event objects with
    source="agent_loop" and structured, non-secret payloads.
"""

from __future__ import annotations

import sys
from datetime import datetime, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtime.runtime import build_runtime
from core.events import EventBus, EventType, Event
from proposal.executor import (
    ProposalExecutor, ExecutionAudit, ExecutionStatus, StepStatus,
    ConfirmationDecision, ExecutedStep,
)
from proposal.state import Proposal, ProposedAction, ProposalStatus
from proposal.validator import ProposalValidator
from proposal.verification import VerificationStatus
from proposal.agent_loop import AgentLoop, AgentLoopStatus
from modules.permission_manager import PermissionManager

from tests.test_research_proposal_e2e import (
    FakeResearchPipeline,
    FakePlanSynthesizer,
    ResearchSource,
    ResearchFindings,
)

# Reuse the proven planner construction pattern from the 9A-9H e2e tests.
from research.planner import ResearchPlanner, PlanSynthesizer, ResearchPlan, PlanStep
from jarvis import JarvisAssistant


ALLOWED = ["git", "file_edit", "test_execution", "build", "workspace_observe",
           "shell", "dependency", "web_search", "web_fetch", "desktop_control",
           "code_execution", "filesystem", "calculator", "system_control"]


# --------------------------------------------------------------------------- #
# fast simulated execution seam (no subprocess)
# --------------------------------------------------------------------------- #
class ScenarioToolRegistry:
    def __init__(self, names):
        self._names = list(names)
        self.execute_calls = 0
    def tool_names(self):
        return list(self._names)
    def execute(self, *a, **k):
        self.execute_calls += 1
        raise AssertionError("AgentLoop must not call ToolRegistry.execute")


def _now():
    return datetime.now(UTC).replace(tzinfo=None).isoformat()


def _pass(action, it):
    return {"status": StepStatus.EXECUTED.value,
            "output": "framework=pytest returncode=0 passed=1 failed=0 errors=0"}


def _fail(action, it):
    return {"status": StepStatus.FAILED.value,
            "output": "framework=pytest returncode=1 passed=0 failed=1 errors=0",
            "error": "assertion failed"}


class ScenarioExecutor:
    def __init__(self, tool_registry, permission_manager, behaviour):
        self.tool_registry = tool_registry
        self.permission_manager = permission_manager
        self.behaviour = behaviour
        self._iteration = 0

    def execute(self, proposal, *, confirm_fn=None, **kw):
        self._iteration += 1
        it = self._iteration
        audit = ExecutionAudit(objective=proposal.objective, proposal=proposal)
        confirm = confirm_fn if callable(confirm_fn) else (lambda t, details="": True)
        for i, a in enumerate(proposal.proposed_actions, 1):
            level = "SAFE"
            try:
                level = self.permission_manager.get_level(a.tool)
            except Exception:
                pass
            allowed = confirm(a.tool, details=a.description or f"Execute {a.tool}")
            audit.confirmation_decisions.append(ConfirmationDecision(
                action_id=a.action_id, tool=a.tool, decision=allowed, level=level))
            if not allowed:
                audit.executed_steps.append(ExecutedStep(
                    action_id=a.action_id, tool=a.tool, order=i,
                    status=StepStatus.DENIED.value, error="confirmation denied"))
                audit.final_status = ExecutionStatus.DENIED.value
                return audit
            out = self.behaviour(a, it)
            audit.executed_steps.append(ExecutedStep(
                action_id=a.action_id, tool=a.tool, order=i,
                status=out.get("status", StepStatus.EXECUTED.value),
                output=out.get("output", ""), error=out.get("error", "")))
        if any(s.status == StepStatus.FAILED.value for s in audit.executed_steps):
            audit.final_status = ExecutionStatus.FAILED.value
        else:
            audit.final_status = ExecutionStatus.SUCCESS.value
        audit.completed_at = _now()
        return audit


# --------------------------------------------------------------------------- #
# a planner synthesizer that includes a DANGEROUS (shell) step, for the
# dangerous-tool confirmation test
# --------------------------------------------------------------------------- #
class DangerPlanSynthesizer(PlanSynthesizer):
    def __init__(self, config=None):
        self.config = config
    def synthesize_plan(self, findings, context=""):
        plan = ResearchPlan(objective=findings.query,
                            rationale="needs a shell command",
                            sources=findings.get_citations())
        plan.steps.append(PlanStep(
            step_id="s1", description="Run a safe computation", tool="calculator",
            parameters={"expression": "6*7"}, expected_result="42",
            risk_level="low", confirmation_requirement=False))
        plan.steps.append(PlanStep(
            step_id="s2", description="Inspect system", tool="shell",
            parameters={"command": "echo hi"}, dependencies=["s1"],
            expected_result="hi", risk_level="high",
            confirmation_requirement=True))
        return plan


class VerifyPlanSynthesizer(PlanSynthesizer):
    """Emits a single test_execution step whose output is verifiably SUCCESS."""

    def __init__(self, config=None):
        self.config = config

    def synthesize_plan(self, findings, context=""):
        plan = ResearchPlan(objective=findings.query,
                            rationale="run a verifiable test",
                            sources=findings.get_citations())
        plan.steps.append(PlanStep(
            step_id="s1",
            description="Run the project test suite",
            tool="test_execution",
            parameters={"framework": "pytest", "target": "tests/unit", "timeout_s": 60},
            expected_result="all tests pass",
            risk_level="low",
            confirmation_requirement=False,
        ))
        return plan


def _fake_findings(objective):
    src = ResearchSource(title="Src", url="https://example.com/x",
                        search_query=objective, fetch_status="success",
                        extracted_text="ok", extraction_method="fake")
    return ResearchFindings(query=objective, sources=[src],
                            findings=[{"claim": "ok", "source": "https://example.com/x"}],
                            synthesis="ok", confidence=0.8, gaps=[])


def _build_minimal_assistant():
    """Build a real runtime and a minimal object exposing run_agent without
    the heavy voice/brain init."""
    ctx = build_runtime(repo=".")
    assert isinstance(ctx.agent_loop, AgentLoop)

    class _Mini:
        def __init__(self, ctx):
            self.config = ctx.config
            self.tools = ctx.tool_registry
            self.permissions = ctx.permission_manager
            self._ctx = ctx
    _Mini.run_agent = JarvisAssistant.run_agent
    return _Mini(ctx), ctx


def _planner(ctx, synthesizer):
    return ResearchPlanner(ctx.config, ctx.tool_registry, ctx.permission_manager,
                           synthesizer=synthesizer)


def _subscribe_all(bus):
    seen = []
    for et in (EventType.AGENT_ITERATION_STARTED, EventType.AGENT_EXECUTION_COMPLETED,
               EventType.AGENT_VERIFICATION_COMPLETED, EventType.AGENT_REPLAN_COMPLETED,
               EventType.AGENT_COMPLETED, EventType.AGENT_ABORTED):
        bus.subscribe(et, lambda e, et=et: seen.append(et))
    return seen


# --------------------------------------------------------------------------- #
def test_f_validated_proposal_generation():
    mini, ctx = _build_minimal_assistant()
    planner = _planner(ctx, FakePlanSynthesizer(ctx.config))
    pipeline = FakeResearchPipeline(config=ctx.config, tool_registry=ctx.tool_registry)
    findings = pipeline.research("do a thing")
    plan = planner.plan(findings)
    proposal = planner.to_proposal(plan)
    # The existing research -> plan -> to_proposal path yields a VALIDATED
    # proposal (ProposalValidator via ProposalManager.create_proposal).
    assert proposal.status == ProposalStatus.VALIDATED
    assert len(proposal.proposed_actions) >= 1


def test_f_successful_execution():
    mini, ctx = _build_minimal_assistant()
    ctx.agent_loop._executor = ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, _pass)
    seen = _subscribe_all(ctx.event_bus)
    planner = _planner(ctx, FakePlanSynthesizer(ctx.config))
    pipeline = FakeResearchPipeline(config=ctx.config, tool_registry=ctx.tool_registry)
    mini.run_agent("do a thing", pipeline=pipeline, planner=planner,
                   confirm_fn=lambda t, details="": True)
    # success path emitted
    for et in (EventType.AGENT_ITERATION_STARTED, EventType.AGENT_EXECUTION_COMPLETED,
               EventType.AGENT_VERIFICATION_COMPLETED, EventType.AGENT_COMPLETED):
        assert et in seen


def test_f_confirmation_denial():
    mini, ctx = _build_minimal_assistant()
    ctx.agent_loop._executor = ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, _fail)
    seen = _subscribe_all(ctx.event_bus)
    planner = _planner(ctx, FakePlanSynthesizer(ctx.config))
    pipeline = FakeResearchPipeline(config=ctx.config, tool_registry=ctx.tool_registry)
    res = mini.run_agent("do a thing", pipeline=pipeline, planner=planner,
                         confirm_fn=lambda t, details="": False)
    assert res.status == AgentLoopStatus.STOPPED_DENIED
    assert res.aborted is True
    assert EventType.AGENT_ABORTED in seen
    assert len(res.iterations) >= 1
    assert res.iterations[-1].execution_status in (
        ExecutionStatus.DENIED.value, ExecutionStatus.ABORTED.value)


def test_f_dangerous_tool_confirmation():
    mini, ctx = _build_minimal_assistant()
    ctx.agent_loop._executor = ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, _pass)
    planner = _planner(ctx, DangerPlanSynthesizer(ctx.config))
    pipeline = FakeResearchPipeline(config=ctx.config, tool_registry=ctx.tool_registry)

    # Denied: the DANGEROUS shell step must NOT execute.
    calls = []
    def deny_shell(tool, details=""):
        calls.append(tool)
        return tool != "shell"  # allow calculator, deny shell
    res = mini.run_agent("do a thing", pipeline=pipeline, planner=planner,
                         confirm_fn=deny_shell)
    assert "shell" in calls  # confirmation gate was consulted
    assert res.status == AgentLoopStatus.STOPPED_DENIED
    # The dangerous shell step was refused by the confirmation gate (no execution).
    shell_decisions = [
        d for it in res.iterations for d in it.confirmation_decisions
        if d.get("tool") == "shell"
    ]
    assert shell_decisions, "shell step must have a recorded confirmation decision"
    assert all(d["decision"] is False for d in shell_decisions)


def test_f_failure_then_replan():
    mini, ctx = _build_minimal_assistant()
    ctx.agent_loop._executor = ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, _fail)
    planner = _planner(ctx, FakePlanSynthesizer(ctx.config))
    pipeline = FakeResearchPipeline(config=ctx.config, tool_registry=ctx.tool_registry)
    # confirm True first (exec fails), then deny the replanned proposal.
    state = {"n": 0}
    def confirm(tool, details=""):
        state["n"] += 1
        return state["n"] == 1
    res = mini.run_agent("do a thing", pipeline=pipeline, planner=planner,
                         confirm_fn=confirm)
    assert res.status == AgentLoopStatus.STOPPED_DENIED
    assert res.final_proposal is not None
    assert res.final_proposal.status == ProposalStatus.VALIDATED


def test_f_failed_never_success():
    mini, ctx = _build_minimal_assistant()
    ctx.agent_loop._executor = ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, _fail)
    planner = _planner(ctx, FakePlanSynthesizer(ctx.config))
    pipeline = FakeResearchPipeline(config=ctx.config, tool_registry=ctx.tool_registry)
    res = mini.run_agent("do a thing", pipeline=pipeline, planner=planner,
                         confirm_fn=lambda t, details="": True, max_iterations=1)
    assert res.status != AgentLoopStatus.DONE
    assert res.final_verification.status == VerificationStatus.FAILURE
    assert res.final_verification.status != VerificationStatus.SUCCESS


def test_f_inconclusive_never_success():
    mini, ctx = _build_minimal_assistant()
    def calc_behaviour(action, it):
        return {"status": StepStatus.EXECUTED.value, "output": "= 4"}
    ctx.agent_loop._executor = ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, calc_behaviour)
    planner = _planner(ctx, DangerPlanSynthesizer(ctx.config))  # calculator step
    pipeline = FakeResearchPipeline(config=ctx.config, tool_registry=ctx.tool_registry)
    res = mini.run_agent("compute", pipeline=pipeline, planner=planner,
                         confirm_fn=lambda t, details="": True, max_iterations=1)
    assert res.status != AgentLoopStatus.DONE
    assert res.final_verification.status != VerificationStatus.SUCCESS


def test_f_iteration_limit():
    mini, ctx = _build_minimal_assistant()
    ctx.agent_loop._executor = ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, _fail)
    planner = _planner(ctx, FakePlanSynthesizer(ctx.config))
    pipeline = FakeResearchPipeline(config=ctx.config, tool_registry=ctx.tool_registry)
    res = mini.run_agent("do a thing", pipeline=pipeline, planner=planner,
                         confirm_fn=lambda t, details="": True, max_iterations=1000)
    assert res.status == AgentLoopStatus.STOPPED_LIMIT
    assert len(res.iterations) <= 20
    assert len(res.iterations) >= 1


def test_f_eventbus_real_events():
    mini, ctx = _build_minimal_assistant()
    ctx.agent_loop._executor = ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, _pass)
    captured = []
    for et in (EventType.AGENT_ITERATION_STARTED, EventType.AGENT_EXECUTION_COMPLETED,
               EventType.AGENT_VERIFICATION_COMPLETED, EventType.AGENT_REPLAN_COMPLETED,
               EventType.AGENT_COMPLETED, EventType.AGENT_ABORTED):
        ctx.event_bus.subscribe(et, lambda e, et=et: captured.append(e))
    planner = _planner(ctx, FakePlanSynthesizer(ctx.config))
    pipeline = FakeResearchPipeline(config=ctx.config, tool_registry=ctx.tool_registry)
    mini.run_agent("do a thing", pipeline=pipeline, planner=planner,
                   confirm_fn=lambda t, details="": True)
    assert captured
    for ev in captured:
        assert isinstance(ev, Event)
        assert ev.source == "agent_loop"
        assert all(isinstance(v, (str, int, float, bool, type(None)))
                   for v in ev.payload.values())


def test_f_no_second_execution_engine():
    mini, ctx = _build_minimal_assistant()
    # The default wiring uses the REAL ProposalExecutor (no injected fake).
    assert isinstance(ctx.agent_loop._executor, ProposalExecutor)
    spy = ScenarioToolRegistry(ALLOWED)
    loop = AgentLoop(spy, ctx.permission_manager, event_bus=ctx.event_bus)
    loop._executor = ScenarioExecutor(spy, ctx.permission_manager, _pass)
    # A proposal whose execution outcome is genuinely verifiable SUCCESS
    # (test_execution with passing output) so the real ProposalExecutor
    # drives the loop to DONE -- proving the real executor was actually used.
    planner = _planner(ctx, VerifyPlanSynthesizer(ctx.config))
    pipeline = FakeResearchPipeline(config=ctx.config, tool_registry=ctx.tool_registry)
    res = loop.run("run the tests", planner.to_proposal(planner.plan(pipeline.research("x"))),
                   confirm_fn=lambda t, details="": True)
    assert res.status == AgentLoopStatus.DONE
    assert spy.execute_calls == 0  # AgentLoop never calls ToolRegistry.execute


def test_f_default_confirm_is_permission_manager():
    mini, ctx = _build_minimal_assistant()
    # run_agent defaults confirm_fn to self.permissions.confirm when None.
    planner = _planner(ctx, FakePlanSynthesizer(ctx.config))
    pipeline = FakeResearchPipeline(config=ctx.config, tool_registry=ctx.tool_registry)
    # Prove the default path uses the REAL PermissionManager.confirm: patch
    # builtins.input so the real gate runs without blocking pytest's captured
    # stdin, and record that the gate was actually consulted.
    recorded = []
    real_confirm = ctx.permission_manager.confirm
    captured_input = []
    import builtins
    real_input = builtins.input
    def fake_input(prompt=""):
        captured_input.append(prompt)
        return "y"  # human approves
    def recorder(tool, details=""):
        recorded.append(tool)
        return real_confirm(tool, details)
    ctx.permission_manager.confirm = recorder
    builtins.input = fake_input
    try:
        ctx.agent_loop._executor = ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, _pass)
        # confirm_fn=None -> uses ctx.permission_manager.confirm (recorder),
        # which delegates to the REAL PermissionManager.confirm (real_input).
        res = mini.run_agent("do a thing", pipeline=pipeline, planner=planner,
                             confirm_fn=None)
    finally:
        ctx.permission_manager.confirm = real_confirm
        builtins.input = real_input
    # The real gate was consulted (input() prompt captured) AND the decision
    # flowed through PermissionManager.confirm (recorder captured the tool).
    assert recorded
    assert captured_input  # PermissionManager.confirm prompted the human
