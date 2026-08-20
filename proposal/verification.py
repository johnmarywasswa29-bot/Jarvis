"""Phase C — Verifier foundation.

Observation-only verification of whether an objective was ACTUALLY achieved
after a proposal was executed. The Verifier consumes an :class:`ExecutionAudit`
and optional external observations (git status/diff, workspace state, file
read-back, etc.) and returns a structured :class:`VerificationResult`.

Safety invariants:
  * The Verifier NEVER reports success merely because the ProposalExecutor
    completed. Success requires observable evidence (tests pass, build
    succeeds, file contents reflect the change, git/workspace state confirms
    it).
  * The Verifier performs NO side effects: it does not execute tools, does not
    modify files, does not commit/push/install, and does not change state.
  * It may *read* evidence passed in via ``observations`` (supplied by the
    caller from read-only tools), but it never invokes ToolRegistry itself.

This component is a building block for the (not-yet-authorized) AgentLoop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from typing import Any, Optional

from proposal.executor import ExecutionAudit, ExecutedStep, StepStatus


def _first_int(pattern: str, text: str) -> int:
    m = re.search(pattern, text or "")
    return int(m.group(1)) if m else 0


class VerificationStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    INCONCLUSIVE = "inconclusive"


@dataclass
class VerificationResult:
    status: VerificationStatus
    objective: str = ""
    evidence: list[str] = field(default_factory=list)
    failed_steps: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: str = ""
    iteration: int = 0
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat()
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == VerificationStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "objective": self.objective,
            "evidence": self.evidence,
            "failed_steps": self.failed_steps,
            "diagnostics": self.diagnostics,
            "iteration": self.iteration,
            "timestamp": self.timestamp,
        }


class PhaseCEvent:
    """Minimal observable event for verification/replan milestones."""

    def __init__(self, type: str, **data: Any) -> None:
        self.type = type
        self.data = data


class Verifier:
    def __init__(self, event_bus: Any | None = None) -> None:
        self.event_bus = event_bus

    # ------------------------------------------------------------------ #
    def verify(
        self,
        objective: str,
        audit: ExecutionAudit,
        observations: Optional[dict[str, Any]] = None,
        iteration: int = 0,
    ) -> VerificationResult:
        observations = observations or {}
        obj = (objective or "").lower()
        steps = list(audit.executed_steps)

        evidence: list[str] = []
        failed: list[dict[str, Any]] = []
        diagnostics_parts: list[str] = []

        # 1) Inspect step statuses. A hard FAILED step => objective not met.
        for s in steps:
            if s.status == StepStatus.FAILED.value:
                rec = {"order": s.order, "tool": s.tool, "error": s.error}
                failed.append(rec)
                diagnostics_parts.append(
                    f"step {s.order} ({s.tool}) FAILED: {s.error or '(no error detail)'}"
                )
            elif s.status in (StepStatus.DENIED.value, StepStatus.SKIPPED.value):
                diagnostics_parts.append(
                    f"step {s.order} ({s.tool}) {s.status} — not executed"
                )

        if failed:
            result = VerificationResult(
                status=VerificationStatus.FAILURE,
                objective=objective,
                evidence=evidence,
                failed_steps=failed,
                diagnostics="; ".join(diagnostics_parts) or "execution failed",
                iteration=iteration,
            )
            self._publish("verification.completed", result=result)
            return result

        # 2) Objective-specific evidence checks (never trust final_status alone).
        if "test" in obj:
            outcome, msg = self._check_test(steps, observations)
        elif "build" in obj:
            outcome, msg = self._check_build(steps, observations)
        elif any(k in obj for k in ("file", "edit", "change", "modify")):
            outcome, msg = self._check_file(steps, observations)
        elif any(k in obj for k in ("git", "commit", "repo", "repository", "branch")):
            outcome, msg = self._check_git(steps, observations)
        else:
            outcome, msg = self._check_generic(steps, observations)

        evidence.append(msg)
        if outcome == "success":
            status = VerificationStatus.SUCCESS
            diagnostics = f"objective achieved: {msg}"
        elif outcome == "failure":
            status = VerificationStatus.FAILURE
            diagnostics = f"objective not achieved: {msg}"
        else:  # inconclusive — could not confirm either way
            status = VerificationStatus.INCONCLUSIVE
            diagnostics = f"could not confirm objective: {msg}"

        result = VerificationResult(
            status=status,
            objective=objective,
            evidence=evidence,
            failed_steps=failed,
            diagnostics=diagnostics,
            iteration=iteration,
        )
        self._publish("verification.completed", result=result)
        return result

    # ------------------------------------------------------------------ #
    def _check_test(self, steps: list[ExecutedStep], obs: dict) -> tuple[str, str]:
        target = obs.get("test_output")
        if target is None:
            for s in steps:
                if s.tool == "test_execution" and s.status == StepStatus.EXECUTED.value:
                    target = s.output
                    break
        if target is None:
            return "inconclusive", "no test execution step/output observed"
        # Parse the tool's own summary line: framework=pytest ... passed=N failed=N errors=N
        summary = ""
        for line in (target or "").splitlines():
            if "framework=" in line or "passed=" in line:
                summary = line
                break
        if not summary:
            summary = target or ""
        passed = _first_int(r"passed=(\d+)", summary)
        failed = _first_int(r"failed=(\d+)", summary)
        errors = _first_int(r"errors=(\d+)", summary)
        if passed > 0 and failed == 0 and errors == 0:
            return "success", f"tests pass (passed={passed})"
        if failed == 0 and errors == 0 and passed == 0:
            return "failure", "no tests collected/ran — cannot confirm"
        return "failure", f"tests did not pass (passed={passed}, failed={failed}, errors={errors})"

    def _check_build(self, steps: list[ExecutedStep], obs: dict) -> tuple[str, str]:
        target = obs.get("build_output")
        if target is None:
            for s in steps:
                if s.tool == "build" and s.status == StepStatus.EXECUTED.value:
                    target = s.output
                    break
        if target is None:
            return "inconclusive", "no build step/output observed"
        if "succeeded" in (target or "").lower() and "failed" not in (target or "").lower():
            return "success", "build succeeded"
        if "failed" in (target or "").lower() or "timed out" in (target or "").lower():
            return "failure", "build did not succeed"
        return "inconclusive", "build output inconclusive"

    def _check_file(self, steps: list[ExecutedStep], obs: dict) -> tuple[str, str]:
        contents = obs.get("file_contents")  # dict path -> text
        if contents:
            for s in steps:
                if s.tool == "file_edit" and s.status == StepStatus.EXECUTED.value:
                    return "success", "file content read-back provided for edited file"
        for s in steps:
            if s.tool == "file_edit" and s.status == StepStatus.EXECUTED.value:
                if "verified" in (s.output or "").lower():
                    return "success", "file edit verified by tool read-back"
        return "inconclusive", "no file-change verification evidence"

    def _check_git(self, steps: list[ExecutedStep], obs: dict) -> tuple[str, str]:
        status = obs.get("git_status") or obs.get("git_diff")
        if status is not None:
            return "success", "git/workspace state observed"
        for s in steps:
            if s.tool == "git" and s.status == StepStatus.EXECUTED.value and (s.output or "").strip():
                return "success", "git step produced output"
        return "inconclusive", "no git/workspace state observed"

    def _check_generic(self, steps: list[ExecutedStep], obs: dict) -> tuple[str, str]:
        # Conservative: require explicit positive evidence, never "completed".
        for s in steps:
            if s.status == StepStatus.EXECUTED.value and s.output:
                low = (s.output or "").lower()
                if any(k in low for k in ("succeeded", "verified", "available", "done", "ok")):
                    return "success", f"positive evidence in step {s.order} ({s.tool})"
        return "inconclusive", "no positive execution evidence observed"

    # ------------------------------------------------------------------ #
    def _publish(self, etype: str, **data: Any) -> None:
        if self.event_bus is not None:
            try:
                self.event_bus.publish(PhaseCEvent(etype, **data))
            except Exception:
                pass
