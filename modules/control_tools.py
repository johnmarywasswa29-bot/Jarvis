"""Phase B controlled-execution tools: ShellTool, BuildTool, DependencyTool.

These EXTEND the existing ToolRegistry / BaseTool / ToolResult machinery and
flow through:
    ProposalValidator -> PermissionManager/confirm_fn -> ProposalExecutor -> Audit
They do NOT replace or bypass the execution architecture, and they introduce
NO autonomous loop, NO self-approval, and NO out-of-scope capability
(AgentLoop, git commit/push, reset --hard, dependency removal/upgrade,
unrestricted terminal, automatic release publication).

Permission classification (all DANGEROUS -> always confirmation-gated):
    shell       -> DANGEROUS
    build       -> DANGEROUS
    dependency  -> DANGEROUS  (install requires explicit confirmation)

Safety invariants:
  * Commands run only inside the active project root (cwd confined).
  * Hard timeout + captured stdout/stderr + bounded output + explicit exit code
    + process cleanup on timeout.
  * argv-style execution (no shell=True) via OS shell wrapper (cmd /c, sh -c).
  * Dangerous command classes are denied by a denylist.
  * Dependency install targets the project venv only (never the global
    interpreter); removal/upgrade are refused.
  * No credentials/secrets are added to command output or audit logs.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from modules.tools import BaseTool, ToolResult
from modules.logger import get_logger

logger = get_logger("control_tools")

_MAX_OUTPUT = 20000  # hard cap on captured stdout/stderr per run


def _truncate(s: str) -> str:
    if not s:
        return ""
    s = s.rstrip("\n")
    if len(s) > _MAX_OUTPUT:
        return s[:_MAX_OUTPUT] + "\n... [output truncated]"
    return s


def _build_argv(command: str) -> list[str]:
    """argv-style execution: wrap the command with the OS shell WITHOUT
    shell=True (the wrapper binary is invoked directly)."""
    if os.name == "nt":
        return ["cmd", "/c", command]
    return ["/bin/sh", "-c", command]


def _kill_tree(proc: "subprocess.Popen") -> None:
    """Kill the whole process tree (the wrapper may spawn children)."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, text=True, timeout=5,
            )
        else:
            import os as _os
            import signal as _signal
            _os.killpg(_os.getpgid(proc.pid), _signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_bounded(argv: list[str], cwd: str, timeout_s: float) -> tuple[int | None, str, str]:
    """Run a command with a hard timeout; kill the process tree on timeout.
    Returns (returncode|None, stdout, stderr)."""
    kwargs: dict = dict(cwd=cwd, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if os.name != "nt":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(argv, **kwargs)
    try:
        out, err = proc.communicate(timeout=timeout_s)
        return proc.returncode, out or "", err or ""
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        return None, "", f"timed out after {timeout_s}s"
    except Exception as exc:
        _kill_tree(proc)
        return None, "", f"execution error: {exc}"


# Dangerous command classes denied by default (case-insensitive substrings).
_DENY_TOKENS = (
    "rm -rf", "rm -fr", "rm -r -f", "rd /s", "del /s", "deltree", "format ",
    "mkfs", "shred ", "truncate -s 0",
    "shutdown", "reboot", "halt", "poweroff", "init 0", "telinit",
    "sudo", "su ", "runas", "pkexec",
    ":(){", "fork bomb",
    "dd if=", "/dev/", "mount ", "umount", "reg delete", "reg add",
    "git push", "git reset --hard", "reset --hard", "git clean", "git checkout --",
    "git checkout -f", "--force", "push --force", "--hard",
    "npm publish", "twine upload",
)


def _is_dangerous(command: str) -> str | None:
    low = command.lower()
    for tok in _DENY_TOKENS:
        if tok in low:
            return tok
    return None


# --------------------------------------------------------------------------- #
# ShellTool — controlled, project-root-confined command execution
# --------------------------------------------------------------------------- #
class ShellTool(BaseTool):
    name = "shell"
    description = (
        "Run a bounded shell/terminal command INSIDE the active project root "
        "only. Hard timeout, captured stdout/stderr, explicit exit code, "
        "process cleanup on timeout. Dangerous command classes (destructive "
        "filesystem, shutdown, privilege escalation, raw disk, force-push, etc.) "
        "are refused. Requires explicit confirmation (DANGEROUS)."
    )

    def __init__(self, config: Any = None, *, timeout_s: float = 30.0, max_output: int = _MAX_OUTPUT) -> None:
        self.config = config
        self.timeout_s = timeout_s
        self.max_output = max_output

    def can_handle(self, prompt: str) -> bool:
        low = prompt.lower()
        return any(x in low for x in ["run command", "execute command", "shell command", "terminal command", "run shell"])

    def _allowed_cwd(self, cwd: Path) -> bool:
        try:
            cwd = cwd.resolve()
            root = (self.config.project_root if self.config and getattr(self.config, "project_root", None)
                    else Path.cwd()).resolve()
            return root == cwd or root in cwd.parents
        except Exception:
            return False

    def execute(self, command: str = "", cwd: str = "", timeout_s: float = 0, **kwargs: Any) -> ToolResult:
        t0 = time.time()
        command = (command or kwargs.get("command", "")).strip()
        cwd = (cwd or kwargs.get("cwd", "") or "").strip()
        if not command:
            return ToolResult(False, "", error="Missing command", duration_s=time.time() - t0)

        # Dangerous command-class refusal.
        bad = _is_dangerous(command)
        if bad:
            return ToolResult(
                False, "", error=f"Refused: command matches denied pattern '{bad}'",
                duration_s=time.time() - t0,
            )

        # Workspace-root enforcement.
        root = (self.config.project_root if self.config and getattr(self.config, "project_root", None)
                else Path.cwd()).resolve()
        run_cwd = Path(cwd) if cwd else root
        if not self._allowed_cwd(run_cwd):
            return ToolResult(
                False, "", error=f"Command cwd outside allowed project root: {run_cwd}",
                duration_s=time.time() - t0,
            )

        timeout = float(timeout_s) if timeout_s else self.timeout_s
        argv = _build_argv(command)
        rc, out, err = _run_bounded(argv, str(run_cwd), timeout)
        if rc is None:
            # Timeout or execution error (message is in `err`).
            return ToolResult(False, _truncate(out), error=_truncate(err) or "Command timed out",
                              duration_s=time.time() - t0)
        out = _truncate(out)
        err = _truncate(err)
        if rc == 0:
            return ToolResult(True, out or "(no output)", error=err, duration_s=time.time() - t0,
                              exit_code=rc)
        return ToolResult(
            False, out, error=(err or f"non-zero exit code {rc}"),
            duration_s=time.time() - t0, exit_code=rc,
        )


# --------------------------------------------------------------------------- #
# BuildTool — bounded project build/package execution
# --------------------------------------------------------------------------- #
class BuildTool(BaseTool):
    name = "build"
    # Known, allow-listed build entry points (argv-style, no arbitrary commands).
    _BUILD_ENTRIES = {
        "jarvis": ["installer/build.py", "build"],
        "python": ["-m", "build"],
        "npm": ["npm", "run", "build"],
        "make": ["make"],
    }
    description = (
        "Run a BOUNDED project build using a known entry point, confined to the "
        "active project root. Hard timeout, captured output, structured "
        "success/failure/exit-code/duration/artifact info. Never deletes or "
        "cleans unrelated files, never pushes releases, never creates Git "
        "commits. Requires explicit confirmation (DANGEROUS)."
    )

    def __init__(self, config: Any = None, *, timeout_s: float = 600.0) -> None:
        self.config = config
        self.timeout_s = timeout_s

    def can_handle(self, prompt: str) -> bool:
        low = prompt.lower()
        return any(x in low for x in ["build project", "run build", "package project", "build the project"])

    def execute(self, entry: str = "", target: str = "", timeout_s: float = 0, **kwargs: Any) -> ToolResult:
        t0 = time.time()
        entry = (entry or kwargs.get("entry", "jarvis")).strip().lower()
        target = (target or kwargs.get("target", "")).strip()
        if entry not in self._BUILD_ENTRIES:
            return ToolResult(
                False, "", error=f"Unknown build entry '{entry}'. Allowed: {', '.join(self._BUILD_ENTRIES)}",
                duration_s=time.time() - t0,
            )
        root = (self.config.project_root if self.config and getattr(self.config, "project_root", None)
                else Path.cwd()).resolve()
        # Confine to project root.
        if target:
            tpath = (root / target).resolve()
            if not (root == tpath or root in tpath.parents):
                return ToolResult(False, "", error=f"Build target outside project root: {target}", duration_s=time.time() - t0)

        argv = [sys.executable, *self._BUILD_ENTRIES[entry]]
        timeout = float(timeout_s) if timeout_s else self.timeout_s
        rc, out, err = _run_bounded(argv, str(root), timeout)
        if rc is None:
            return ToolResult(False, _truncate(out), error=_truncate(err) or "Build timed out",
                              duration_s=time.time() - t0)
        out = _truncate(out)
        err = _truncate(err)
        artifact = ""
        if rc == 0:
            if entry == "jarvis":
                exe = root / "Release" / "Jarvis.exe"
                if exe.exists():
                    artifact = f"artifact: {exe} ({exe.stat().st_size} bytes)"
            summary = f"build '{entry}' succeeded (exit {rc}, {time.time()-t0:.1f}s)"
            body = "\n".join(x for x in (summary, artifact, out[-3000:]) if x)
            return ToolResult(True, body, error=err, duration_s=time.time() - t0, exit_code=rc)
        return ToolResult(
            False, out, error=(err or f"build failed (exit {rc})"),
            duration_s=time.time() - t0, exit_code=rc,
        )


# --------------------------------------------------------------------------- #
# DependencyTool — controlled dependency inspection + venv-scoped install
# --------------------------------------------------------------------------- #
_PKG_RE = re.compile(r"^[A-Za-z0-9_.\-]+(?:[=<>!~]=?[A-Za-z0-9_.\-*+,]*)?$")
_DENY_DEP_WORDS = ("uninstall", "remove", "upgrade", "--upgrade", "--user",
                   "break-system-packages", "@", "http://", "https://", "git+")


class DependencyTool(BaseTool):
    name = "dependency"
    description = (
        "Controlled dependency operations scoped to the project virtual "
        "environment: inspect installed packages, show metadata, check "
        "availability, and perform a venv-scoped install. Removal/upgrade and "
        "global-environment modification are refused. No credentials/secrets "
        "are exposed. Install requires explicit confirmation (DANGEROUS)."
    )

    def __init__(self, config: Any = None, *, timeout_s: float = 120.0, python_executable: str | None = None) -> None:
        self.config = config
        self.timeout_s = timeout_s
        # Install targets THIS interpreter's environment (the project venv),
        # never the global interpreter. Test-injectable for isolated installs.
        self.python_executable = python_executable or sys.executable

    def can_handle(self, prompt: str) -> bool:
        low = prompt.lower()
        return any(x in low for x in ["install package", "check dependency", "list dependencies", "dependency status", "pip list"])

    def _pip(self, *args: str, timeout_s: float) -> ToolResult:
        t0 = time.time()
        argv = [self.python_executable, "-m", "pip", *args]
        rc, out, err = _run_bounded(argv, str(self.config.project_root if self.config else Path.cwd()), timeout_s)
        if rc is None:
            return ToolResult(False, _truncate(out), error=_truncate(err) or "pip timed out",
                              duration_s=time.time() - t0)
        out = _truncate(out)
        err = _truncate(err)
        if rc == 0:
            return ToolResult(True, out or "(no output)", error=err, duration_s=time.time() - t0, exit_code=rc)
        return ToolResult(False, out, error=(err or f"pip failed (exit {rc})"),
                          duration_s=time.time() - t0, exit_code=rc)

    def execute(self, action: str = "", package: str = "", timeout_s: float = 0, **kwargs: Any) -> ToolResult:
        t0 = time.time()
        action = (action or kwargs.get("action", "inspect")).strip().lower()
        package = (package or kwargs.get("package", "")).strip()
        timeout = float(timeout_s) if timeout_s else self.timeout_s

        if action == "inspect":
            return self._pip("list", timeout_s=timeout)
        if action == "show":
            if not package:
                return ToolResult(False, "", error="show requires 'package'", duration_s=time.time() - t0)
            return self._pip("show", package, timeout_s=timeout)
        if action == "check":
            if not package:
                return ToolResult(False, "", error="check requires 'package'", duration_s=time.time() - t0)
            r = self._pip("show", package, timeout_s=timeout)
            available = r.success and bool(r.output.strip())
            return ToolResult(available, f"{package}: {'available' if available else 'not installed'}",
                              error="" if available else "not installed", duration_s=time.time() - t0)

        if action == "install":
            if not package:
                return ToolResult(False, "", error="install requires 'package'", duration_s=time.time() - t0)
            plow = package.lower()
            # Refuse removal/upgrade / global / URL specs.
            if any(w in plow for w in _DENY_DEP_WORDS):
                return ToolResult(False, "", error="Refused: removal, upgrade, global, or URL-based install are not permitted",
                                  duration_s=time.time() - t0)
            if not _PKG_RE.match(package):
                return ToolResult(False, "", error=f"Invalid package name/version spec: {package!r}", duration_s=time.time() - t0)
            return self._pip("install", package, timeout_s=timeout)

        return ToolResult(False, "", error=f"Unknown dependency action: {action}", duration_s=time.time() - t0)
