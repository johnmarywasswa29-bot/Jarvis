"""Tests for planner improvements."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1] / "Desktop" / "jarvis"
if not REPO.exists():
    REPO = Path.cwd()
os.chdir(REPO)
sys.path.insert(0, str(REPO))


class FakeLLM:
    def chat(self, prompt):
        return f"plan:{prompt[:20]}"


class TestPlannerV2(unittest.TestCase):
    def test_improve_plan_returns_string(self):
        from modules.planner_v2 import improve_plan
        plan = improve_plan("do homework", ["web_search"], "ctx", "goals")
        self.assertIsInstance(plan, str)
        self.assertTrue(len(plan) > 0)

    def test_improve_plan_uses_llm(self):
        from modules.planner_v2 import improve_plan
        plan = improve_plan("do homework", ["web_search"], "ctx", "", llm_builder=FakeLLM())
        self.assertIn("plan:", plan)

    def test_legacy_planner_node_no_llm(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("planner", REPO / "agent" / "planner.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        class DummyMemory:
            def get_recent_context(self):
                return ""

        state = {"transcript": "List files", "selected_tools": ["filesystem"]}
        out = mod.planner_node(state, config=None, memory=DummyMemory())
        self.assertIn("plan", out)
        self.assertIn("goal_context", out)

    def test_planner_node_with_goals(self):
        import importlib.util
        from modules.goals import GoalManager

        tmpdir = Path(tempfile.gettempdir()) / "jarvis-planner-goals-tests"
        tmpdir.mkdir(parents=True, exist_ok=True)
        persist = tmpdir / "goals.json"
        try:
            gm = GoalManager(persist_path=persist)
            gm.create("alpha", steps=["s1"])

            spec = importlib.util.spec_from_file_location("planner", REPO / "agent" / "planner.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            class DummyMemory:
                def get_recent_context(self):
                    return ""

            state = {"transcript": "focus on alpha", "selected_tools": [], "goals": gm}
            out = mod.planner_node(state, config=None, memory=DummyMemory(), goals=gm)
            self.assertIn("plan", out)
            self.assertIn("alpha", out.get("goal_context", ""))
        finally:
            try:
                persist.unlink(missing_ok=True)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
