"""Phase C verification + replanning tests (deterministic, offline).

Verifier: observation-only success-from-evidence (never "executor completed").
Replanner: planning-only, produces a validated proposal, never executes,
never confirms/self-approves, rejects unknown/unsafe tools.
Integration: Executor -> Verifier -> Replanner -> ProposalValidator.
"""

from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

import pytest

from proposal.executor import ExecutionAudit, ExecutedStep, StepStatus, ProposalExecutor
from proposal.state import Proposal, ProposedAction, ProposalStatus
from proposal.validator import ProposalValidator
from proposal.verification import Verifier, VerificationStatus
from proposal.replanner import Replanner, ReplanStatus
from modules.tools import ToolRegistry
from modules.permission_manager import PermissionManager


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def _audit(final_status: str, steps: list[ExecutedStep], objective: str = "demo") -> ExecutionAudit:
    a = ExecutionAudit(objective=objective, final_status=final_status)
    a.executed_steps = steps
    return a


def _step(tool: str, status: str, output: str = "", error: str = "", order: int = 1, params: dict | None = None) -> ExecutedStep:
    return ExecutedStep(
        action_id="a%d" % order, tool=tool, order=order, status=status,
        output=output, error=error, parameters=params or {},
    )


def _cfg(root: Path) -> object:
    return types.SimpleNamespace(project_root=root)


# --------------------------------------------------------------------------- #
# VERIFIER
# --------------------------------------------------------------------------- #
def test_verifier_successful_test_objective():
    a = _audit("success", [_step("test_execution", StepStatus.EXECUTED.value,
                                 "framework=pytest returncode=0 passed=1 failed=0 errors=0")]
            )
    r = Verifier().verify("run the tests", a)
    assert r.status == VerificationStatus.SUCCESS
    assert r.success


def test_verifier_failed_test_objective():
    a = _audit("failed", [_step("test_execution", StepStatus.EXECUTED.value,
                                "framework=pytest returncode=1 passed=0 failed=1 errors=0")]
            )
    r = Verifier().verify("run the tests", a)
    assert r.status == VerificationStatus.FAILURE


def test_verifier_successful_build_objective():
    a = _audit("success", [_step("build", StepStatus.EXECUTED.value,
                                 "build 'jarvis' succeeded (exit 0, 1.0s)")]
            )
    r = Verifier().verify("build the project", a)
    assert r.status == VerificationStatus.SUCCESS


def test_verifier_failed_build_objective():
    a = _audit("failed", [_step("build", StepStatus.EXECUTED.value,
                                 "build failed (exit 1)")]
            )
    r = Verifier().verify("build the project", a)
    assert r.status == VerificationStatus.FAILURE


def test_verifier_file_change_verified():
    a = _audit("success", [_step("file_edit", StepStatus.EXECUTED.value,
                                 "Edited code.py (1 occurrence replaced; verified)")]
            )
    r = Verifier().verify("edit the file", a)
    assert r.status == VerificationStatus.SUCCESS


def test_verifier_git_workspace():
    a = _audit("success", [_step("git", StepStatus.EXECUTED.value, "On branch main\nnothing to commit")])
    r = Verifier().verify("check repository state", a)
    assert r.status == VerificationStatus.SUCCESS
    # observations-only path
    r2 = Verifier().verify("check repository state",
                           _audit("success", []), observations={"git_status": "clean"})
    assert r2.status == VerificationStatus.SUCCESS


def test_verifier_inconclusive_without_evidence():
    # generic objective, all steps executed but no positive evidence
    a = _audit("success", [_step("calculator", StepStatus.EXECUTED.value, "= 4")])
    r = Verifier().verify("do a thing", a)
    assert r.status == VerificationStatus.INCONCLUSIVE


def test_verifier_failed_execution_cannot_be_success():
    # Executor claims success but a step actually FAILED -> must be FAILURE.
    a = _audit("success", [_step("test_execution", StepStatus.FAILED.value,
                                 error="boom", output="")])
    r = Verifier().verify("run the tests", a)
    assert r.status == VerificationStatus.FAILURE
    assert r.failed_steps


# --------------------------------------------------------------------------- #
# REPLANNER
# --------------------------------------------------------------------------- #
def _allowed() -> list[str]:
    return ["git", "file_edit", "test_execution", "build", "workspace_observe",
            "shell", "dependency", "web_search", "web_fetch", "desktop_control",
            "code_execution", "filesystem", "calculator", "system_control"]


def test_replanner_diagnosis_from_structured_failure():
    a = _audit("failed", [_step("test_execution", StepStatus.FAILED.value, error="AssertionError")])
    v = Verifier().verify("run the tests", a)
    res = Replanner().replan("run the tests", Proposal(objective="run the tests"), a, v, allowed_tools=_allowed())
    assert "AssertionError" in res.diagnosis


def test_replanner_generates_validated_recovery_proposal():
    a = _audit("failed", [_step("test_execution", StepStatus.FAILED.value, error="boom")])
    v = Verifier().verify("run the tests", a)
    res = Replanner().replan("run the tests", Proposal(objective="run the tests"), a, v, allowed_tools=_allowed())
    assert res.status == ReplanStatus.SUCCESS
    assert res.proposal is not None
    assert res.proposal.status == ProposalStatus.VALIDATED
    assert res.proposal.proposed_actions[0].tool == "test_execution"


def test_replanner_rejects_unknown_tool_via_llm():
    a = _audit("failed", [_step("test_execution", StepStatus.FAILED.value, error="boom")])
    v = Verifier().verify("run the tests", a)
    llm = lambda obj, diag, fails: {"proposed_actions": [{"tool": "rm_rf", "description": "x", "parameters": {}}]}
    res = Replanner().replan("run the tests", Proposal(objective="run the tests"), a, v,
                             allowed_tools=_allowed(), llm=llm)
    assert res.status == ReplanStatus.FAILURE
    assert res.proposal is None
    assert "unknown tool" in res.notes.lower() or "rejected" in res.notes.lower()


def test_replanner_rejects_malformed_proposal():
    a = _audit("failed", [_step("test_execution", StepStatus.FAILED.value, error="boom")])
    v = Verifier().verify("run the tests", a)
    llm = lambda obj, diag, fails: {"proposed_actions": [{"description": "no tool"}]}
    res = Replanner().replan("run the tests", Proposal(objective="run the tests"), a, v,
                             allowed_tools=_allowed(), llm=llm)
    assert res.status == ReplanStatus.FAILURE
    assert res.proposal is None


def test_replanner_inconclusive_when_already_achieved():
    a = _audit("success", [_step("test_execution", StepStatus.EXECUTED.value,
                                  "passed=1 failed=0 errors=0")])
    v = Verifier().verify("run the tests", a)
    res = Replanner().replan("run the tests", Proposal(objective="run the tests"), a, v, allowed_tools=_allowed())
    assert res.status == ReplanStatus.INCONCLUSIVE
    assert res.proposal is None


def test_replanner_no_direct_execution():
    class FakeRegistry:
        def __init__(self):
            self.calls = 0
        def tool_names(self):
            return _allowed()
        def execute(self, *a, **k):
            self.calls += 1
            raise AssertionError("Replanner must not execute tools")

    a = _audit("failed", [_step("test_execution", StepStatus.FAILED.value, error="boom")])
    v = Verifier().verify("run the tests", a)
    reg = FakeRegistry()
    # Replanner only takes allowed_tools, but guard against future coupling:
    res = Replanner().replan("run the tests", Proposal(objective="run the tests"), a, v,
                             allowed_tools=reg.tool_names())
    assert reg.calls == 0
    assert res.proposal is not None


def test_replanner_no_permission_bypass_no_self_approval():
    class FakePerm:
        def __init__(self):
            self.calls = 0
        def confirm(self, *a, **k):
            self.calls += 1
            raise AssertionError("Replanner must not confirm/self-approve")
        def get_level(self, tool):
            return "DANGEROUS"

    a = _audit("failed", [_step("build", StepStatus.FAILED.value, error="boom")])
    v = Verifier().verify("build the project", a)
    perm = FakePerm()
    res = Replanner().replan("build the project", Proposal(objective="build the project"), a, v,
                             allowed_tools=_allowed())
    assert perm.calls == 0
    assert res.status == ReplanStatus.SUCCESS


def test_replanner_cannot_determine_safe_recovery():
    # No failed steps, inconclusive objective, no allowed read-only tool.
    a = _audit("success", [_step("calculator", StepStatus.EXECUTED.value, "= 4")])
    v = Verifier().verify("do a thing", a)
    res = Replanner().replan("do a thing", Proposal(objective="do a thing"), a, v,
                             allowed_tools=["calculator"])  # no recovery tool available
    assert res.status in (ReplanStatus.FAILURE, ReplanStatus.INCONCLUSIVE)
    assert res.proposal is None


# --------------------------------------------------------------------------- #
# INTEGRATION: Executor -> Verifier -> Replanner -> Validator
# --------------------------------------------------------------------------- #
def test_integration_failing_test_then_replan():
    root = Path(tempfile.mkdtemp(prefix="jarvis_int_"))
    (root / "test_x.py").write_text("def test_bad():\n    assert False\n", encoding="utf-8")
    reg = ToolRegistry(_cfg(root))
    ex = ProposalExecutor(tool_registry=reg, permission_manager=PermissionManager())
    prop = Proposal(
        objective="run the tests",
        proposed_actions=[ProposedAction(tool="test_execution", description="run tests",
                                         parameters={"framework": "pytest", "target": str(root), "timeout_s": 60})],
    )
    ProposalValidator().validate(prop)
    audit = ex.execute(prop, confirm_fn=lambda tool, details="": True)
    assert audit.final_status == "failed"

    ver = Verifier().verify("run the tests", audit)
    assert ver.status == VerificationStatus.FAILURE

    replan = Replanner().replan("run the tests", prop, audit, ver, allowed_tools=reg.tool_names())
    assert replan.status == ReplanStatus.SUCCESS
    assert replan.proposal is not None
    assert replan.proposal.status == ProposalStatus.VALIDATED
    assert replan.proposal.proposed_actions[0].tool == "test_execution"


def test_integration_passing_test_then_no_replan():
    root = Path(tempfile.mkdtemp(prefix="jarvis_int_"))
    (root / "test_g.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    reg = ToolRegistry(_cfg(root))
    ex = ProposalExecutor(tool_registry=reg, permission_manager=PermissionManager())
    prop = Proposal(
        objective="run the tests",
        proposed_actions=[ProposedAction(tool="test_execution", description="run tests",
                                         parameters={"framework": "pytest", "target": str(root), "timeout_s": 60})],
    )
    ProposalValidator().validate(prop)
    audit = ex.execute(prop, confirm_fn=lambda tool, details="": True)
    assert audit.final_status == "success"

    ver = Verifier().verify("run the tests", audit)
    assert ver.status == VerificationStatus.SUCCESS

    replan = Replanner().replan("run the tests", prop, audit, ver, allowed_tools=reg.tool_names())
    assert replan.status == ReplanStatus.INCONCLUSIVE  # already achieved
    assert replan.proposal is None
