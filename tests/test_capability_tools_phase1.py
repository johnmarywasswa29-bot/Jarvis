"""Phase A capability-tool tests (read-only git / CAUTION file-edit / test / workspace).

Deterministic and OFFLINE: uses temporary git repos and project dirs, never
the user's real repository. Verifies permission classification, project-root
boundaries, read-back verification, and ProposalExecutor integration.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import types
from pathlib import Path
import tempfile

import pytest

from modules.capability_tools import (
    GitTool,
    FileEditTool,
    ProjectTestTool,
    WorkspaceObserveTool,
)
from modules.permission_manager import PermissionManager
from modules.tools import ToolRegistry
from proposal.executor import ProposalExecutor
from proposal.state import Proposal, ProposedAction, ProposalStatus
from proposal.validator import ProposalValidator


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _make_repo() -> Path:
    d = Path(tempfile.mkdtemp(prefix="jarvis_git_"))
    subprocess.run(["git", "init", "-q"], cwd=str(d), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(d), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(d), check=True)
    (d / "a.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(d), check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=str(d), check=True)
    return d


def _cfg(root: Path) -> object:
    return types.SimpleNamespace(project_root=root)


# --------------------------------------------------------------------------- #
# GitTool — read-only operations
# --------------------------------------------------------------------------- #
def test_git_status_readonly():
    repo = _make_repo()
    tool = GitTool(_cfg(repo))
    r = tool.execute(command="status --porcelain", repo=str(repo))
    assert r.success, r.error
    # still a git repo, no error
    assert "a.txt" not in r.output  # clean tree => porcelain empty


def test_git_log_branch():
    repo = _make_repo()
    tool = GitTool(_cfg(repo))
    r = tool.execute(command="log --oneline -5", repo=str(repo))
    assert r.success and "init" in r.output
    b = tool.execute(command="branch", repo=str(repo))
    assert b.success and "*" in b.output


def test_git_diff():
    repo = _make_repo()
    (repo / "a.txt").write_text("hello world\n", encoding="utf-8")
    tool = GitTool(_cfg(repo))
    r = tool.execute(command="diff", repo=str(repo))
    assert r.success and "hello world" in r.output


def test_git_refuses_modifying_subcommand():
    repo = _make_repo()
    tool = GitTool(_cfg(repo))
    for cmd in ["commit -m x", "push origin main", "reset --hard", "checkout main", "merge dev", "rebase dev"]:
        r = tool.execute(command=cmd, repo=str(repo))
        assert not r.success, f"should refuse: {cmd}"
        assert "not permitted" in r.error or "forbidden token" in r.error


def test_git_refuses_outside_project_root():
    repo = _make_repo()
    other = Path(tempfile.mkdtemp(prefix="jarvis_other_"))
    tool = GitTool(_cfg(repo))  # allowed root = repo only
    r = tool.execute(command="status", repo=str(other))
    assert not r.success
    assert "outside allowed project root" in r.error


# --------------------------------------------------------------------------- #
# FileEditTool — boundary + read-back
# --------------------------------------------------------------------------- #
def test_file_edit_inside_root():
    root = Path(tempfile.mkdtemp(prefix="jarvis_proj_"))
    f = root / "code.py"
    f.write_text("x = 1\ny = 2\n", encoding="utf-8")
    tool = FileEditTool(_cfg(root))
    r = tool.execute(action="edit", path=str(f), old="x = 1", new="x = 100")
    assert r.success, r.error
    content = f.read_text(encoding="utf-8")
    assert "x = 100" in content            # new value present
    assert "x = 1\n" not in content        # old line fully gone (not substring trap)


def test_file_edit_outside_root_rejected():
    root = Path(tempfile.mkdtemp(prefix="jarvis_proj_"))
    outside = Path(tempfile.mkdtemp(prefix="jarvis_outside_")) / "x.txt"
    outside.write_text("a", encoding="utf-8")
    tool = FileEditTool(_cfg(root))
    r = tool.execute(action="edit", path=str(outside), old="a", new="b")
    assert not r.success
    assert "outside allowed project roots" in r.error
    assert outside.read_text(encoding="utf-8") == "a"  # unchanged


def test_file_edit_old_not_found():
    root = Path(tempfile.mkdtemp(prefix="jarvis_proj_"))
    f = root / "c.py"
    f.write_text("data = 0\n", encoding="utf-8")
    tool = FileEditTool(_cfg(root))
    r = tool.execute(action="edit", path=str(f), old="NOPE", new="1")
    assert not r.success and "not found" in r.error


def test_file_edit_patch_application():
    root = Path(tempfile.mkdtemp(prefix="jarvis_proj_"))
    f = root / "m.py"
    f.write_text("def f():\n    return 1\n", encoding="utf-8")
    patch = (
        "--- a/m.py\n+++ b/m.py\n@@\n def f():\n-    return 1\n+    return 2\n"
    )
    tool = FileEditTool(_cfg(root))
    r = tool.execute(action="patch", path=str(f), patch=patch)
    assert r.success, r.error
    assert "return 2" in f.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# TestTool — pass / fail / timeout
# --------------------------------------------------------------------------- #
def _write_test(root: Path, body: str, name: str = "test_sample.py") -> None:
    (root / name).write_text(textwrap.dedent(body), encoding="utf-8")


def test_testtool_pass():
    root = Path(tempfile.mkdtemp(prefix="jarvis_t_"))
    _write_test(root, """
        def test_ok():
            assert 1 + 1 == 2
    """)
    tool = ProjectTestTool(_cfg(root))
    r = tool.execute(framework="pytest", target=str(root), timeout_s=60)
    assert r.success, r.error
    assert "passed=1" in r.output


def test_testtool_fail():
    root = Path(tempfile.mkdtemp(prefix="jarvis_t_"))
    _write_test(root, """
        def test_bad():
            assert False
    """)
    tool = ProjectTestTool(_cfg(root))
    r = tool.execute(framework="pytest", target=str(root), timeout_s=60)
    assert not r.success
    assert "failed=1" in r.output


def test_testtool_timeout():
    root = Path(tempfile.mkdtemp(prefix="jarvis_t_"))
    _write_test(root, """
        import time
        def test_slow():
            time.sleep(30)
            assert True
    """)
    tool = ProjectTestTool(_cfg(root))
    r = tool.execute(framework="pytest", target=str(root), timeout_s=2)
    assert not r.success
    assert "timed out" in r.error


def test_testtool_no_claim_on_ambiguous():
    # A run that collects nothing must not be reported as success.
    root = Path(tempfile.mkdtemp(prefix="jarvis_t_"))
    (root / "not_a_test.py").write_text("x=1\n", encoding="utf-8")
    tool = ProjectTestTool(_cfg(root))
    r = tool.execute(framework="pytest", target=str(root), timeout_s=30)
    assert not r.success


# --------------------------------------------------------------------------- #
# WorkspaceObserveTool
# --------------------------------------------------------------------------- #
def test_workspace_observe():
    repo = _make_repo()
    tool = WorkspaceObserveTool(_cfg(repo))
    r = tool.execute()
    assert r.success, r.error
    assert "git_repo: True" in r.output
    assert "modified_files" in r.output


# --------------------------------------------------------------------------- #
# Permission classification
# --------------------------------------------------------------------------- #
def test_permission_classification():
    pm = PermissionManager()
    assert pm.get_level("git") == "SAFE"
    assert pm.get_level("test_execution") == "SAFE"
    assert pm.get_level("workspace_observe") == "SAFE"
    assert pm.get_level("file_edit") == "CAUTION"
    # existing levels untouched
    assert pm.get_level("calculator") == "SAFE"
    assert pm.get_level("code_execution") == "DANGEROUS"


# --------------------------------------------------------------------------- #
# ToolRegistry integration
# --------------------------------------------------------------------------- #
def test_registry_registers_new_tools():
    root = Path(tempfile.mkdtemp(prefix="jarvis_reg_"))
    reg = ToolRegistry(_cfg(root))
    for name in ("git", "file_edit", "test_execution", "workspace_observe"):
        assert reg.has_tool(name), f"{name} not registered"


# --------------------------------------------------------------------------- #
# ProposalValidator + ProposalExecutor integration
# --------------------------------------------------------------------------- #
def _proposal(actions):
    p = Proposal(
        objective="demo",
        proposed_actions=[ProposedAction(tool=a["tool"], description=a.get("description", ""),
                                         parameters=a.get("parameters", {})) for a in actions],
    )
    ProposalValidator().validate(p)
    return p


def test_executor_runs_safe_git_with_accept():
    root = _make_repo()
    reg = ToolRegistry(_cfg(root))
    pm = PermissionManager()
    ex = ProposalExecutor(tool_registry=reg, permission_manager=pm)
    prop = _proposal([{"tool": "git", "description": "status", "parameters": {"command": "status --porcelain", "repo": str(root)}}])
    audit = ex.execute(prop, confirm_fn=lambda tool, details="": True)
    assert audit.final_status == "success"
    assert audit.executed_steps[0].status == "executed"


def test_executor_caution_file_edit_denied_blocks_execution():
    root = Path(tempfile.mkdtemp(prefix="jarvis_p_"))
    f = root / "z.py"
    f.write_text("v=1\n", encoding="utf-8")
    reg = ToolRegistry(_cfg(root))
    pm = PermissionManager()
    ex = ProposalExecutor(tool_registry=reg, permission_manager=pm)
    prop = _proposal([{"tool": "file_edit", "description": "edit", "parameters": {"action": "edit", "path": str(f), "old": "v=1", "new": "v=2"}}])
    audit = ex.execute(prop, confirm_fn=lambda tool, details="": False)  # user denies
    assert audit.final_status == "denied"
    assert audit.executed_steps[0].status == "denied"
    assert "v=2" not in f.read_text(encoding="utf-8")  # nothing executed


def test_executor_unknown_tool_not_executed():
    root = Path(tempfile.mkdtemp(prefix="jarvis_p_"))
    reg = ToolRegistry(_cfg(root))
    pm = PermissionManager()
    ex = ProposalExecutor(tool_registry=reg, permission_manager=pm)
    prop = _proposal([{"tool": "shell_rm_rf", "description": "evil", "parameters": {}}])
    audit = ex.execute(prop, confirm_fn=lambda tool, details="": True)
    assert audit.final_status == "failed"
    assert audit.executed_steps[0].status == "failed"
    assert "unknown or unsupported tool" in audit.executed_steps[0].error


def test_executor_validator_rejects_unknown_tool():
    prop = Proposal(
        objective="x",
        proposed_actions=[ProposedAction(tool="nonexistent_tool", description="d", parameters={})],
    )
    v = ProposalValidator()
    v.validate(prop)
    # Validation only checks structure; execution is what refuses unknown tools.
    assert prop.status == ProposalStatus.VALIDATED  # structurally valid
    reg = ToolRegistry(_cfg(Path(tempfile.mkdtemp(prefix="jarvis_p_"))))
    ex = ProposalExecutor(tool_registry=reg, permission_manager=PermissionManager())
    audit = ex.execute(prop, confirm_fn=lambda tool, details="": True)
    assert audit.final_status == "failed"
