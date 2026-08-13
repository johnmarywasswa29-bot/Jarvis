"""Phase A tests: mandatory confirmation enforcement in WorkflowExecutor."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from workflows.state import WorkflowState, WorkflowStep, StepStatus
from workflows.executor import WorkflowExecutor


class ConfirmationTool:
    name = "demo"
    enabled = True

    def run(self, **kwargs):
        return {"ran": True, "args": kwargs}


class TestConfirmationEnforcement(unittest.TestCase):
    def setUp(self):
        self.registry = SimpleNamespace(
            tools=[ConfirmationTool()],
            run_tool=lambda t, **kw: type("R", (), {"__dict__": t.run(**kw)})(),
        )

    def test_step_blocks_without_confirmation(self):
        executor = WorkflowExecutor(tool_registry=self.registry)
        step = WorkflowStep(tool="demo", parameters={"prompt": "hi"}, requires_confirmation=True)
        state = WorkflowState(name="c", steps=[step])
        result = executor.execute(state)
        self.assertEqual(result.status, StepStatus.WAITING_FOR_CONFIRMATION)
        self.assertEqual(step.status, StepStatus.WAITING_FOR_CONFIRMATION)
        self.assertNotEqual(step.confirmation_token, "")

    def test_confirmed_step_executes(self):
        executor = WorkflowExecutor(tool_registry=self.registry)
        step = WorkflowStep(tool="demo", parameters={"prompt": "hi"}, requires_confirmation=True)
        state = WorkflowState(name="c", steps=[step])
        executor.execute(state)
        result = executor.confirm_step(state, step.uuid, approved=True, token=step.confirmation_token)
        self.assertEqual(result.status, StepStatus.COMPLETED)
        self.assertEqual(step.status, StepStatus.COMPLETED)
        self.assertIsNotNone(step.result)

    def test_rejected_step_does_not_execute(self):
        executor = WorkflowExecutor(tool_registry=self.registry)
        step = WorkflowStep(tool="demo", parameters={"prompt": "hi"}, requires_confirmation=True)
        state = WorkflowState(name="c", steps=[step])
        executor.execute(state)
        result = executor.confirm_step(state, step.uuid, approved=False, token=step.confirmation_token)
        self.assertEqual(result.status, StepStatus.FAILED)
        self.assertEqual(step.status, StepStatus.REJECTED)
        self.assertIn("rejected", step.error)

    def test_confirm_with_wrong_token_fails(self):
        executor = WorkflowExecutor(tool_registry=self.registry)
        step = WorkflowStep(tool="demo", parameters={"prompt": "hi"}, requires_confirmation=True)
        state = WorkflowState(name="c", steps=[step])
        executor.execute(state)
        result = executor.confirm_step(state, step.uuid, approved=True, token="bad-token")
        self.assertEqual(result.status, StepStatus.FAILED)
        self.assertEqual(step.status, StepStatus.REJECTED)
        self.assertIn("token", step.error)

    def test_non_confirmed_step_runs_immediately(self):
        executor = WorkflowExecutor(tool_registry=self.registry)
        step = WorkflowStep(tool="demo", parameters={"prompt": "hi"})
        state = WorkflowState(name="c", steps=[step])
        result = executor.execute(state)
        self.assertEqual(result.status, StepStatus.COMPLETED)

    def test_mixed_plan_confirmation_flow(self):
        executor = WorkflowExecutor(tool_registry=self.registry)
        safe = WorkflowStep(description="safe", tool="demo", parameters={"prompt": "1"}, requires_confirmation=False)
        guarded = WorkflowStep(description="guarded", tool="demo", parameters={"prompt": "2"}, requires_confirmation=True)
        safe2 = WorkflowStep(description="safe2", tool="demo", parameters={"prompt": "3"}, requires_confirmation=False)
        state = WorkflowState(name="mixed", steps=[safe, guarded, safe2])
        result = executor.execute(state)
        self.assertEqual(result.status, StepStatus.WAITING_FOR_CONFIRMATION)
        self.assertEqual(safe.status, StepStatus.COMPLETED)
        self.assertEqual(guarded.status, StepStatus.WAITING_FOR_CONFIRMATION)
        self.assertEqual(safe2.status, StepStatus.PENDING)
        result2 = executor.confirm_step(state, guarded.uuid, approved=True, token=guarded.confirmation_token)
        self.assertEqual(result2.status, StepStatus.COMPLETED)
        self.assertEqual(safe2.status, StepStatus.COMPLETED)

    def test_confirmation_does_not_run_twice(self):
        executor = WorkflowExecutor(tool_registry=self.registry)
        step = WorkflowStep(tool="demo", parameters={"prompt": "hi"}, requires_confirmation=True)
        state = WorkflowState(name="c", steps=[step])
        executor.execute(state)
        executor.confirm_step(state, step.uuid, approved=True, token=step.confirmation_token)
        second = executor.confirm_step(state, step.uuid, approved=False, token=step.confirmation_token)
        self.assertEqual(step.status, StepStatus.COMPLETED)
        self.assertEqual(second.status, StepStatus.COMPLETED)

    def test_cancel_before_confirmation(self):
        calls = {"count": 0}

        def cancel():
            calls["count"] += 1
            return True

        executor = WorkflowExecutor(tool_registry=self.registry)
        step = WorkflowStep(tool="demo", parameters={"prompt": "hi"}, requires_confirmation=True)
        state = WorkflowState(name="c", steps=[step])
        result = executor.execute(state, cancel_callback=cancel)
        self.assertEqual(result.status, StepStatus.CANCELLED)
        self.assertEqual(step.status, StepStatus.CANCELLED)


if __name__ == "__main__":
    unittest.main()
