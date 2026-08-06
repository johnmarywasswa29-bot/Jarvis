"""Execution manager: runs Python code in an isolated subprocess with timeout."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modules.config import JarvisConfig
from modules.logger import get_logger

try:
    from modules.permission_manager import PermissionManager

    _HAS_PERMS = True
except Exception:
    _HAS_PERMS = False

logger = get_logger("execution")


@dataclass
class ExecutionResult:
    success: bool = False
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    exit_code: int = -1
    approved: bool = True

    def to_tool_result(self) -> Any:
        from modules.tools import ToolResult

        if not self.approved:
            return ToolResult(False, "", error="Permission denied", duration_s=self.duration_s)
        if not self.success:
            return ToolResult(False, "", error=(self.stderr or self.stdout or "Execution failed"), duration_s=self.duration_s)
        return ToolResult(True, (self.stdout or "Done").strip(), duration_s=self.duration_s)


class ExecutionManager:
    def __init__(self, config: JarvisConfig, permissions: Any | None = None) -> None:
        self.config = config
        self.permissions = permissions
        self.logger = get_logger("executor")

    def execute(self, code: str, timeout: float = 20.0) -> ExecutionResult:
        action = "execute_python"
        if self.permissions is not None and _HAS_PERMS:
            allowed = self.permissions.confirm(action, details="Execute generated Python code in an isolated process.")
            if not allowed:
                return ExecutionResult(approved=False)
        return self._run_subprocess(code, timeout)

    def _run_subprocess(self, code: str, timeout: float) -> ExecutionResult:
        t0 = time.time()
        script = textwrap.dedent(code).strip()
        if not script:
            return ExecutionResult(duration_s=time.time() - t0)

        tmp: tempfile.NamedTemporaryFile | None = None
        try:
            tmp = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
            tmp.write(script)
            tmp.close()
            path = Path(tmp.name)

            completed = subprocess.run(
                [sys.executable, str(path)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.config.project_root),
                shell=False,
            )
            return ExecutionResult(
                success=completed.returncode == 0,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
                duration_s=time.time() - t0,
                exit_code=completed.returncode,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(success=False, stderr=f"Timed out after {timeout}s", duration_s=time.time() - t0)
        except Exception as exc:
            return ExecutionResult(success=False, stderr=f"Execution error: {exc}", duration_s=time.time() - t0)
        finally:
            if tmp is not None:
                try:
                    Path(tmp.name).unlink(missing_ok=True)
                except Exception:
                    pass
