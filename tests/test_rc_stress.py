"""Release Candidate stress probe."""
from __future__ import annotations

import importlib
import os
import shutil
import sys
import threading
import time
import unittest
from pathlib import Path

REPO = Path(r"C:\Users\User NA\Desktop\jarvis")
os.chdir(REPO)
sys.path.insert(0, str(REPO))


class RCStress(unittest.TestCase):
    def setUp(self):
        self.repo = REPO
        self.data_dir = self.repo / "data" / f"rc_{id(self)}_{threading.get_native_id()}"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        try:
            shutil.rmtree(self.data_dir, ignore_errors=True)
        except Exception:
            pass

    def _cfg(self):
        from modules.config import JarvisConfig

        class C(JarvisConfig):
            def __init__(self, repo: Path):
                self.repo = repo
                self.memory_persist_directory = str(repo / "data" / "rc_mem")
                Path(self.memory_persist_directory).mkdir(parents=True, exist_ok=True)

        return C(self.repo)

    def test_memory_1000_inserts(self):
        from modules.memory_v2 import JarvisMemoryV2

        mem = JarvisMemoryV2(self._cfg())
        for i in range(1000):
            mem.add_message("user", f"msg {i}", metadata={"i": i})
        ctx = mem.get_recent_context(50)
        self.assertIn("msg 999", ctx)
        mem.shutdown()

    def test_tasks_500_queue(self):
        from task_queue.task_queue import TaskQueue
        from task_queue.task_storage import TaskStorage
        from task_queue.task import Task, TaskPriority

        db = self.data_dir / "rc_tasks.sqlite"
        storage = TaskStorage(db)
        q = TaskQueue(storage=storage)
        for i in range(500):
            q.enqueue(Task(id=f"t{i}", title=f"Task {i}", status="pending", priority=TaskPriority.LOW))
        self.assertEqual(len(q.get_queue()), 500)

    def test_goals_100(self):
        from goal_manager.goal_manager import GoalManager
        from goal_manager.goal_storage import GoalStorage
        from goal_manager.goal import GoalPriority

        db = self.data_dir / "rc_goals.sqlite"
        storage = GoalStorage(db)
        gm = GoalManager(storage=storage)
        for i in range(100):
            gm.create(f"Goal {i}", priority=GoalPriority.LOW)
        self.assertEqual(len(gm.get_active_goals()), 100)

    def test_knowledge_100_docs(self):
        from knowledge.knowledge_engine import KnowledgeEngine

        dirp = self.data_dir / "rc_knowledge"
        dirp.mkdir(parents=True, exist_ok=True)
        engine = KnowledgeEngine(dirp)
        for i in range(100):
            p = dirp / f"d{i}.txt"
            p.write_text(f"alpha content {i} " * 10, encoding="utf-8")
            engine.index_file(p)
        res = engine.search("alpha", k=5)
        self.assertGreaterEqual(len(res), 5)
        engine.close()

    def test_idle_8h_sim(self):
        from knowledge.knowledge_engine import KnowledgeEngine

        base = self.data_dir / "rc_knowledge"
        base.mkdir(parents=True, exist_ok=True)
        seed = base / "seed.txt"
        seed.write_text("alpha content seed", encoding="utf-8")

        eng = KnowledgeEngine(base)
        try:
            self.assertIsNotNone(eng.index_file(seed))
            self.assertGreaterEqual(len(eng.search("alpha", k=3)), 1)
        finally:
            eng.close()

        eng2 = KnowledgeEngine(base)
        try:
            self.assertGreaterEqual(len(eng2.search("alpha", k=3)), 1)
        finally:
            eng2.close()

    def test_plugin_reload(self):
        import modules.tools as tools

        for _ in range(5):
            importlib.reload(tools)

    def test_recovery_after_crash(self):
        from task_queue.task_queue import TaskQueue
        from task_queue.task_storage import TaskStorage
        from task_queue.task import Task, TaskPriority

        db = self.data_dir / "rc_recovery.sqlite"
        storage = TaskStorage(db)
        q = TaskQueue(storage=storage)
        q.enqueue(Task(id="r1", title="r1", status="pending", priority=TaskPriority.NORMAL))
        q.enqueue(Task(id="r2", title="r2", status="pending", priority=TaskPriority.NORMAL))
        q._tasks = {}
        q.recover()
        self.assertEqual(len(q.load_active()), 2)


if __name__ == "__main__":
    unittest.main()
