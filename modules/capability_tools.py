"""Phase A capability tools: project/agent foundation (read-only + CAUTION edits).

These tools EXTEND the existing ToolRegistry / BaseTool / ToolResult machinery.
They do NOT introduce any autonomous, "god mode", or DANGEROUS execution:

  * GitTool          -> READ-ONLY git inspection only (status/diff/log/branch/...).
                        commit/push/reset/checkout/merge/rebase are refused.
  * FileEditTool     -> in-place edit / unified-diff apply, strict project-root
                        boundary, read-back verification. (CAUTION)
  * TestTool         -> bounded pytest/unittest execution, structured pass/fail.
                        Observation only; never claims success from ambiguous
                        output. (SAFE)
  * WorkspaceObserveTool -> exposes WorkspaceWatcher snapshot (git/modified/
                        test-framework/languages) to the agent. (SAFE)

Every consequential action still flows through:
  ProposalValidator -> PermissionManager/confirm_fn -> ProposalExecutor -> Audit
No self-approval; no ShellTool; no dependency management; no build execution.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from modules.tools import BaseTool, ToolResult
from modules.logger import get_logger

logger = get_logger("capability_tools")


# --------------------------------------------------------------------------- #
# GitTool — READ-ONLY inspection only
# --------------------------------------------------------------------------- #
class GitTool(BaseTool):
    name = "git"
    description = (
        "Read-only git repository inspection: status, diff, log, branch list, "
        "show, remote, rev-parse, ls-files. Never commits, pushes, resets, "
        "checks out, merges, or rebases."
    )

    # Subcommands explicitly permitted (read-only).
    _READ_SUBCOMMANDS = {
        "status", "diff", "log", "branch", "show", "remote", "rev-parse",
        "ls-files", "tag", "config", "describe", "shortlog", "blame",
    }
    # Any of these anywhere in the args => refused (modifying / dangerous).
    _FORBIDDEN_TOKENS = {
        "commit", "push", "pull", "fetch", "reset", "checkout", "merge",
        "rebase", "cherry-pick", "revert", "add", "mv", "rm", "clean",
        "stash", "am", "apply", "clone", "init", "switch", "--hard",
        "--soft", "--mixed", "--force", "-f", "--amend",
    }

    def __init__(self, config: Any = None, *, timeout_s: float = 15.0) -> None:
        self.config = config
        self.timeout_s = timeout_s

    def can_handle(self, prompt: str) -> bool:
        low = prompt.lower()
        return any(x in low for x in ["git status", "git diff", "git log", "git branch", "show git", "repository status"])

    def _allowed_root(self, path: Path) -> bool:
        # Strict boundary: git ops confined to the active project root.
        try:
            path = path.resolve()
            root = (self.config.project_root if self.config and getattr(self.config, "project_root", None)
                    else Path.cwd()).resolve()
            return root == path or root in path.parents
        except Exception:
            return False

    def execute(self, command: str = "", repo: str = "", path: str = "", **kwargs: Any) -> ToolResult:
        t0 = time.time()
        command = (command or kwargs.get("command", "")).strip()
        repo = (repo or path or kwargs.get("repo", "") or "").strip()
        if not command:
            return ToolResult(False, "", error="Missing git command", duration_s=time.time() - t0)

        # Parse first token as the subcommand; validate strictly.
        tokens = command.replace("--porcelain", " ").split()
        sub = tokens[0].lower() if tokens else ""
        if sub not in self._READ_SUBCOMMANDS:
            return ToolResult(
                False, "", error=f"Git subcommand '{sub}' is not permitted (read-only inspection only)",
                duration_s=time.time() - t0,
            )
        # Refuse any forbidden token anywhere in the command.
        lowered = command.lower()
        for tok in self._FORBIDDEN_TOKENS:
            if tok in lowered.split():
                return ToolResult(
                    False, "", error=f"Refusing git command containing forbidden token '{tok}'",
                    duration_s=time.time() - t0,
                )

        repo_path = Path(repo) if repo else (self.config.project_root if self.config else Path.cwd())
        if not self._allowed_root(repo_path):
            return ToolResult(
                False, "", error=f"Git repo path outside allowed project root: {repo_path}",
                duration_s=time.time() - t0,
            )
        if not (repo_path / ".git").exists():
            return ToolResult(False, "", error=f"Not a git repository: {repo_path}", duration_s=time.time() - t0)

        try:
            proc = subprocess.run(
                ["git", "-C", str(repo_path), *command.split()],
                capture_output=True, text=True, timeout=self.timeout_s,
            )
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            if proc.returncode != 0:
                return ToolResult(False, "", error=f"git {command} failed: {err or 'non-zero exit'}", duration_s=time.time() - t0)
            return ToolResult(True, out or "(no output)", error="", duration_s=time.time() - t0)
        except subprocess.TimeoutExpired:
            return ToolResult(False, "", error=f"git {command} timed out after {self.timeout_s}s", duration_s=time.time() - t0)
        except Exception as exc:
            return ToolResult(False, "", error=f"git error: {exc}", duration_s=time.time() - t0)


# --------------------------------------------------------------------------- #
# FileEditTool — in-place edit / patch, strict boundary, read-back verification
# --------------------------------------------------------------------------- #
class FileEditTool(BaseTool):
    name = "file_edit"
    description = (
        "Modify a file in place within the active project root: replace a string "
        "or apply a unified diff. Edits outside the project root are refused. "
        "Every modification is verified by read-back afterward."
    )

    def __init__(self, config: Any = None, *, allowed_roots: list | None = None) -> None:
        self.config = config
        if allowed_roots:
            self.allowed_roots = [Path(p).resolve() for p in allowed_roots]
        else:
            root = (config.project_root if config and getattr(config, "project_root", None) else Path.cwd())
            self.allowed_roots = [Path(root).resolve()]

    def can_handle(self, prompt: str) -> bool:
        low = prompt.lower()
        return any(x in low for x in ["edit file", "modify file", "change file", "apply patch", "apply diff", "update file"])

    def _allowed(self, path: Path) -> bool:
        try:
            path = path.resolve()
            return any(r == path or r in path.parents for r in self.allowed_roots)
        except Exception:
            return False

    def execute(
        self,
        action: str = "",
        path: str = "",
        old: str = "",
        new: str = "",
        patch: str = "",
        **kwargs: Any,
    ) -> ToolResult:
        t0 = time.time()
        action = (action or kwargs.get("action", "edit")).lower().strip()
        path = (path or kwargs.get("path", "")).strip()
        old = kwargs.get("old", old)
        new = kwargs.get("new", new)
        patch = kwargs.get("patch", patch)

        if not path:
            return ToolResult(False, "", error="Missing path", duration_s=time.time() - t0)
        fpath = Path(path)
        if not self._allowed(fpath):
            return ToolResult(
                False, "", error=f"Path outside allowed project roots: {fpath}",
                duration_s=time.time() - t0,
            )

        try:
            if action == "edit":
                if not old:
                    return ToolResult(False, "", error="edit requires 'old' text", duration_s=time.time() - t0)
                if not fpath.exists():
                    return ToolResult(False, "", error=f"File not found: {fpath}", duration_s=time.time() - t0)
                original = fpath.read_text(encoding="utf-8", errors="replace")
                if old not in original:
                    return ToolResult(
                        False, "", error="'old' substring not found in file (no change made)",
                        duration_s=time.time() - t0,
                    )
                updated = original.replace(old, new, 1)
                fpath.write_text(updated, encoding="utf-8")
                # Read-back verification: the file must equal the intended
                # post-edit text exactly (full equality, not naive substring
                # checks, which break when `new` embeds `old` as a substring).
                reread = fpath.read_text(encoding="utf-8", errors="replace")
                if reread != updated:
                    return ToolResult(
                        False, "", error="Read-back verification FAILED after edit",
                        duration_s=time.time() - t0,
                    )
                return ToolResult(True, f"Edited {fpath} (1 occurrence replaced; verified)", duration_s=time.time() - t0)

            elif action == "patch":
                if not patch:
                    return ToolResult(False, "", error="patch requires 'patch' unified diff text", duration_s=time.time() - t0)
                ok, msg = self._apply_unified_diff(fpath, patch)
                if not ok:
                    return ToolResult(False, "", error=msg, duration_s=time.time() - t0)
                return ToolResult(True, msg, duration_s=time.time() - t0)

            return ToolResult(False, "", error=f"Unknown file_edit action: {action}", duration_s=time.time() - t0)
        except Exception as exc:
            return ToolResult(False, "", error=f"file_edit error: {exc}", duration_s=time.time() - t0)

    def _apply_unified_diff(self, fpath: Path, patch: str) -> tuple[bool, str]:
        """Apply a simple unified diff (single file) with verification."""
        original = fpath.read_text(encoding="utf-8", errors="replace") if fpath.exists() else ""
        patch_lines = patch.replace("\r\n", "\n").split("\n")
        try:
            new_text = self._patch_text(original, patch_lines)
        except Exception as exc:
            return False, f"patch parse error: {exc}"
        fpath.write_text(new_text, encoding="utf-8")
        reread = fpath.read_text(encoding="utf-8", errors="replace")
        if reread != new_text:
            return False, "Read-back verification FAILED after patch"
        return True, f"Applied patch to {fpath} (verified)"

    @staticmethod
    def _patch_text(original: str, patch_lines: list[str]) -> str:
        """Minimal unified-diff applier (context + +/- lines)."""
        orig_lines = original.replace("\r\n", "\n").split("\n")
        result: list[str] = []
        oi = 0
        n = len(patch_lines)
        pi = 0
        while pi < n:
            line = patch_lines[pi]
            if line.startswith("@@"):
                pi += 1
                continue
            if line.startswith("--- ") or line.startswith("+++ ") or line.startswith("diff ") or line.startswith("index "):
                pi += 1
                continue
            if line.startswith("+"):
                result.append(line[1:])
                pi += 1
            elif line.startswith("-"):
                if oi < len(orig_lines) and orig_lines[oi] == line[1:]:
                    oi += 1
                pi += 1
            elif line.startswith(" "):
                if oi < len(orig_lines) and orig_lines[oi] == line[1:]:
                    result.append(line[1:])
                    oi += 1
                pi += 1
            else:
                pi += 1
        while oi < len(orig_lines):
            result.append(orig_lines[oi])
            oi += 1
        return "\n".join(result)


# --------------------------------------------------------------------------- #
# TestTool — bounded test execution, structured pass/fail, honest reporting
# --------------------------------------------------------------------------- #
class ProjectTestTool(BaseTool):
    name = "test_execution"
    description = (
        "Run project tests (pytest/unittest) with a hard timeout. Returns a "
        "structured pass/fail result and never claims success from ambiguous "
        "output."
    )

    def __init__(self, config: Any = None, *, timeout_s: float = 120.0) -> None:
        self.config = config
        self.timeout_s = timeout_s

    def can_handle(self, prompt: str) -> bool:
        low = prompt.lower()
        return any(x in low for x in ["run tests", "run the tests", "execute tests", "test suite", "pytest", "run unittest"])

    def execute(self, framework: str = "pytest", target: str = "", timeout_s: float = 0, **kwargs: Any) -> ToolResult:
        t0 = time.time()
        framework = (framework or kwargs.get("framework", "pytest")).lower().strip()
        target = (target or kwargs.get("target", "")).strip()
        timeout = float(timeout_s) if timeout_s else self.timeout_s

        if framework not in ("pytest", "unittest"):
            return ToolResult(False, "", error=f"Unsupported test framework: {framework}", duration_s=time.time() - t0)

        root = (self.config.project_root if self.config and getattr(self.config, "project_root", None) else Path.cwd())
        cmd: list[str]
        if framework == "pytest":
            cmd = ["python", "-m", "pytest", "-q", "--tb=short"]
            if target:
                cmd.append(target)
        else:
            cmd = ["python", "-m", "unittest"]
            if target:
                cmd.append(target if "." in target else f"discover -s {target}")

        try:
            proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return ToolResult(False, "", error=f"Test run timed out after {timeout}s", duration_s=time.time() - t0)

        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        import re as _re
        collected = passed = failed = error_tests = 0
        for line in out.splitlines():
            if "passed" in line or "failed" in line:
                passed += sum(int(x) for x in _re.findall(r"(\d+) passed", line))
                failed += sum(int(x) for x in _re.findall(r"(\d+) failed", line))
                error_tests += sum(int(x) for x in _re.findall(r"(\d+) error", line))
                m = _re.search(r"(\d+) (?:test|tests) collected", line)
                if m:
                    collected = int(m.group(1))

        success = (proc.returncode == 0 and failed == 0 and error_tests == 0 and passed > 0)
        # Never claim success from ambiguous output.
        if passed == 0 and "no tests ran" in out.lower():
            success = False
            out = out or "No tests were collected/run."

        summary = (
            f"framework={framework} returncode={proc.returncode} "
            f"passed={passed} failed={failed} errors={error_tests}"
        )
        if success:
            return ToolResult(True, f"{summary}\n\n{out[-2000:]}", error="", duration_s=time.time() - t0)
        return ToolResult(
            False, summary, error=(err[:1500] if err else "tests failed or inconclusive"),
            duration_s=time.time() - t0,
        )


# --------------------------------------------------------------------------- #
# WorkspaceObserveTool — expose project state to the agent (SAFE)
# --------------------------------------------------------------------------- #
class WorkspaceObserveTool(BaseTool):
    name = "workspace_observe"
    description = (
        "Report current workspace/project state: git repository, branch, dirty "
        "status, modified files, detected languages, and test framework. "
        "Read-only observation."
    )

    def __init__(self, config: Any = None) -> None:
        self.config = config

    def can_handle(self, prompt: str) -> bool:
        low = prompt.lower()
        return any(x in low for x in ["workspace state", "project state", "what files changed", "current project", "git repository status"])

    def execute(self, root: str = "", **kwargs: Any) -> ToolResult:
        t0 = time.time()
        try:
            from modules.workspace import WorkspaceWatcher
            root_path = Path(root) if root else (self.config.project_root if self.config else Path.cwd())
            watcher = WorkspaceWatcher()
            ctx = watcher.snapshot(root_path)
            if ctx is None or ctx.root is None:
                return ToolResult(False, "", error="Could not snapshot workspace", duration_s=time.time() - t0)
            lines = [
                f"root: {ctx.root}",
                f"git_repo: {ctx.git_repo}",
                f"git_branch: {ctx.git_branch}",
                f"dirty: {ctx.dirty}",
                f"modified_files: {', '.join(ctx.modified_files) or 'none'}",
                f"languages: {ctx.languages}",
                f"test_framework: {ctx.test_framework}",
                f"recently_edited: {', '.join(ctx.recently_edited[:10]) or 'none'}",
            ]
            return ToolResult(True, "\n".join(lines), duration_s=time.time() - t0)
        except Exception as exc:
            return ToolResult(False, "", error=f"workspace_observe error: {exc}", duration_s=time.time() - t0)
