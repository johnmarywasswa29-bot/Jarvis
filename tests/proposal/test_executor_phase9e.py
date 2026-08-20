"""Phase 9E deterministic tests: validated proposal -> execution.

Exercises ProposalExecutor offline with:
  * a fake ToolRegistry (stub tools returning success/failure),
  * a stub PermissionManager (configurable confirmation decisions),
  * a real ProposalValidator (reused, not replaced).

All tests are @pytest.mark.offline.

Covered:
  * successful execution of a multi-step, dependency-ordered proposal
  * dependency ordering (dependent step runs after its dependency)
  * failed step stops execution safely (later steps SKIPPED)
  * denied confirmation stops execution safely (step DENIED)
  * invalid (non-validated) proposal refused
  * audit/result collection (status, output, error, duration, order, decisions)
"""

from __future__ import annotations

import pytest

from proposal.executor import (
    ExecutionAudit,
    ExecutionStatus,
    ProposalExecutor,
    StepStatus,
)
from proposal.manager import ProposalManager
from proposal.state import (
    ProposedAction,
    Proposal,
    ProposalRiskLevel,
    ProposalStatus,
)


# -----------------------------------------------------------------------------
# Stub infrastructure (no real tools / no real prompts)
# -----------------------------------------------------------------------------
class _StubTool:
    """A registered tool whose execute() is scripted by the test."""

    def __init__(self, name, success=True, output="ok", error="", raises=False):
        self.name = name
        self.enabled = True
        self._success = success
        self._output = output
        self._error = error
        self._raises = raises
        self.calls = []

    def can_handle(self, prompt: str) -> bool:
        return True

    def execute(self, **kwargs):
        from modules.tools import ToolResult
        self.calls.append(kwargs)
        if self._raises:
            raise RuntimeError("boom")
        return ToolResult(
            success=self._success,
            output=self._output,
            error=self._error,
            duration_s=0.0,
        )


class _StubRegistry:
    """Minimal ToolRegistry stand-in exposing get_tool/has_tool/tool_names."""

    def __init__(self, tools):
        self._tools = {t.name: t for t in tools}

    def get_tool(self, name):
        return self._tools.get(name)

    def has_tool(self, name):
        return name in self._tools

    def tool_names(self):
        return list(self._tools)


class _StubPermissionManager:
    """Configurable confirmation gate. Never prompts.

    Mirrors the real PermissionManager's level semantics: a SAFE action is
    always allowed; otherwise the configured ``_allow`` decision is used.
    ``deny_actions`` lets a test force-deny specific tools (e.g. terminal)
    while letting others through.
    """

    def __init__(self, allow=True, level="CAUTION", deny_actions=None):
        self._allow = allow
        self._level = level
        self._deny_actions = set(deny_actions or [])
        self.confirmed = []  # record of (action, details)

    def get_level(self, action):
        # A denied action is treated as DANGEROUS for reporting.
        if action in self._deny_actions:
            return "DANGEROUS"
        return self._level

    def requires_confirmation(self, action):
        return action not in self._deny_actions and self._level != "SAFE"

    def confirm(self, action, details=""):
        self.confirmed.append((action, details))
        if action in self._deny_actions:
            return False
        if self._level == "SAFE":
            return True
        return self._allow

    def set_permission(self, action, level):
        self._level = level


def _make_proposal(actions, *, risk=ProposalRiskLevel.LOW, status=ProposalStatus.VALIDATED):
    """Build a proposal directly (bypassing the planner) for execution tests."""
    pactions = [
        ProposedAction(
            action_id=a["id"],
            tool=a["tool"],
            description=a.get("description", ""),
            parameters=a.get("parameters", {}),
            dependencies=a.get("dependencies", []),
        )
        for a in actions
    ]
    p = Proposal(
        objective="test objective",
        proposed_actions=pactions,
        risk_level=risk,
        requires_confirmation=True,
        status=status,
    )
    return p


# -----------------------------------------------------------------------------
# 1. Successful execution
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestSuccessfulExecution:
    def test_all_steps_execute_successfully(self):
        t_search = _StubTool("web_search", output="results")
        t_calc = _StubTool("calculator", output="42")
        reg = _StubRegistry([t_search, t_calc])
        pm = _StubPermissionManager(allow=True, level="CAUTION")
        executor = ProposalExecutor(tool_registry=reg, permission_manager=pm)

        proposal = _make_proposal([
            {"id": "s1", "tool": "web_search", "parameters": {"query": "x"}},
            {"id": "s2", "tool": "calculator", "parameters": {"expression": "6*7"}},
        ])
        audit = executor.execute(proposal)

        assert audit.final_status == ExecutionStatus.SUCCESS.value
        assert len(audit.executed_steps) == 2
        assert all(s.status == StepStatus.EXECUTED.value for s in audit.executed_steps)
        # Confirmation was requested for both consequential steps.
        assert len(pm.confirmed) == 2
        # Per-step output collected.
        assert audit.executed_steps[1].output == "42"

    def test_safe_tool_runs_without_confirmation(self):
        t_calc = _StubTool("calculator", output="1")
        reg = _StubRegistry([t_calc])
        pm = _StubPermissionManager(allow=True, level="SAFE")
        executor = ProposalExecutor(tool_registry=reg, permission_manager=pm)
        proposal = _make_proposal([{"id": "s1", "tool": "calculator"}])
        audit = executor.execute(proposal)
        assert audit.final_status == ExecutionStatus.SUCCESS.value
        # SAFE tool: confirm() returns True but should NOT require prompting;
        # the decision is recorded True.
        assert audit.executed_steps[0].confirmation_decision is True


# -----------------------------------------------------------------------------
# 2. Dependency ordering
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestDependencyOrdering:
    def test_dependent_step_runs_after_dependency(self):
        calls = []
        t1 = _StubTool("web_search", output="a")
        t2 = _StubTool("calculator", output="b")
        t1.execute = lambda **k: (calls.append("s1") or __import__("modules.tools", fromlist=["ToolResult"]).ToolResult(True, "a"))
        t2.execute = lambda **k: (calls.append("s2") or __import__("modules.tools", fromlist=["ToolResult"]).ToolResult(True, "b"))
        reg = _StubRegistry([t1, t2])
        pm = _StubPermissionManager(allow=True)
        executor = ProposalExecutor(tool_registry=reg, permission_manager=pm)
        proposal = _make_proposal([
            {"id": "s1", "tool": "web_search"},
            {"id": "s2", "tool": "calculator", "dependencies": ["s1"]},
        ])
        audit = executor.execute(proposal)
        assert audit.final_status == ExecutionStatus.SUCCESS.value
        assert calls == ["s1", "s2"]
        # Execution order field reflects dependency order.
        assert audit.executed_steps[0].action_id == "s1"
        assert audit.executed_steps[1].action_id == "s2"


# -----------------------------------------------------------------------------
# 3. Failed step stops safely
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestFailedStepStops:
    def test_failed_step_halts_and_skips_later(self):
        t_ok = _StubTool("web_search", output="ok")
        t_bad = _StubTool("calculator", success=False, error="div by zero")
        t_late = _StubTool("filesystem", output="late")
        reg = _StubRegistry([t_ok, t_bad, t_late])
        pm = _StubPermissionManager(allow=True)
        executor = ProposalExecutor(tool_registry=reg, permission_manager=pm)
        proposal = _make_proposal([
            {"id": "s1", "tool": "web_search"},
            {"id": "s2", "tool": "calculator", "dependencies": ["s1"]},
            {"id": "s3", "tool": "filesystem", "dependencies": ["s2"]},
        ])
        audit = executor.execute(proposal)

        assert audit.final_status == ExecutionStatus.FAILED.value
        statuses = {s.action_id: s.status for s in audit.executed_steps}
        assert statuses["s1"] == StepStatus.EXECUTED.value
        assert statuses["s2"] == StepStatus.FAILED.value
        # s3 never ran (skipped) because s2 failed.
        assert statuses["s3"] == StepStatus.SKIPPED.value
        # The late tool was never invoked.
        assert t_late.calls == []

    def test_tool_exception_treated_as_failure(self):
        t = _StubTool("calculator", raises=True)
        reg = _StubRegistry([t])
        pm = _StubPermissionManager(allow=True)
        executor = ProposalExecutor(tool_registry=reg, permission_manager=pm)
        proposal = _make_proposal([{"id": "s1", "tool": "calculator"}])
        audit = executor.execute(proposal)
        assert audit.final_status == ExecutionStatus.FAILED.value
        assert audit.executed_steps[0].status == StepStatus.FAILED.value
        assert "execution exception" in audit.executed_steps[0].error


# -----------------------------------------------------------------------------
# 4. Denied confirmation stops safely
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestDeniedConfirmation:
    def test_denied_confirmation_stops_execution(self):
        t1 = _StubTool("web_search", output="ok")
        t2 = _StubTool("terminal", output="ran")
        reg = _StubRegistry([t1, t2])
        # Deny ONLY the terminal tool; web_search (CAUTION base) is allowed.
        pm = _StubPermissionManager(allow=True, level="CAUTION", deny_actions={"terminal"})
        executor = ProposalExecutor(tool_registry=reg, permission_manager=pm)
        proposal = _make_proposal([
            {"id": "s1", "tool": "web_search"},
            {"id": "s2", "tool": "terminal", "dependencies": ["s1"]},
        ])
        audit = executor.execute(proposal)

        assert audit.final_status == ExecutionStatus.DENIED.value
        statuses = {s.action_id: s.status for s in audit.executed_steps}
        assert statuses["s1"] == StepStatus.EXECUTED.value
        assert statuses["s2"] == StepStatus.DENIED.value
        # The denied tool was never executed.
        assert t2.calls == []
        # Confirmation decision recorded as False.
        assert audit.confirmation_decisions[-1].decision is False

    def test_confirmation_never_auto_approved(self):
        # Even when the executor is constructed, it must call confirm() and
        # respect a denial; it must not execute regardless.
        t = _StubTool("terminal")
        reg = _StubRegistry([t])
        pm = _StubPermissionManager(allow=False, level="DANGEROUS")
        executor = ProposalExecutor(tool_registry=reg, permission_manager=pm)
        proposal = _make_proposal([{"id": "s1", "tool": "terminal"}])
        audit = executor.execute(proposal)
        assert audit.final_status == ExecutionStatus.DENIED.value
        assert t.calls == []  # never executed


# -----------------------------------------------------------------------------
# 5. Invalid proposal refused
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestInvalidProposal:
    def test_non_validated_proposal_refused(self):
        t = _StubTool("calculator")
        reg = _StubRegistry([t])
        pm = _StubPermissionManager(allow=True)
        executor = ProposalExecutor(tool_registry=reg, permission_manager=pm)
        # Proposal status is DRAFT, not VALIDATED.
        proposal = _make_proposal([{"id": "s1", "tool": "calculator"}],
                                  status=ProposalStatus.DRAFT)
        audit = executor.execute(proposal)
        assert audit.final_status == ExecutionStatus.INVALID.value
        assert any("not validated" in e for e in audit.errors)
        assert t.calls == []  # nothing executed

    def test_unknown_tool_in_step_fails_safely(self):
        reg = _StubRegistry([])  # no tools
        pm = _StubPermissionManager(allow=True)
        executor = ProposalExecutor(tool_registry=reg, permission_manager=pm)
        proposal = _make_proposal([{"id": "s1", "tool": "nonexistent_tool"}])
        audit = executor.execute(proposal)
        assert audit.final_status == ExecutionStatus.FAILED.value
        assert audit.executed_steps[0].status == StepStatus.FAILED.value
        assert "unknown or unsupported tool" in audit.executed_steps[0].error


# -----------------------------------------------------------------------------
# 6. Audit / result collection
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestAuditCollection:
    def test_audit_contains_all_required_sections(self):
        t1 = _StubTool("web_search", output="r1")
        t2 = _StubTool("calculator", output="r2")
        reg = _StubRegistry([t1, t2])
        pm = _StubPermissionManager(allow=True)
        executor = ProposalExecutor(tool_registry=reg, permission_manager=pm)
        proposal = _make_proposal([
            {"id": "s1", "tool": "web_search", "parameters": {"query": "q"}},
            {"id": "s2", "tool": "calculator", "dependencies": ["s1"]},
        ])
        audit = executor.execute(proposal)

        # Required fields present.
        assert isinstance(audit, ExecutionAudit)
        assert audit.proposal_id == proposal.proposal_id
        assert audit.objective == "test objective"
        assert audit.proposal is proposal
        assert audit.started_at and audit.completed_at
        # Per-step records carry order, output, duration, decision.
        s1 = audit.executed_steps[0]
        s2 = audit.executed_steps[1]
        assert s1.order == 1 and s2.order == 2
        assert s1.output == "r1" and s2.output == "r2"
        assert s1.duration_s >= 0.0
        assert s1.confirmation_decision is True
        # to_dict roundtrips without error.
        d = audit.to_dict()
        assert d["final_status"] == ExecutionStatus.SUCCESS.value
        assert len(d["executed_steps"]) == 2
        assert len(d["confirmation_decisions"]) == 2
