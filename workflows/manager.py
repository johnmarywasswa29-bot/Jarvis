"""WorkflowManager: orchestrates planning, execution, persistence, and learning."""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

from workflows.state import WorkflowState, StepStatus
from workflows.history import WorkflowHistory
from workflows.planner import WorkflowPlanner
from workflows.executor import WorkflowExecutor
from workflows.memory_bridge import WorkflowMemoryBridge


class WorkflowManager:
    def __init__(
        self,
        history: Optional[WorkflowHistory] = None,
        planner: Optional[WorkflowPlanner] = None,
        executor: Optional[WorkflowExecutor] = None,
        memory_bridge: Optional[WorkflowMemoryBridge] = None,
    ) -> None:
        self.history = history or WorkflowHistory()
        self.planner = planner or WorkflowPlanner()
        self.executor = executor or WorkflowExecutor()
        self.memory_bridge = memory_bridge or WorkflowMemoryBridge()
        self._lock = threading.RLock()
        self._paused_ids: set[str] = set()

    def close(self) -> None:
        self.history.close()

    def __enter__(self) -> "WorkflowManager":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def create(self, goal: str, context: Optional[dict[str, Any]] = None) -> WorkflowState:
        with self._lock:
            state = self.planner.plan(goal, context)
            state.context.update(context or {})
            state = self.memory_bridge.enrich_plan(state)
            state.updated_at = self._now()
            for step in state.steps:
                self.memory_bridge.enhance_step(step)
            self.history.save_workflow(state)
            return state

    def run(self, state: WorkflowState) -> WorkflowState:
        with self._lock:
            result = self._execute_with_pause(state)
            self.history.save_workflow(result)
            if result.status == StepStatus.COMPLETED:
                self.memory_bridge.record_success(result)
            else:
                failed_step = next((s for s in result.steps if s.status == StepStatus.FAILED), None)
                if failed_step:
                    self.memory_bridge.record_failure(result, failed_step)
            return result

    def pause(self, workflow_id: str) -> None:
        with self._lock:
            self._paused_ids.add(workflow_id)

    def resume(self, workflow_id: str) -> None:
        with self._lock:
            self._paused_ids.discard(workflow_id)

    def _execute_with_pause(self, state: WorkflowState) -> WorkflowState:
        state.status = StepStatus.RUNNING
        state.updated_at = self._now()
        for i, step in enumerate(state.steps):
            if state.workflow_id in self._paused_ids:
                step.status = StepStatus.PAUSED
                state.status = StepStatus.PAUSED
                state.updated_at = self._now()
                self.history.save_workflow(state)
                return state
            state.current_step_index = i
            step.status = StepStatus.RUNNING
            step.updated_at = self._now()
            t0 = time.perf_counter()
            try:
                result = self.executor.execute(state)
                step.result = getattr(result, "result", None)
                if result.status == StepStatus.COMPLETED:
                    step.status = StepStatus.COMPLETED
                    step.execution_time_s = round(time.perf_counter() - t0, 3)
                elif result.status == StepStatus.FAILED:
                    step.status = StepStatus.FAILED
                    step.error = getattr(step, "error", "") or "step failed"
                    step.execution_time_s = round(time.perf_counter() - t0, 3)
                    state.status = StepStatus.FAILED
                    state.updated_at = self._now()
                    return state
                elif result.status == StepStatus.CANCELLED:
                    step.status = StepStatus.CANCELLED
                    state.status = StepStatus.CANCELLED
                    state.updated_at = self._now()
                    return state
                else:
                    step.status = StepStatus.COMPLETED
                    step.execution_time_s = round(time.perf_counter() - t0, 3)
            except Exception as exc:
                step.error = str(exc)
                recovered = self.executor._recover(step)
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

    def get(self, workflow_id: str) -> Optional[WorkflowState]:
        with self._lock:
            return self.history.load_workflow(workflow_id)

    def cancel(self, workflow_id: str) -> Optional[WorkflowState]:
        with self._lock:
            state = self.history.load_workflow(workflow_id)
            if not state:
                return None
            for step in state.steps:
                if step.status == StepStatus.RUNNING:
                    step.status = StepStatus.CANCELLED
            state.status = StepStatus.CANCELLED
            state.updated_at = self._now()
            self.history.save_workflow(state)
            return state

    def list_workflows(self) -> list[dict[str, Any]]:
        with self._lock:
            return self.history.list_workflows()

    @staticmethod
    def _now() -> str:
        from datetime import datetime, UTC
        return datetime.now(UTC).replace(tzinfo=None).isoformat()
