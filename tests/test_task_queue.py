"""Tests for the Task Queue package."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
os.chdir(REPO)

from task_queue.task import Task, TaskPriority, TaskStatus
from task_queue.task_events import TaskEventBus, TaskEvent, TaskEventType
from task_queue.task_serializer import TaskSerializer
from task_queue.task_storage import TaskStorage
from task_queue.task_scheduler import TaskScheduler
from task_queue.task_queue import TaskQueue, DependencyCycleError


class TestTaskModel(unittest.TestCase):
    def test_defaults(self):
        t = Task(title="t", priority=TaskPriority.HIGH)
        self.assertTrue(t.id)
        self.assertEqual(t.status, TaskStatus.PENDING)
        self.assertEqual(t.priority, TaskPriority.HIGH)
        self.assertEqual(t.retry_count, 0)
        self.assertEqual(t.max_retries, 3)

    def test_status_priority(self):
        self.assertEqual(TaskPriority.HIGH.value, "high")
        self.assertEqual(TaskStatus.READY.value, "ready")

    def test_attach_deps(self):
        t = Task("x", depends_on=["a"])
        self.assertEqual(t.depends_on, ["a"])

    def test_touch_updates_updated_at(self):
        t = Task("x")
        t.touch()
        self.assertTrue(getattr(t, 'updatedAt', getattr(t, 'updated_at', None)))


class TestEventBus(unittest.TestCase):
    def test_publish_subscribe(self):
        bus = TaskEventBus()
        events = []
        bus.subscribe(lambda e: events.append(e))
        bus.publish(TaskEvent(TaskEventType.CREATED, "1", {}))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, TaskEventType.CREATED)
        self.assertEqual(events[0].task_id, "1")

    def test_unsubscribe(self):
        bus = TaskEventBus()
        events = []
        handler = lambda e: events.append(e)
        bus.subscribe(handler)
        bus.unsubscribe(handler)
        bus.publish(TaskEvent(TaskEventType.CREATED, "9", {}))
        self.assertEqual(len(events), 0)


class TestTaskStorage(unittest.TestCase):
    def _fresh_db(self):
        db_path = Path(tempfile.gettempdir()) / (f"verify-tasks-{uuid.uuid4().hex}.sqlite")
        return TaskStorage(db_path=db_path), db_path

    def test_crud_roundtrip(self):
        store, path = self._fresh_db()
        try:
            t = Task("x", title="hello", status=TaskStatus.READY, depends_on=["a"])
            store.upsert(t)
            loaded = store.load(t.id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.title, "hello")
            self.assertEqual(loaded.status, TaskStatus.READY)
            self.assertEqual(loaded.depends_on, ["a"])
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_exists_delete(self):
        store, path = self._fresh_db()
        try:
            t = Task("x")
            store.upsert(t)
            self.assertTrue(store.exists(t.id))
            self.assertFalse(store.exists("missing"))
            self.assertTrue(store.delete(t.id))
            self.assertFalse(store.exists(t.id))
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_status_filter(self):
        store, path = self._fresh_db()
        try:
            a = Task("a", status=TaskStatus.READY)
            b = Task("b", status=TaskStatus.FAILED)
            c = Task("c", status=TaskStatus.READY)
            for t in [a, b, c]:
                store.upsert(t)
            ready = store.load_by_status(TaskStatus.READY)
            failed = store.load_by_status(TaskStatus.FAILED)
            self.assertEqual(len(ready), 2)
            self.assertEqual(len(failed), 1)
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_update_timestamp(self):
        store, path = self._fresh_db()
        try:
            t = Task("x")
            store.upsert(t)
            loaded = store.load(t.id)
            loaded.touch()
            self.assertTrue(getattr(loaded, 'updatedAt', getattr(loaded, 'updated_at', None)))
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


class TestTaskQueueLifecycle(unittest.TestCase):
    def _queue(self):
        db_path = Path(tempfile.gettempdir()) / (f"verify-queue-{uuid.uuid4().hex}.sqlite")
        return TaskQueue(storage=TaskStorage(db_path=db_path)), db_path

    def test_enqueue_dequeue_ready(self):
        q, path = self._queue()
        try:
            t = Task("x", title="hello", status=TaskStatus.PENDING, priority=TaskPriority.HIGH)
            q.enqueue(t)
            self.assertEqual(t.status, TaskStatus.READY)
            task = q.dequeue()
            self.assertIsNotNone(task)
            self.assertEqual(task.status, TaskStatus.RUNNING)
            self.assertEqual(task.id, "x")
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_complete_fail(self):
        q, path = self._queue()
        try:
            t = Task("x")
            q.enqueue(t)
            task = q.complete("x", result="ok")
            self.assertEqual(task.status, TaskStatus.COMPLETED)
            self.assertEqual(task.result, "ok")
            f = Task("y")
            q.enqueue(f)
            task_f = q.fail("y", error="boom")
            self.assertEqual(task_f.status, TaskStatus.FAILED)
            self.assertEqual(task_f.error, "boom")
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_pause_resume_cancel(self):
        q, path = self._queue()
        try:
            t = Task("x")
            q.enqueue(t)
            task = q.dequeue()
            paused = q.pause(task.id)
            self.assertEqual(paused.status, TaskStatus.WAITING)
            resumed = q.resume(task.id)
            self.assertEqual(resumed.status, TaskStatus.READY)
            cancelled = q.cancel(task.id)
            self.assertEqual(cancelled.status, TaskStatus.CANCELLED)
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_retry_basic(self):
        q, path = self._queue()
        try:
            t = Task("x", max_retries=1)
            q.enqueue(t)
            q.dequeue()
            q.fail("x", error="oops")
            retried = q.retry("x")
            self.assertEqual(retried.status, TaskStatus.READY)
            self.assertEqual(retried.retry_count, 1)
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_duplicate_rejection(self):
        q, path = self._queue()
        try:
            t = Task("x")
            q.enqueue(t)
            with self.assertRaises(ValueError):
                q.enqueue(t)
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_empty_dequeue(self):
        q, path = self._queue()
        try:
            self.assertIsNone(q.dequeue())
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_priority_ordering(self):
        q, path = self._queue()
        try:
            low = Task("low", priority=TaskPriority.LOW)
            high = Task("high", priority=TaskPriority.HIGH)
            q.enqueue(high)
            q.enqueue(low)
            task = q.dequeue()
            self.assertEqual(task.id, "high")
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_dependency_blocking(self):
        q, path = self._queue()
        try:
            t1 = Task("t1")
            t2 = Task("t2", depends_on=["t1"])
            q.enqueue(t1)
            q.enqueue(t2)
            self.assertEqual(t1.status, TaskStatus.READY)
            self.assertEqual(t2.status, TaskStatus.BLOCKED)
            q.complete("t1")
            self.assertEqual(q._require("t2").status, TaskStatus.READY)
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_dependency_cycle_rejection(self):
        q, path = self._queue()
        try:
            t1 = Task("t1", depends_on=["t2"])
            t2 = Task("t2", depends_on=["t1"])
            with self.assertRaises(DependencyCycleError):
                q.enqueue(t1)
                q.enqueue(t2)
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_requeue_failed(self):
        q, path = self._queue()
        try:
            t = Task("x")
            q.enqueue(t)
            q.dequeue()
            q.fail("x", error="boom")
            requeued = q.requeue_failed("x")
            self.assertEqual(requeued.status, TaskStatus.READY)
            self.assertEqual(requeued.retry_count, 0)
            self.assertEqual(requeued.error, None)
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_recover_running_to_ready(self):
        q, path = self._queue()
        try:
            t = Task("x", status=TaskStatus.RUNNING)
            q.enqueue(t)
            q.recover()
            self.assertEqual(q._require("x").status, TaskStatus.READY)
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_tick_promotes_waiting(self):
        q, path = self._queue()
        try:
            past = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 10))
            t = Task("x", scheduled_time=past)
            q.enqueue(t)
            self.assertEqual(t.status, TaskStatus.WAITING)
            q.tick()
            self.assertEqual(q._require("x").status, TaskStatus.READY)
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_get_failed_history_running_upcoming(self):
        q, path = self._queue()
        try:
            a = Task("a", status=TaskStatus.FAILED)
            b = Task("b", status=TaskStatus.RUNNING)
            c = Task("c", status=TaskStatus.READY)
            for t in [a, b, c]:
                q.enqueue(t)
            self.assertEqual(len(q.get_failed_tasks()), 1)
            self.assertEqual(len(q.get_running_tasks()), 0)
            history = q.get_history()
            self.assertEqual(len(history), 1)
            self.assertCountEqual([t.id for t in history], ["a"])
            upcoming = q.get_upcoming(limit=1)
            self.assertEqual(len(upcoming), 1)
            self.assertEqual(upcoming[0].id, "b")
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


class TestGoalIntegration(unittest.TestCase):
    def test_complete_updates_goal_progress(self):
        db_path = Path(tempfile.gettempdir()) / (f"verify-goal-{id(object())}.sqlite")
        store = TaskStorage(db_path=db_path)
        goal_manager = _FakeGoalManager()
        q = TaskQueue(storage=store, goal_manager=goal_manager)
        try:
            t = Task("x", goal_id="g", step_id="s")
            q.enqueue(t)
            q.complete("x")
            self.assertEqual(goal_manager.calls[-1], ("update_progress", "g", 1, 1))
        finally:
            try:
                db_path.unlink(missing_ok=True)
            except Exception:
                pass


class _FakeGoalManager:
    def __init__(self):
        self.calls = []
    def get(self, goal_id):
        return _FakeGoal(goal_id, self)
    def update_progress(self, goal_id, completed, total):
        self.calls.append(("update_progress", goal_id, completed, total))
        return None

class _FakeGoal:
    def __init__(self, goal_id, mgr):
        self.id = goal_id
        self.mgr = mgr


if __name__ == "__main__":
    unittest.main()
