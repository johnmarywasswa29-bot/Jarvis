"""Tests for Goal Manager."""
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


class DummyGoalManager:
    pass


class TestGoalManager(unittest.TestCase):
    def setUp(self):
        import modules.goals as goalsmod
        self.tmpdir = Path(tempfile.gettempdir()) / "jarvis-goals-tests"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.persist = self.tmpdir / "goals.json"
        self.gm = goalsmod.GoalManager(persist_path=self.persist)
        self.maxDiff = 2000

    def tearDown(self):
        try:
            self.persist.unlink(missing_ok=True)
        except Exception:
            pass

    def test_create_and_get(self):
        g = self.gm.create("test", steps=["a", "b"], priority=2)
        self.assertTrue(g.id)
        self.assertEqual(g.title, "test")
        self.assertEqual(g.steps, ["a", "b"])
        self.assertEqual(g.priority, 2)
        loaded = self.gm.get(g.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.title, "test")

    def test_status_transitions(self):
        g = self.gm.create("t")
        self.gm.mark_active(g.id)
        self.assertEqual(self.gm.get(g.id).status, self.gm.get(g.id).status.__class__.ACTIVE)
        self.gm.complete_step(g.id, "x")
        self.assertEqual(self.gm.get(g.id).completed_steps, ["x"])

    def test_auto_done_when_steps_complete(self):
        g = self.gm.create("t", steps=["x"])
        self.gm.mark_active(g.id)
        self.gm.complete_step(g.id, "x")
        self.assertEqual(self.gm.get(g.id).status.value, "done")

    def test_persistence_across_instances(self):
        g = self.gm.create("persist")
        del self.gm
        from modules.goals import GoalManager
        gm2 = GoalManager(persist_path=self.persist)
        self.assertIsNotNone(gm2.get(g.id))

    def test_update_metadata(self):
        g = self.gm.create("t")
        updated = self.gm.update(g.id, metadata={"key": "value"})
        self.assertEqual(updated.metadata.get("key"), "value")

    def test_list_filters(self):
        g1 = self.gm.create("pending")
        g2 = self.gm.create("active")
        self.gm.mark_active(g2.id)
        actives = self.gm.list_active()
        pendings = self.gm.list_pending()
        self.assertTrue(any(x.id == g2.id for x in actives))
        self.assertTrue(any(x.id == g1.id for x in pendings))
        self.assertFalse(any(x.id == g2.id for x in pendings))

    def test_duplicate_id_safe(self):
        g1 = self.gm.create("a")
        g2 = self.gm.create("b", goal_id=g1.id)
        self.assertEqual(g1.id, g2.id)
        self.assertEqual(self.gm.get(g1.id).title, "b")

    def test_to_context(self):
        self.gm.create("ctx", steps=["s1", "s2"], priority=1)
        out = self.gm.to_context(max_goals=1)
        self.assertIn("ctx", out)


if __name__ == "__main__":
    unittest.main()
