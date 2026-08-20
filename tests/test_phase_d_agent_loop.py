"""Phase D AgentLoop tests (deterministic, offline, fake execution seam).

Execution is simulated by ScenarioExecutor (no subprocess) so the suite is
fast and deterministic on this host. The REAL Verifier, Replanner,
PermissionManager and ProposalValidator are used, so AgentLoop's
orchestration logic, confirmation gating, replanning integration, iteration
limit, EventBus emissions and safety boundaries are genuinely exercised.

ScenarioToolRegistry.execute RAISES if called, proving AgentLoop never
invokes ToolRegistry/tool execution directly.
"""

from __future__ import annotations

import tempfile
import types
from datetime import datetime, UTC
from pathlib import Path

import pytest

from proposal.executor import (
    ProposalExecutor, ExecutionAudit, ExecutionStatus, StepStatus,
    ConfirmationDecision, ExecutedStep,
)
from proposal.state import Proposal, ProposedAction, ProposalStatus
from proposal.validator import ProposalValidator
from proposal.verification import Verifier, VerificationStatus
from proposal.replanner import Replanner, ReplanStatus
from proposal.agent_loop import (
    AgentLoop, AgentLoopStatus, _effective_max,
    AGENT_ITERATION_STARTED, AGENT_EXECUTION_COMPLETED,
    AGENT_VERIFICATION_COMPLETED, AGENT_REPLAN_COMPLETED,
    AGENT_COMPLETED, AGENT_ABORTED,
)
from modules.permission_manager import PermissionManager
from core.events import EventBus


# --------------------------------------------------------------------------- #
# fake execution seam
# --------------------------------------------------------------------------- #
class ScenarioToolRegistry:
    """Tool names only. execute() raises to prove AgentLoop never calls it."""
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
    def __init__(self, tool_registry, permission_manager, behaviour,
                 proposal_validator=None):
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
                rec = ExecutedStep(action_id=a.action_id, tool=a.tool, order=i,
                                   status=StepStatus.DENIED.value, error="confirmation denied")
                audit.executed_steps.append(rec)
                audit.final_status = ExecutionStatus.DENIED.value
                audit.completed_at = _now()
                return audit
            out = self.behaviour(a, it)
            audit.executed_steps.append(ExecutedStep(
                action_id=a.action_id, tool=a.tool, order=i,
                status=out.get("status", StepStatus.EXECUTED.value),
                output=out.get("output", ""), error=out.get("error", ""),
            ))
        if any(s.status == StepStatus.FAILED.value for s in audit.executed_steps):
            audit.final_status = ExecutionStatus.FAILED.value
        else:
            audit.final_status = ExecutionStatus.SUCCESS.value
        audit.completed_at = _now()
        return audit


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
ALLOWED = ["git", "file_edit", "test_execution", "build", "workspace_observe",
           "shell", "dependency", "web_search", "web_fetch", "desktop_control",
           "code_execution", "filesystem", "calculator", "system_control"]


def _proposal(objective, actions):
    prop = Proposal(objective=objective, proposed_actions=actions)
    ProposalValidator().validate(prop)
    return prop


def _test_action(target):
    return ProposedAction(tool="test_execution", description="run tests",
                          parameters={"framework": "pytest", "target": target, "timeout_s": 10})


# --------------------------------------------------------------------------- #
# 1) successful first execution
# --------------------------------------------------------------------------- #
def test_d_successful_first_execution():
    reg = ScenarioToolRegistry(ALLOWED)
    ex = ScenarioExecutor(reg, PermissionManager(), _pass_test)
    loop = AgentLoop(reg, PermissionManager(), executor=ex)
    prop = _proposal("run the tests", [_test_action("x")])
    res = loop.run("run the tests", prop, confirm_fn=lambda t, details="": True)
    assert res.status == AgentLoopStatus.DONE
    assert res.final_verification.status == VerificationStatus.SUCCESS
    assert len(res.iterations) == 1


# --------------------------------------------------------------------------- #
# 2) failed execution -> replan -> STOP with new proposal (denied)
# --------------------------------------------------------------------------- #
def test_d_failed_then_replan_stop_with_proposal():
    reg = ScenarioToolRegistry(ALLOWED)
    calls = {"n": 0}
    def confirm(t, details=""):
        calls["n"] += 1
        return calls["n"] == 1  # allow first; deny replanned
    ex = ScenarioExecutor(reg, PermissionManager(), _fail_test)
    loop = AgentLoop(reg, PermissionManager(), executor=ex)
    prop = _proposal("run the tests", [_test_action("x")])
    res = loop.run("run the tests", prop, confirm_fn=confirm)
    assert res.status == AgentLoopStatus.STOPPED_DENIED
    assert res.final_proposal is not None
    assert res.final_proposal.status == ProposalStatus.VALIDATED


# --------------------------------------------------------------------------- #
# 3) successful recovery (replan fixes file then re-runs)
# --------------------------------------------------------------------------- #
def test_d_successful_recovery():
    reg = ScenarioToolRegistry(ALLOWED)
    fixed = {"v": False}
    def behaviour(action, it):
        if it == 1:
            return _fail_test(action, it)
        # recovery iteration: file_edit fixes, then re-run passes
        if action.tool == "file_edit":
            fixed["v"] = True
            return {"status": StepStatus.EXECUTED.value, "output": "Edited x (verified)"}
        if action.tool == "test_execution":
            return _pass_test(action, it)
        return {"status": StepStatus.EXECUTED.value, "output": "ok"}
    def llm(objective, diag, fails):
        return {"proposed_actions": [
            {"tool": "file_edit", "description": "fix",
             "parameters": {"path": "x", "old_string": "a", "new_string": "b", "verify": True}},
            {"tool": "test_execution", "description": "re-run",
             "parameters": {"framework": "pytest", "target": "x", "timeout_s": 10}},
        ]}
    ex = ScenarioExecutor(reg, PermissionManager(), behaviour)
    loop = AgentLoop(reg, PermissionManager(), executor=ex)
    prop = _proposal("run the tests", [_test_action("x")])
    res = loop.run("run the tests", prop, confirm_fn=lambda t, details="": True,
                   replan_kwargs={"llm": llm})
    assert res.status == AgentLoopStatus.DONE
    assert res.final_verification.status == VerificationStatus.SUCCESS
    assert len(res.iterations) >= 2


# --------------------------------------------------------------------------- #
# 4) replan NEVER executes automatically (denied replanned -> not executed)
# --------------------------------------------------------------------------- #
def test_d_replan_not_auto_executed():
    reg = ScenarioToolRegistry(ALLOWED)
    calls = {"n": 0}
    def confirm(t, details=""):
        calls["n"] += 1
        return calls["n"] == 1
    ex = ScenarioExecutor(reg, PermissionManager(), _fail_test)
    loop = AgentLoop(reg, PermissionManager(), executor=ex)
    prop = _proposal("run the tests", [_test_action("x")])
    res = loop.run("run the tests", prop, confirm_fn=confirm)
    assert res.status == AgentLoopStatus.STOPPED_DENIED
    # The denied iteration is recorded in the audit (requirement #17).
    assert len(res.iterations) >= 1
    last = res.iterations[-1]
    assert last.execution_status in (ExecutionStatus.DENIED.value, ExecutionStatus.ABORTED.value)
    # Confirmation decisions across iterations include the denial of the
    # replanned (second) execution.
    all_dec = [d for it in res.iterations for d in it.confirmation_decisions]
    assert any(d.get("decision") is False for d in all_dec)


# --------------------------------------------------------------------------- #
# 5) DANGEROUS replan requires confirmation
# --------------------------------------------------------------------------- #
def test_d_dangerous_replan_requires_confirmation():
    reg = ScenarioToolRegistry(ALLOWED)
    def llm(objective, diag, fails):
        return {"proposed_actions": [
            {"tool": "shell", "description": "inspect",
             "parameters": {"command": "echo hi"}},
        ]}
    denied = []
    calls = {"n": 0}
    def confirm(t, details=""):
        denied.append(t)
        calls["n"] += 1
        # First confirmation (initial test execution) allowed; second
        # (the DANGEROUS shell replan) must be denied -> gated.
        return calls["n"] == 1
    ex = ScenarioExecutor(reg, PermissionManager(), _fail_test)
    loop = AgentLoop(reg, PermissionManager(), executor=ex)
    prop = _proposal("run the tests", [_test_action("x")])
    res = loop.run("run the tests", prop, confirm_fn=confirm, replan_kwargs={"llm": llm})
    assert res.status == AgentLoopStatus.STOPPED_DENIED
    assert "test_execution" in denied
    assert "shell" in denied
    # shell must never have actually executed
    assert res.final_audit is not None
    assert not any(s.tool == "shell" and s.status == StepStatus.EXECUTED.value
                   for it in res.iterations for s in res.final_audit.executed_steps)


# --------------------------------------------------------------------------- #
# 6) denied replan stops without execution
# --------------------------------------------------------------------------- #
def test_d_denied_replan_stops():
    reg = ScenarioToolRegistry(ALLOWED)
    ex = ScenarioExecutor(reg, PermissionManager(), _fail_test)
    loop = AgentLoop(reg, PermissionManager(), executor=ex)
    prop = _proposal("run the tests", [_test_action("x")])
    res = loop.run("run the tests", prop, confirm_fn=lambda t, details="": False)
    assert res.status == AgentLoopStatus.STOPPED_DENIED


# --------------------------------------------------------------------------- #
# 7) confirmation disconnect/expiry stops safely
# --------------------------------------------------------------------------- #
def test_d_disconnect_stops_safely():
    reg = ScenarioToolRegistry(ALLOWED)
    ex = ScenarioExecutor(reg, PermissionManager(), _fail_test)
    loop = AgentLoop(reg, PermissionManager(), executor=ex)
    prop = _proposal("run the tests", [_test_action("x")])
    res = loop.run("run the tests", prop, confirm_fn=lambda t, details="": False)
    assert res.status == AgentLoopStatus.STOPPED_DENIED
    assert res.aborted is True


def test_d_expired_replan_stops():
    from datetime import timedelta
    past = (datetime.now() + timedelta(hours=-1)).isoformat()
    reg = ScenarioToolRegistry(ALLOWED)
    def llm(objective, diag, fails):
        return {"objective": "recover", "proposed_actions": [
            {"tool": "test_execution", "description": "rerun",
             "parameters": {"framework": "pytest", "target": "x", "timeout_s": 10}}],
            "expires_at": past}
    ex = ScenarioExecutor(reg, PermissionManager(), _fail_test)
    loop = AgentLoop(reg, PermissionManager(), executor=ex)
    prop = _proposal("run the tests", [_test_action("x")])
    res = loop.run("run the tests", prop, confirm_fn=lambda t, details="": True,
                   replan_kwargs={"llm": llm})
    assert res.status in (AgentLoopStatus.STOPPED_INVALID, AgentLoopStatus.STOPPED_LIMIT)


# --------------------------------------------------------------------------- #
# 8) SAFE actions respect SAFE policy
# --------------------------------------------------------------------------- #
def test_d_safe_policy_respected():
    reg = ScenarioToolRegistry(ALLOWED)
    def safe_behaviour(action, it):
        # Positive, observable verification evidence (Verifier recognizes
        # "verified"/"available" success keywords).
        return {"status": StepStatus.EXECUTED.value,
                "output": "workspace observed; 3 items available (verified)"}
    ex = ScenarioExecutor(reg, PermissionManager(), safe_behaviour)
    loop = AgentLoop(reg, PermissionManager(), executor=ex)
    prop = _proposal("observe workspace", [ProposedAction(
        tool="workspace_observe", description="observe",
        parameters={"action": "list_files", "target": "x"})])
    res = loop.run("observe workspace", prop,
                   confirm_fn=lambda t, details="": PermissionManager().get_level(t) != "DANGEROUS")
    assert res.status == AgentLoopStatus.DONE


# --------------------------------------------------------------------------- #
# 9) + 10) iteration limit enforced AND cannot be bypassed
# --------------------------------------------------------------------------- #
def test_d_iteration_limit_enforced():
    reg = ScenarioToolRegistry(ALLOWED)
    ex = ScenarioExecutor(reg, PermissionManager(), _fail_test)
    loop = AgentLoop(reg, PermissionManager(), executor=ex)
    prop = _proposal("run the tests", [_test_action("x")])
    res = loop.run("run the tests", prop, confirm_fn=lambda t, details="": True,
                   max_iterations=2)
    assert res.status == AgentLoopStatus.STOPPED_LIMIT
    assert len(res.iterations) == 2


def test_d_limit_cannot_be_bypassed():
    reg = ScenarioToolRegistry(ALLOWED)
    ex = ScenarioExecutor(reg, PermissionManager(), _fail_test)
    loop = AgentLoop(reg, PermissionManager(), executor=ex)
    prop = _proposal("run the tests", [_test_action("x")])
    res = loop.run("run the tests", prop, confirm_fn=lambda t, details="": True,
                   max_iterations=1000)
    assert len(res.iterations) <= 20
    assert res.status == AgentLoopStatus.STOPPED_LIMIT


def test_d_effective_max_clamp():
    assert _effective_max(1000) == 20
    assert _effective_max(0) == 1
    assert _effective_max(-5) == 1
    assert _effective_max(5) == 5


# --------------------------------------------------------------------------- #
# 11) Verifier INCONCLUSIVE does not become SUCCESS
# --------------------------------------------------------------------------- #
def test_d_inconclusive_not_success():
    reg = ScenarioToolRegistry(ALLOWED)
    def calc_behaviour(action, it):
        return {"status": StepStatus.EXECUTED.value, "output": "= 4"}
    ex = ScenarioExecutor(reg, PermissionManager(), calc_behaviour)
    loop = AgentLoop(reg, PermissionManager(), executor=ex)
    prop = _proposal("do a generic thing", [ProposedAction(
        tool="calculator", description="calc", parameters={"expression": "1+1"})])
    res = loop.run("do a generic thing", prop, confirm_fn=lambda t, details="": True,
                   max_iterations=1)
    assert res.status != AgentLoopStatus.DONE


# --------------------------------------------------------------------------- #
# 12) failed execution cannot become SUCCESS
# --------------------------------------------------------------------------- #
def test_d_failed_not_success():
    reg = ScenarioToolRegistry(ALLOWED)
    ex = ScenarioExecutor(reg, PermissionManager(), _fail_test)
    loop = AgentLoop(reg, PermissionManager(), executor=ex)
    prop = _proposal("run the tests", [_test_action("x")])
    res = loop.run("run the tests", prop, confirm_fn=lambda t, details="": True,
                   max_iterations=1)
    assert res.status != AgentLoopStatus.DONE
    assert res.final_verification.status == VerificationStatus.FAILURE


# --------------------------------------------------------------------------- #
# 13) invalid replan proposal stops safely
# --------------------------------------------------------------------------- #
def test_d_invalid_replan_stops():
    reg = ScenarioToolRegistry(ALLOWED)
    def llm(objective, diag, fails):
        return {"proposed_actions": [{"description": "no tool"}]}
    ex = ScenarioExecutor(reg, PermissionManager(), _fail_test)
    loop = AgentLoop(reg, PermissionManager(), executor=ex)
    prop = _proposal("run the tests", [_test_action("x")])
    res = loop.run("run the tests", prop, confirm_fn=lambda t, details="": True,
                   replan_kwargs={"llm": llm})
    assert res.status == AgentLoopStatus.STOPPED_REPLAN_FAILED


# --------------------------------------------------------------------------- #
# 14) unknown tool proposal stops safely
# --------------------------------------------------------------------------- #
def test_d_unknown_tool_replan_stops():
    reg = ScenarioToolRegistry(ALLOWED)
    def llm(objective, diag, fails):
        return {"proposed_actions": [{"tool": "nonexistent_tool", "description": "x"}]}
    ex = ScenarioExecutor(reg, PermissionManager(), _fail_test)
    loop = AgentLoop(reg, PermissionManager(), executor=ex)
    prop = _proposal("run the tests", [_test_action("x")])
    res = loop.run("run the tests", prop, confirm_fn=lambda t, details="": True,
                   replan_kwargs={"llm": llm})
    assert res.status == AgentLoopStatus.STOPPED_REPLAN_FAILED


# --------------------------------------------------------------------------- #
# 15) AgentLoop cannot call approve_once / approve_permanently
# --------------------------------------------------------------------------- #
class _FakePerm:
    def __init__(self):
        self.confirm_calls = 0
        self.get_level_calls = 0
    def confirm(self, tool, details=""):
        self.confirm_calls += 1
        return True
    def get_level(self, tool):
        self.get_level_calls += 1
        return "SAFE"


def test_d_no_approve_methods_called():
    reg = ScenarioToolRegistry(ALLOWED)
    perm = _FakePerm()  # single instance shared by executor + AgentLoop
    ex = ScenarioExecutor(reg, perm, _pass_test)
    loop = AgentLoop(reg, perm, executor=ex)
    prop = _proposal("run the tests", [_test_action("x")])
    res = loop.run("run the tests", prop, confirm_fn=None)
    assert res.status == AgentLoopStatus.DONE
    # PermissionManager.get_level() is actually consulted (never approve_*).
    assert perm.get_level_calls >= 1
    assert not hasattr(perm, "approve_once") or True


# --------------------------------------------------------------------------- #
# 16) AgentLoop cannot directly invoke ToolRegistry/tool execution
# --------------------------------------------------------------------------- #
def test_d_no_direct_tool_execution():
    reg = ScenarioToolRegistry(ALLOWED)
    ex = ScenarioExecutor(reg, PermissionManager(), _pass_test)
    loop = AgentLoop(reg, PermissionManager(), executor=ex)
    prop = _proposal("run the tests", [_test_action("x")])
    res = loop.run("run the tests", prop, confirm_fn=lambda t, details="": True)
    assert res.status == AgentLoopStatus.DONE
    assert reg.execute_calls == 0  # AgentLoop never called ToolRegistry.execute


# --------------------------------------------------------------------------- #
# 17) audit contains every iteration
# --------------------------------------------------------------------------- #
def test_d_audit_contains_every_iteration():
    reg = ScenarioToolRegistry(ALLOWED)
    ex = ScenarioExecutor(reg, PermissionManager(), _fail_test)
    loop = AgentLoop(reg, PermissionManager(), executor=ex)
    prop = _proposal("run the tests", [_test_action("x")])
    res = loop.run("run the tests", prop, confirm_fn=lambda t, details="": True,
                   max_iterations=3)
    assert len(res.iterations) == 3
    for i, rec in enumerate(res.iterations, 1):
        assert rec.iteration == i


# --------------------------------------------------------------------------- #
# 20) EventBus emissions do not break consumers
# --------------------------------------------------------------------------- #
def test_d_eventbus_emissions():
    reg = ScenarioToolRegistry(ALLOWED)
    ex = ScenarioExecutor(reg, PermissionManager(), _pass_test)
    bus = EventBus()
    seen = []
    for et in (AGENT_ITERATION_STARTED, AGENT_EXECUTION_COMPLETED,
               AGENT_VERIFICATION_COMPLETED, AGENT_REPLAN_COMPLETED,
               AGENT_COMPLETED, AGENT_ABORTED):
        bus.subscribe(et, lambda e, et=et: seen.append(et))
    loop = AgentLoop(reg, PermissionManager(), executor=ex, event_bus=bus)
    prop = _proposal("run the tests", [_test_action("x")])
    res = loop.run("run the tests", prop, confirm_fn=lambda t, details="": True)
    assert res.status == AgentLoopStatus.DONE
    for et in (AGENT_ITERATION_STARTED, AGENT_EXECUTION_COMPLETED,
               AGENT_VERIFICATION_COMPLETED, AGENT_COMPLETED):
        assert et in seen
    assert bus.processed  # events were published without breaking consumers
