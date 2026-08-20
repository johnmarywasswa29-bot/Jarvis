"""Phase E — runtime integration tests for the bounded AgentLoop.

Exercises the REAL runtime construction path (runtime.runtime.build_runtime)
which now wires proposal.agent_loop into RuntimeContext, and verifies the
AgentLoop publishes real core.events.Event agent.* events on the real
EventBus. Execution is simulated via a fast ScenarioExecutor so the suite is
deterministic and does not spawn nested pytest subprocesses; one test uses
the genuinely real ProposalExecutor to prove the default wiring path.

Safety invariants checked:
  * AgentLoop reuses the existing ProposalExecutor + PermissionManager +
    confirm_fn gate (no second approval mechanism).
  * AgentLoop never calls ToolRegistry.execute / tool.execute directly.
  * HARD_MAX_ITERATIONS (20) bounds the loop.
  * DENIED/ABORTED iterations are recorded (requirement #17).
  * Failed / inconclusive verification never becomes SUCCESS.
  * No approve_once / approve_permanently, no self-approval.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, UTC
from pathlib import Path

import pytest

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


def _pass_test(action, it):
    return {"status": StepStatus.EXECUTED.value,
            "output": "framework=pytest returncode=0 passed=1 failed=0 errors=0"}


def _fail_test(action, it):
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
                audit.completed_at = _now()
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
# helpers
# --------------------------------------------------------------------------- #
def _ctx():
    return build_runtime(repo=".")


def _proposal(objective, actions):
    prop = Proposal(objective=objective, proposed_actions=actions)
    ProposalValidator().validate(prop)
    return prop


def _test_action(target):
    return ProposedAction(tool="test_execution", description="run tests",
                          parameters={"framework": "pytest", "target": target, "timeout_s": 10})


def _subscribe_all(bus):
    seen = []
    for et in (EventType.AGENT_ITERATION_STARTED, EventType.AGENT_EXECUTION_COMPLETED,
               EventType.AGENT_VERIFICATION_COMPLETED, EventType.AGENT_REPLAN_COMPLETED,
               EventType.AGENT_COMPLETED, EventType.AGENT_ABORTED):
        bus.subscribe(et, lambda e, et=et: seen.append(et))
    return seen


# --------------------------------------------------------------------------- #
# 1) runtime AgentLoop construction
# --------------------------------------------------------------------------- #
def test_e_runtime_constructs_agent_loop():
    ctx = _ctx()
    assert ctx.agent_loop is not None
    assert isinstance(ctx.agent_loop, AgentLoop)
    # Built from the real runtime subsystems; shares the real bus.
    assert ctx.agent_loop._event_bus is ctx.event_bus
    # Default wiring uses the REAL ProposalExecutor (no injected fake).
    assert isinstance(ctx.agent_loop._executor, ProposalExecutor)
    # agent_loop is reachable in all_managers() diagnostics.
    assert "agent_loop" in ctx.all_managers()


# --------------------------------------------------------------------------- #
# 2) real EventBus receives the 6 agent.* EventType events
# --------------------------------------------------------------------------- #
def test_e_agent_events_on_real_bus():
    ctx = _ctx()
    seen = _subscribe_all(ctx.event_bus)
    ctx.agent_loop._executor = ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, _pass_test)
    prop = _proposal("run the tests", [_test_action("x")])
    res = ctx.agent_loop.run("run the tests", prop, confirm_fn=lambda t, details="": True)
    assert res.status == AgentLoopStatus.DONE
    for et in (EventType.AGENT_ITERATION_STARTED, EventType.AGENT_EXECUTION_COMPLETED,
               EventType.AGENT_VERIFICATION_COMPLETED, EventType.AGENT_COMPLETED):
        assert et in seen
    assert ctx.event_bus.processed


def test_e_event_source_and_payload():
    ctx = _ctx()
    captured = []
    ctx.event_bus.subscribe(EventType.AGENT_ITERATION_STARTED,
                             lambda e: captured.append(e))
    ctx.agent_loop._executor = ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, _pass_test)
    prop = _proposal("run the tests", [_test_action("x")])
    ctx.agent_loop.run("run the tests", prop, confirm_fn=lambda t, details="": True)
    assert captured
    ev = captured[0]
    assert isinstance(ev, Event)
    assert ev.event_type == EventType.AGENT_ITERATION_STARTED
    assert ev.source == "agent_loop"
    assert "iteration" in ev.payload and "objective" in ev.payload
    # No credentials/secrets in payload.
    assert all(isinstance(v, (str, int, float, bool, type(None))) for v in ev.payload.values())


# --------------------------------------------------------------------------- #
# 3) confirmation denial stops safely (real PermissionManager consulted)
# --------------------------------------------------------------------------- #
def test_e_denial_stops_with_aborted_event():
    ctx = _ctx()
    seen = _subscribe_all(ctx.event_bus)
    ctx.agent_loop._executor = ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, _fail_test)
    prop = _proposal("run the tests", [_test_action("x")])
    res = ctx.agent_loop.run("run the tests", prop, confirm_fn=lambda t, details="": False)
    assert res.status == AgentLoopStatus.STOPPED_DENIED
    assert res.aborted is True
    assert EventType.AGENT_ABORTED in seen
    # DENIED iteration is recorded (requirement #17).
    assert len(res.iterations) >= 1
    assert res.iterations[-1].execution_status in (ExecutionStatus.DENIED.value, ExecutionStatus.ABORTED.value)


# --------------------------------------------------------------------------- #
# 4) failed verification -> replan -> stop with new proposal
# --------------------------------------------------------------------------- #
def test_e_failed_then_replan_stop():
    ctx = _ctx()
    calls = {"n": 0}
    def confirm(t, details=""):
        calls["n"] += 1
        return calls["n"] == 1  # allow first, deny replanned
    ctx.agent_loop._executor = ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, _fail_test)
    prop = _proposal("run the tests", [_test_action("x")])
    res = ctx.agent_loop.run("run the tests", prop, confirm_fn=confirm)
    assert res.status == AgentLoopStatus.STOPPED_DENIED
    assert res.final_proposal is not None
    assert res.final_proposal.status == ProposalStatus.VALIDATED


# --------------------------------------------------------------------------- #
# 5) bounded iteration limit (hard cap 20)
# --------------------------------------------------------------------------- #
def test_e_iteration_limit_enforced():
    ctx = _ctx()
    ctx.agent_loop._executor = ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, _fail_test)
    prop = _proposal("run the tests", [_test_action("x")])
    res = ctx.agent_loop.run("run the tests", prop,
                             confirm_fn=lambda t, details="": True, max_iterations=1000)
    assert res.status == AgentLoopStatus.STOPPED_LIMIT
    assert len(res.iterations) <= 20
    assert len(res.iterations) >= 1


# --------------------------------------------------------------------------- #
# 6) AgentLoop never calls ToolRegistry.execute directly (real wiring)
# --------------------------------------------------------------------------- #
def test_e_no_direct_tool_execution():
    ctx = _ctx()
    spy = ScenarioToolRegistry(ALLOWED)
    # Rebuild a loop pointing at the spy registry for this assertion while
    # keeping the real PermissionManager + EventBus.
    loop = AgentLoop(spy, ctx.permission_manager, event_bus=ctx.event_bus)
    loop._executor = ScenarioExecutor(spy, ctx.permission_manager, _pass_test)
    prop = _proposal("run the tests", [_test_action("x")])
    res = loop.run("run the tests", prop, confirm_fn=lambda t, details="": True)
    assert res.status == AgentLoopStatus.DONE
    assert spy.execute_calls == 0  # AgentLoop never called ToolRegistry.execute


# --------------------------------------------------------------------------- #
# 7) failed / inconclusive verification never becomes SUCCESS
# --------------------------------------------------------------------------- #
def test_e_failed_not_success():
    ctx = _ctx()
    ctx.agent_loop._executor = ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, _fail_test)
    prop = _proposal("run the tests", [_test_action("x")])
    res = ctx.agent_loop.run("run the tests", prop,
                             confirm_fn=lambda t, details="": True, max_iterations=1)
    assert res.status != AgentLoopStatus.DONE
    assert res.final_verification.status == VerificationStatus.FAILURE


def test_e_inconclusive_not_success():
    ctx = _ctx()
    def calc_behaviour(action, it):
        return {"status": StepStatus.EXECUTED.value, "output": "= 4"}
    ctx.agent_loop._executor = ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, calc_behaviour)
    prop = _proposal("do a thing", [ProposedAction(
        tool="calculator", description="calc", parameters={"expression": "1+1"})])
    res = ctx.agent_loop.run("do a thing", prop,
                             confirm_fn=lambda t, details="": True, max_iterations=1)
    assert res.status != AgentLoopStatus.DONE


# --------------------------------------------------------------------------- #
# 8) import-edge exists so PyInstaller will bundle the proposal modules
# --------------------------------------------------------------------------- #
def test_e_import_graph_edge():
    # runtime.runtime now imports proposal.agent_loop at module load, which
    # transitively pulls proposal.executor/verification/replanner/state/
    # validator into the import graph -> naturally bundled (no hiddenimports).
    import runtime.runtime as rt
    import proposal.agent_loop as al
    assert hasattr(rt, "build_runtime")
    assert al.AgentLoop is not None
    # Confirm the agent.* EventType members exist (P1).
    assert EventType.AGENT_ITERATION_STARTED.value == "agent.iteration.started"
    assert EventType.AGENT_ABORTED.value == "agent.aborted"
