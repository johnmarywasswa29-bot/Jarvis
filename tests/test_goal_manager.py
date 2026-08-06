"""Tests for the Goal Manager package."""
from __future__ import annotations

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


class TestGoal(unittest.TestCase):
    def test_defaults(self):
        from goal_manager.goal import Goal, GoalPriority, GoalStatus
        g = Goal("title")
        self.assertEqual(g.title, "title")
        self.assertEqual(g.status, GoalStatus.ACTIVE)
        self.assertEqual(g.priority, GoalPriority.NORMAL)
        self.assertEqual(g.progress, 0.0)
        self.assertEqual(g.tags, [])

    def test_progress_clamp(self):
        from goal_manager.goal import Goal
        g = Goal("x", progress=150)
        self.assertEqual(g.progress, 100.0)
        g2 = Goal("y", progress=-10)
        self.assertEqual(g2.progress, 0.0)

    def test_touch_updates_updated_at(self):
        from goal_manager.goal import Goal
        g = Goal("x", created_at="2020-01-01T00:00:00", updated_at="2020-01-01T00:00:00")
        g.touch()
        self.assertTrue(g.updated_at >= g.created_at)

    def test_attach_plan(self):
        from goal_manager.goal import Goal, PlanAttachment
        g = Goal("x")
        g.attach_plan(PlanAttachment(plan_id="p1", plan_dict={"steps": []}))
        self.assertEqual(len(g.plans), 1)
        self.assertEqual(g.plans[0].plan_id, "p1")


class TestGoalStorage(unittest.TestCase):
    def _new_store(self, prefix: str = "goal-storage"):
        path = Path(tempfile.gettempdir()) / f"{prefix}-{id(self)}.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        from goal_manager.goal_storage import GoalStorage
        return GoalStorage(db_path=path), path

    def test_crud_roundtrip(self):
        from goal_manager.goal import Goal, GoalStatus
        store, path = self._new_store("crud")
        try:
            g = Goal("ship", status=GoalStatus.ACTIVE, priority="high", tags=["a", "b"])
            store.upsert(g)
            loaded = store.load(g.id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.title, "ship")
            self.assertEqual(list(loaded.tags), ["a", "b"])
            self.assertTrue(store.exists(g.id))
            self.assertFalse(store.exists("missing"))
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_delete(self):
        from goal_manager.goal import Goal
        store, path = self._new_store("delete")
        try:
            g = Goal("del")
            store.upsert(g)
            self.assertTrue(store.delete(g.id))
            self.assertIsNone(store.load(g.id))
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_load_active_completed(self):
        from goal_manager.goal import Goal, GoalStatus
        store, path = self._new_store("lists")
        try:
            a = Goal("a", status=GoalStatus.ACTIVE)
            c = Goal("c", status=GoalStatus.COMPLETED)
            store.upsert(a)
            store.upsert(c)
            act = store.load_active()
            comp = store.load_completed()
            self.assertEqual([x.id for x in act], [a.id])
            self.assertEqual([x.id for x in comp], [c.id])
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_search(self):
        from goal_manager.goal import Goal
        store, path = self._new_store("search")
        try:
            store.upsert(Goal("alpha", category="proj"))
            store.upsert(Goal("beta", notes="second"))
            results = list(store.search("alpha"))
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].title, "alpha")
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_restart_persistence(self):
        from goal_manager.goal import Goal
        path = Path(tempfile.gettempdir()) / f"restart-{id(self)}.sqlite"
        try:
            from goal_manager.goal_storage import GoalStorage
            store = GoalStorage(db_path=path)
            store.upsert(Goal("persist"))
            store2 = GoalStorage(db_path=path)
            active = store2.load_active()
            self.assertEqual(len(active), 1)
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


class TestGoalManager(unittest.TestCase):
    def _new_manager(self, prefix: str = "gm"):
        path = Path(tempfile.gettempdir()) / f"{prefix}-{id(self)}.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        from goal_manager.goal_storage import GoalStorage
        from goal_manager.goal_manager import GoalManager
        return GoalManager(storage=GoalStorage(db_path=path)), path

    def test_create_update_complete(self):
        manager, path = self._new_manager("create")
        try:
            g = manager.create("build", "desc", priority="high")
            g2 = manager.update_progress(g.id, 8, 10)
            self.assertIsNotNone(g2)
            self.assertEqual(g2.progress, 80.0)
            g3 = manager.complete(g.id)
            self.assertEqual(g3.status.value, "completed")
            self.assertEqual(g3.progress, 100.0)
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_attach_plan(self):
        manager, path = self._new_manager("attach")
        try:
            g = manager.create("build")
            plan = {"steps": [{"id": "1", "description": "code"}, {"id": "2", "description": "test"}]}
            g2 = manager.attach_plan(g.id, plan)
            self.assertEqual(len(g2.plans), 1)
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_search_isolation(self):
        manager, path = self._new_manager("search")
        try:
            manager.create("alpha", category="proj")
            results = manager.search("alpha")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].category, "proj")
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_archive_delete(self):
        manager, path = self._new_manager("archive")
        try:
            g = manager.create("tmp")
            self.assertTrue(manager.archive(g.id))
            loaded = manager.get(g.id)
            self.assertEqual(loaded.status.value, "archived")
            self.assertTrue(manager.delete(g.id))
            self.assertIsNone(manager.get(g.id))
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


class TestGoalEvents(unittest.TestCase):
    def _new_manager(self, prefix: str = "events"):
        path = Path(tempfile.gettempdir()) / f"{prefix}-{id(self)}.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        from goal_manager.goal_storage import GoalStorage
        from goal_manager.goal_manager import GoalManager
        return GoalManager(storage=GoalStorage(db_path=path)), path

    def test_publish_subscribe(self):
        from goal_manager.goal_events import GoalEvent, GoalEventBus, GoalEventType
        bus = GoalEventBus()
        events = []
        bus.subscribe(lambda e: events.append(e))
        bus.publish(GoalEvent(GoalEventType.CREATED, "1"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, GoalEventType.CREATED)
        self.assertEqual(events[0].goal_id, "1")


if __name__ == "__main__":
    unittest.main()
