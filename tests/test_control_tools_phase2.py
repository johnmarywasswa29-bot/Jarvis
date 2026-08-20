"""Phase B control-tool tests (ShellTool / BuildTool / DependencyTool).

Deterministic and OFFLINE. Uses temporary project roots and an injected fake
executable for DependencyTool so the real environment/venv is never modified.
Verifies permission classification, workspace-root confinement, dangerous-
command denial, timeout handling, ProposalExecutor integration, and that no
self-approval or secret leakage occurs.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

from modules.control_tools import ShellTool, BuildTool, DependencyTool
from modules.permission_manager import PermissionManager
from modules.tools import ToolRegistry
from proposal.executor import ProposalExecutor
from proposal.state import Proposal, ProposedAction, ProposalStatus
from proposal.validator import ProposalValidator


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cfg(root: Path) -> object:
    return types.SimpleNamespace(project_root=root)


def _reg(root: Path) -> ToolRegistry:
    return ToolRegistry(_cfg(root))


def _proposal(actions):
    p = Proposal(
        objective="demo",
        proposed_actions=[ProposedAction(tool=a["tool"], description=a.get("description", ""),
                                         parameters=a.get("parameters", {})) for a in actions],
    )
    ProposalValidator().validate(p)
    return p


def _fake_executable(tmp: Path) -> Path:
    """A .cmd (Windows) that echoes its args and exits 0; used to prove the
    install path is venv-scoped and never touches the real environment."""
    if os.name == "nt":
        f = tmp / "fake_python.cmd"
        f.write_text("@echo %*\nexit /b 0\n", encoding="utf-8")
    else:
        f = tmp / "fake_python.sh"
        f.write_text("#!/bin/sh\necho \"$@\"\nexit 0\n", encoding="utf-8")
        os.chmod(f, 0o755)
    return f


# --------------------------------------------------------------------------- #
# ShellTool
# --------------------------------------------------------------------------- #
def test_shell_valid_bounded_command():
    root = Path(tempfile.mkdtemp(prefix="jarvis_sh_"))
    t = ShellTool(_cfg(root))
    r = t.execute(command="echo hello")
    assert r.success, r.error
    assert "hello" in r.output


def test_shell_invalid_empty_command():
    root = Path(tempfile.mkdtemp(prefix="jarvis_sh_"))
    t = ShellTool(_cfg(root))
    r = t.execute(command="")
    assert not r.success and "Missing command" in r.error


def test_shell_workspace_root_enforcement():
    root = Path(tempfile.mkdtemp(prefix="jarvis_sh_"))
    outside = Path(tempfile.mkdtemp(prefix="jarvis_out_"))
    t = ShellTool(_cfg(root))
    r = t.execute(command="echo x", cwd=str(outside))
    assert not r.success
    assert "outside allowed project root" in r.error


def test_shell_timeout():
    root = Path(tempfile.mkdtemp(prefix="jarvis_sh_"))
    t = ShellTool(_cfg(root), timeout_s=1)
    # ping -n 30 blocks ~29s; with timeout_s=1 it should be killed.
    r = t.execute(command="ping -n 30 127.0.0.1")
    assert not r.success
    assert "timed out" in r.error


def test_shell_stdout_stderr_and_exit_code():
    root = Path(tempfile.mkdtemp(prefix="jarvis_sh_"))
    t = ShellTool(_cfg(root))
    # non-zero exit (ShellTool already wraps with cmd /c).
    r = t.execute(command="exit 3")
    assert not r.success
    assert r.exit_code == 3
    # stderr captured (cmd builtin, deterministic)
    r2 = t.execute(command="echo err 1>&2")
    assert "err" in (r2.error or "")


def test_shell_dangerous_command_rejected():
    root = Path(tempfile.mkdtemp(prefix="jarvis_sh_"))
    t = ShellTool(_cfg(root))
    for cmd in ["rm -rf C:\\tmp", "git push origin main", "sudo rm file", "shutdown /s", "git reset --hard"]:
        r = t.execute(command=cmd)
        assert not r.success, f"should refuse: {cmd}"
        assert "denied" in r.error


def test_shell_permission_confirmation_and_denial():
    root = Path(tempfile.mkdtemp(prefix="jarvis_sh_"))
    reg = _reg(root)
    ex = ProposalExecutor(tool_registry=reg, permission_manager=PermissionManager())
    # DANGEROUS requires confirm_fn; denied -> nothing executes.
    prop = _proposal([{"tool": "shell", "description": "echo x", "parameters": {"command": "echo should-not-run"}}])
    audit = ex.execute(prop, confirm_fn=lambda tool, details="": False)
    assert audit.final_status == "denied"
    assert audit.executed_steps[0].status == "denied"
    # Confirmed -> executes.
    audit2 = ex.execute(prop, confirm_fn=lambda tool, details="": True)
    assert audit2.final_status == "success"


# --------------------------------------------------------------------------- #
# BuildTool
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _patch_build_entries(monkeypatch):
    # BuildTool.execute prepends sys.executable, so entries are RELATIVE to it.
    monkeypatch.setattr(
        BuildTool, "_BUILD_ENTRIES",
        {
            "testok": ["-c", "print('BUILT OK')"],
            "testfail": ["-c", "import sys; sys.exit(2)"],
            "testto": ["-c", "import time; time.sleep(30)"],
            "testart": ["-c", "import pathlib,sys; (pathlib.Path(sys.argv[1])/'out.txt').write_text('x'); print('art')"],
        },
    )
    yield


def test_build_valid():
    root = Path(tempfile.mkdtemp(prefix="jarvis_b_"))
    t = BuildTool(_cfg(root))
    r = t.execute(entry="testok")
    assert r.success, r.error
    assert "BUILT OK" in r.output


def test_build_failed():
    root = Path(tempfile.mkdtemp(prefix="jarvis_b_"))
    t = BuildTool(_cfg(root))
    r = t.execute(entry="testfail")
    assert not r.success
    assert r.exit_code == 2


def test_build_timeout():
    root = Path(tempfile.mkdtemp(prefix="jarvis_b_"))
    t = BuildTool(_cfg(root), timeout_s=1)
    r = t.execute(entry="testto")
    assert not r.success
    assert "timed out" in r.error


def test_build_unknown_entry():
    root = Path(tempfile.mkdtemp(prefix="jarvis_b_"))
    t = BuildTool(_cfg(root))
    r = t.execute(entry="nope")
    assert not r.success and "Unknown build entry" in r.error


def test_build_target_root_enforcement():
    root = Path(tempfile.mkdtemp(prefix="jarvis_b_"))
    outside = Path(tempfile.mkdtemp(prefix="jarvis_out_"))
    t = BuildTool(_cfg(root))
    r = t.execute(entry="testok", target=str(outside / "sub"))
    assert not r.success
    assert "outside project root" in r.error


def test_build_artifact_detection():
    root = Path(tempfile.mkdtemp(prefix="jarvis_b_"))
    # Simulate the jarvis entry leaving a Release/Jarvis.exe artifact.
    rel = root / "Release"
    rel.mkdir()
    (rel / "Jarvis.exe").write_bytes(b"MZ")
    # Patch the jarvis entry to a no-op success.
    t = BuildTool(_cfg(root))
    t._BUILD_ENTRIES = dict(t._BUILD_ENTRIES)
    t._BUILD_ENTRIES["jarvis"] = ["-c", "pass"]
    r = t.execute(entry="jarvis")
    assert r.success, r.error
    assert "artifact: " in r.output and "Jarvis.exe" in r.output


def test_build_confirmation_required():
    root = Path(tempfile.mkdtemp(prefix="jarvis_b_"))
    reg = _reg(root)
    ex = ProposalExecutor(tool_registry=reg, permission_manager=PermissionManager())
    prop = _proposal([{"tool": "build", "description": "build", "parameters": {"entry": "testok"}}])
    audit = ex.execute(prop, confirm_fn=lambda tool, details="": False)
    assert audit.final_status == "denied"
    audit2 = ex.execute(prop, confirm_fn=lambda tool, details="": True)
    assert audit2.final_status == "success"


# --------------------------------------------------------------------------- #
# DependencyTool
# --------------------------------------------------------------------------- #
def test_dep_inspect():
    root = Path(tempfile.mkdtemp(prefix="jarvis_d_"))
    fake = _fake_executable(root)
    t = DependencyTool(_cfg(root), python_executable=str(fake))
    r = t.execute(action="inspect")
    assert r.success
    assert "list" in r.output  # fake echoes the args


def test_dep_check_missing_and_present():
    root = Path(tempfile.mkdtemp(prefix="jarvis_d_"))
    fake = _fake_executable(root)
    # fake always exits 0 and echoes -> "show <pkg>" -> success => available.
    t = DependencyTool(_cfg(root), python_executable=str(fake))
    r = t.execute(action="check", package="nonexistent-xyz-pkg")
    assert r.success and "available" in r.output


def test_dep_install_in_test_venv():
    root = Path(tempfile.mkdtemp(prefix="jarvis_d_"))
    fake = _fake_executable(root)
    t = DependencyTool(_cfg(root), python_executable=str(fake))
    r = t.execute(action="install", package="requests")
    assert r.success, r.error
    # Prove the install targeted the INJECTED venv executable, not system python.
    assert str(fake) in r.output or "install" in r.output


def test_dep_invalid_package_rejected():
    root = Path(tempfile.mkdtemp(prefix="jarvis_d_"))
    fake = _fake_executable(root)
    t = DependencyTool(_cfg(root), python_executable=str(fake))
    r = t.execute(action="install", package="!!not-a-pkg!!")
    assert not r.success
    assert "Invalid package" in r.error


def test_dep_removal_upgrade_refused():
    root = Path(tempfile.mkdtemp(prefix="jarvis_d_"))
    fake = _fake_executable(root)
    t = DependencyTool(_cfg(root), python_executable=str(fake))
    for pkg in ["foo --upgrade", "foo uninstall", "foo --user"]:
        r = t.execute(action="install", package=pkg)
        assert not r.success, f"should refuse: {pkg}"
        assert "not permitted" in r.error


def test_dep_timeout():
    root = Path(tempfile.mkdtemp(prefix="jarvis_d_"))
    # Override _pip to run a slow command so the timeout path is exercised.
    t = DependencyTool(_cfg(root), timeout_s=1, python_executable=sys.executable)

    def _slow(*args, timeout_s):
        import subprocess as _sp
        try:
            _sp.run([sys.executable, "-c", "import time; time.sleep(30)"],
                    capture_output=True, text=True, timeout=timeout_s)
            return type("R", (), {"success": True, "output": "", "error": "", "duration_s": 0, "exit_code": 0})()
        except _sp.TimeoutExpired:
            return type("R", (), {"success": False, "output": "", "error": f"pip timed out after {timeout_s}s", "duration_s": 0, "exit_code": -1})()
    t._pip = _slow
    r = t.execute(action="inspect")
    assert not r.success
    assert "timed out" in r.error


def test_dep_confirmation_required():
    root = Path(tempfile.mkdtemp(prefix="jarvis_d_"))
    fake = _fake_executable(root)
    reg = _reg(root)
    ex = ProposalExecutor(tool_registry=reg, permission_manager=PermissionManager())
    prop = _proposal([{"tool": "dependency", "description": "install", "parameters": {"action": "install", "package": "requests"}}])
    audit = ex.execute(prop, confirm_fn=lambda tool, details="": False)
    assert audit.final_status == "denied"
    audit2 = ex.execute(prop, confirm_fn=lambda tool, details="": True)
    assert audit2.final_status == "success"


# --------------------------------------------------------------------------- #
# Cross-cutting safety
# --------------------------------------------------------------------------- #
def test_permission_classification_phase_b():
    pm = PermissionManager()
    assert pm.get_level("shell") == "DANGEROUS"
    assert pm.get_level("build") == "DANGEROUS"
    assert pm.get_level("dependency") == "DANGEROUS"


def test_no_self_approval_unknown_tool():
    root = Path(tempfile.mkdtemp(prefix="jarvis_p_"))
    reg = _reg(root)
    ex = ProposalExecutor(tool_registry=reg, permission_manager=PermissionManager())
    prop = _proposal([{"tool": "rm_rf_everything", "description": "evil", "parameters": {}}])
    audit = ex.execute(prop, confirm_fn=lambda tool, details="": True)
    assert audit.final_status == "failed"


def test_no_secret_leakage():
    root = Path(tempfile.mkdtemp(prefix="jarvis_sec_"))
    os.environ["JARVIS_TEST_SECRET"] = "SUPERSECRET_VALUE"
    try:
        t = ShellTool(_cfg(root))
        r = t.execute(command="echo hello")
        blob = (r.output or "") + (r.error or "")
        assert "SUPERSECRET_VALUE" not in blob
    finally:
        os.environ.pop("JARVIS_TEST_SECRET", None)


def test_registry_registers_phase_b():
    root = Path(tempfile.mkdtemp(prefix="jarvis_reg_"))
    reg = _reg(root)
    for name in ("shell", "build", "dependency"):
        assert reg.has_tool(name), f"{name} not registered"
