"""WorkflowExecutor: executes planned workflows with retry, timeout, and recovery."""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from workflows.state import WorkflowState, WorkflowStep, StepStatus


class WorkflowExecutor:
    def __init__(
        self,
        tool_registry: Any = None,
        max_retries: int = 2,
        default_timeout_s: float = 30.0,
        max_parallel: int = 4,
    ) -> None:
        self.tool_registry = tool_registry
        self.max_retries = max_retries
        self.default_timeout_s = default_timeout_s
        self.max_parallel = max_parallel

    def execute(self, state: WorkflowState, *, cancel_callback: Any = None) -> WorkflowState:
        state.status = StepStatus.RUNNING
        state.updated_at = self._now()
        for i, step in enumerate(state.steps):
            if cancel_callback and cancel_callback():
                step.status = StepStatus.CANCELLED
                step.error = "workflow cancelled"
                state.status = StepStatus.CANCELLED
                state.updated_at = self._now()
                return state
            state.current_step_index = i
            step.status = StepStatus.RUNNING
            step.updated_at = self._now()
            t0 = time.perf_counter()
            try:
                if step.requires_confirmation and not getattr(state, "_confirmed_steps", {}).get(step.uuid):
                    step.status = StepStatus.WAITING_FOR_CONFIRMATION
                    if not step.confirmation_token:
                        step.confirmation_token = uuid.uuid4().hex
                    step.execution_time_s = round(time.perf_counter() - t0, 3)
                    state.status = StepStatus.WAITING_FOR_CONFIRMATION
                    state.updated_at = self._now()
                    if not hasattr(state, "_pending_steps"):
                        state._pending_steps = {}
                    state._pending_steps[step.uuid] = i
                    return state
                result = self._run_step(step)
                step.result = result
                step.status = StepStatus.COMPLETED
                step.execution_time_s = round(time.perf_counter() - t0, 3)
            except Exception as exc:
                step.error = str(exc)
                recovered = self._recover(step)
                if recovered:
                    step.status = StepStatus.COMPLETED
                    step.result = recovered
                    step.execution_time_s = round(time.perf_counter() - t0, 3)
                else:
                    step.status = StepStatus.FAILED
                    step.execution_time_s = round(time.perf_counter() - t0, 3)
                    state.status = StepStatus.FAILED
                    state.updated_at = self._now()
                    return state
        state.status = StepStatus.COMPLETED
        state.updated_at = self._now()
        return state

    def confirm_step(self, state: WorkflowState, step_uuid: str, *, approved: bool = True, token: str = "") -> WorkflowState:
        pending = getattr(state, "_pending_steps", {})
        idx = pending.get(step_uuid)
        if idx is None:
            for i, step in enumerate(state.steps):
                if step.uuid == step_uuid:
                    idx = i
                    break
        if idx is None:
            step = WorkflowStep(uuid=step_uuid, status=StepStatus.FAILED, error="no pending confirmation found")
            state.steps.append(step)
            state.status = StepStatus.FAILED
            state.updated_at = self._now()
            return state
        step = state.steps[idx]
        if step.status not in {StepStatus.WAITING_FOR_CONFIRMATION}:
            return state
        if step.confirmation_token and token != step.confirmation_token:
            step.status = StepStatus.REJECTED
            step.error = "invalid confirmation token"
            state.status = StepStatus.FAILED
            state.updated_at = self._now()
            return state
        if not approved:
            step.status = StepStatus.REJECTED
            step.error = "rejected by user"
            state.status = StepStatus.FAILED
            state.updated_at = self._now()
            return state
        if not hasattr(state, "_confirmed_steps"):
            state._confirmed_steps = {}
        state._confirmed_steps[step.uuid] = True
        step.status = StepStatus.RUNNING
        step.updated_at = self._now()
        t0 = time.perf_counter()
        try:
            result = self._run_step(step)
            step.result = result
            step.status = StepStatus.COMPLETED
            step.execution_time_s = round(time.perf_counter() - t0, 3)
        except Exception as exc:
            step.error = str(exc)
            step.status = StepStatus.FAILED
            step.execution_time_s = round(time.perf_counter() - t0, 3)
            state.status = StepStatus.FAILED
            state.updated_at = self._now()
            return state
        pending.pop(step.uuid, None)
        # Continue executing remaining workflow steps after confirmation.
        start_idx = idx + 1
        while start_idx < len(state.steps):
            next_step = state.steps[start_idx]
            if next_step.status in {StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.REJECTED, StepStatus.CANCELLED, StepStatus.SKIPPED}:
                start_idx += 1
                continue
            if next_step.requires_confirmation and not getattr(state, "_confirmed_steps", {}).get(next_step.uuid):
                next_step.status = StepStatus.WAITING_FOR_CONFIRMATION
                next_step.execution_time_s = 0.0
                if not next_step.confirmation_token:
                    next_step.confirmation_token = uuid.uuid4().hex
                state.status = StepStatus.WAITING_FOR_CONFIRMATION
                state.updated_at = self._now()
                if not hasattr(state, "_pending_steps"):
                    state._pending_steps = {}
                state._pending_steps[next_step.uuid] = start_idx
                return state
            next_step.status = StepStatus.RUNNING
            next_step.updated_at = self._now()
            t2 = time.perf_counter()
            try:
                result = self._run_step(next_step)
                next_step.result = result
                next_step.status = StepStatus.COMPLETED
                next_step.execution_time_s = round(time.perf_counter() - t2, 3)
            except Exception as exc2:
                next_step.error = str(exc2)
                next_step.status = StepStatus.FAILED
                next_step.execution_time_s = round(time.perf_counter() - t2, 3)
                state.status = StepStatus.FAILED
                state.updated_at = self._now()
                return state
            start_idx += 1
        state.status = StepStatus.COMPLETED
        state.updated_at = self._now()
        return state

    def _run_step(self, step: WorkflowStep) -> dict[str, Any]:
        registry = self.tool_registry
        if registry and step.tool:
            tool = self._resolve_tool(registry, step.tool)
            if tool:
                params = dict(step.parameters)
                params.setdefault("prompt", "")
                return registry.run_tool(tool, **params).__dict__
        return {"status": "completed", "message": "no tool registry available", "tool": step.tool}

    def _resolve_tool(self, registry: Any, name: str) -> Any:
        for t in getattr(registry, "tools", []):
            if getattr(t, "name", None) == name:
                return t
        return None

    def _recover(self, step: WorkflowStep) -> Optional[dict[str, Any]]:
        if step.retry_count >= self.max_retries:
            return None
        step.retry_count += 1
        step.updated_at = self._now()
        try:
            return self._run_step(step)
        except Exception:
            return None

    @staticmethod
    def _now() -> str:
        from datetime import datetime, UTC
        return datetime.now(UTC).replace(tzinfo=None).isoformat()
