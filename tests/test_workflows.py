"""Phase 4 tests: WorkflowManager, planner, executor, history, memory bridge."""
from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from workflows.state import WorkflowState, WorkflowStep, StepStatus
from workflows.history import WorkflowHistory
from workflows.memory_bridge import WorkflowMemoryBridge
from workflows.planner import WorkflowPlanner
from workflows.executor import WorkflowExecutor
from workflows.manager import WorkflowManager
from modules.tools import ToolRegistry
from modules.config import JarvisConfig


def _clean_dir(path: Path) -> None:
    import shutil
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


TMP_DIR = REPO / "tests" / "tmp_workflows"


class FakeTool:
    def __init__(self, name, response=None, fail=False):
        self.name = name
        self.enabled = True
        self._response = response
        self._fail = fail

    def can_handle(self, prompt: str) -> bool:
        return True

    def run(self, **kwargs):
        if self._fail:
            raise RuntimeError("boom")
        return self._response or {"status": "ok", "tool": self.name}


class FakeIntent:
    def analyze(self, text):
        return SimpleNamespace(intent="test.intent")


class TestWorkflowPlanner(unittest.TestCase):
    def test_plan_returns_state(self):
        planner = WorkflowPlanner(tool_registry=None, intent_analyzer=None)
        state = planner.plan("do something")
        self.assertIsInstance(state, WorkflowState)
        self.assertEqual(state.name, "do something")

    def test_plan_selects_tools(self):
        registry = SimpleNamespace(select_tools=lambda p: [FakeTool("web_search")])
        planner = WorkflowPlanner(tool_registry=registry, intent_analyzer=None)
        state = planner.plan("search web")
        self.assertTrue(len(state.steps) >= 1)

    def test_plan_sets_intent(self):
        planner = WorkflowPlanner(tool_registry=None, intent_analyzer=FakeIntent())
        state = planner.plan("open file")
        self.assertEqual(state.steps[0].intent, "test.intent")


class TestWorkflowExecutor(unittest.TestCase):
    def _ok_registry(self, tool_name):
        class OkTool:
            name = tool_name; enabled = True
            def run(self, **kw):
                return {"ok": True}
        def run_tool(t, **kw):
            r = t.run(**kw)
            class R: pass
            obj = R(); obj.__dict__ = r
            return obj
        return SimpleNamespace(tools=[OkTool()], run_tool=run_tool)

    def test_execute_success(self):
        executor = WorkflowExecutor(tool_registry=self._ok_registry("code_execution"))
        state = WorkflowState(name="test", steps=[WorkflowStep(tool="code_execution", parameters={"prompt": "hi"})])
        result = executor.execute(state)
        self.assertEqual(result.status, StepStatus.COMPLETED)

    def test_execute_retry_then_success(self):
        class FlakyTool:
            name = "tool"; enabled = True
            def __init__(self):
                self.calls = 0
            def run(self, **kwargs):
                self.calls += 1
                if self.calls < 2:
                    raise RuntimeError("transient")
                return {"ok": True}
        tool = FlakyTool()
        def run_tool(t, **kw):
            r = t.run(**kw)
            class R: pass
            obj = R(); obj.__dict__ = r
            return obj
        registry = SimpleNamespace(tools=[tool], run_tool=run_tool)
        executor = WorkflowExecutor(tool_registry=registry, max_retries=2)
        step = WorkflowStep(tool="tool", parameters={"prompt": "hi"})
        state = WorkflowState(name="test", steps=[step])
        result = executor.execute(state)
        self.assertEqual(result.status, StepStatus.COMPLETED)
        self.assertEqual(step.retry_count, 1)

    def test_execute_failure_after_retries(self):
        class BoomTool:
            name = "tool"; enabled = True
            def run(self, **kw):
                raise RuntimeError("boom")
        def run_tool(t, **kw):
            r = t.run(**kw)
            class R: pass
            obj = R(); obj.__dict__ = r
            return obj
        registry = SimpleNamespace(tools=[BoomTool()], run_tool=run_tool)
        executor = WorkflowExecutor(tool_registry=registry, max_retries=1)
        step = WorkflowStep(tool="tool", parameters={"prompt": "hi"})
        state = WorkflowState(name="test", steps=[step])
        result = executor.execute(state)
        self.assertEqual(result.status, StepStatus.FAILED)

    def test_execute_cancel(self):
        cancel_calls = {"count": 0}
        def cancel():
            cancel_calls["count"] += 1
            return cancel_calls["count"] >= 1
        executor = WorkflowExecutor(tool_registry=self._ok_registry("tool"))
        state = WorkflowState(name="test", steps=[WorkflowStep(tool="tool")])
        result = executor.execute(state, cancel_callback=cancel)
        self.assertEqual(result.status, StepStatus.CANCELLED)


class TestWorkflowHistory(unittest.TestCase):
    def setUp(self):
        _clean_dir(TMP_DIR)
        self.history = WorkflowHistory(TMP_DIR / "workflows.sqlite")

    def tearDown(self):
        self.history.close()

    def test_save_and_load(self):
        state = WorkflowState(name="demo", description="demo workflow")
        state.steps.append(WorkflowStep(description="step1", tool="tool"))
        self.history.save_workflow(state)
        loaded = self.history.load_workflow(state.workflow_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "demo")
        self.assertEqual(len(loaded.steps), 1)

    def test_list_workflows(self):
        state = WorkflowState(name="a")
        self.history.save_workflow(state)
        rows = self.history.list_workflows()
        self.assertTrue(len(rows) >= 1)

    def test_delete(self):
        state = WorkflowState(name="x")
        self.history.save_workflow(state)
        self.history.delete_workflow(state.workflow_id)
        self.assertIsNone(self.history.load_workflow(state.workflow_id))

    def test_logs(self):
        state = WorkflowState(name="l")
        self.history.save_workflow(state)
        self.history.log(state.workflow_id, "info", "hello")
        logs = self.history.recent_logs(state.workflow_id)
        self.assertTrue(len(logs) >= 1)


class TestWorkflowMemoryBridge(unittest.TestCase):
    def test_enhance_step_with_intent(self):
        bridge = WorkflowMemoryBridge(intent_analyzer=FakeIntent())
        step = WorkflowStep(description="search web")
        step = bridge.enhance_step(step)
        self.assertEqual(step.intent, "test.intent")

    def test_enrich_plan_with_habits(self):
        habits = SimpleNamespace(suggest_habits=lambda context=None, threshold=0.5: [{"habit": SimpleNamespace(name="open browser"), "score": 0.9}])
        bridge = WorkflowMemoryBridge(habits=habits)
        state = WorkflowState(name="browse", steps=[WorkflowStep(description="open browser")])
        state = bridge.enrich_plan(state)
        self.assertIn("habit_suggestions", state.context)


class TestWorkflowManager(unittest.TestCase):
    def setUp(self):
        _clean_dir(TMP_DIR)
        self.mgr = WorkflowManager(history=WorkflowHistory(TMP_DIR / "workflows.sqlite"))

    def tearDown(self):
        self.mgr.close()

    def test_create_and_get(self):
        state = self.mgr.create("demo")
        self.assertIsNotNone(state.workflow_id)
        loaded = self.mgr.get(state.workflow_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "demo")

    def test_run_success(self):
        registry = SimpleNamespace(tools=[FakeTool("tool", response={"ok": True})], run_tool=lambda t, prompt="", **kw: type("R", (), {"__dict__": lambda self: {"ok": True}})())
        planner = WorkflowPlanner(tool_registry=registry, intent_analyzer=None)
        executor = WorkflowExecutor(tool_registry=registry)
        mgr = WorkflowManager(history=WorkflowHistory(TMP_DIR / "wf2.sqlite"), planner=planner, executor=executor)
        state = mgr.create("do tool")
        result = mgr.run(state)
        self.assertEqual(result.status, StepStatus.COMPLETED)
        mgr.close()

    def test_cancel(self):
        state = WorkflowState(name="c", steps=[WorkflowStep(tool="tool", status=StepStatus.RUNNING)])
        self.mgr.history.save_workflow(state)
        result = self.mgr.cancel(state.workflow_id)
        self.assertEqual(result.status, StepStatus.CANCELLED)


if __name__ == "__main__":
    unittest.main()
