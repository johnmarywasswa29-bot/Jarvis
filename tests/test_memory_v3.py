"""Tests for MemoryManager v3."""
from __future__ import annotations

import os
import sqlite3
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


class DummyConfig:
    project_root = REPO
    memory_persist_directory = str(REPO / "memory")
    memory_collection = "jarvis_memory"

    def memory_path(self) -> Path:
        return Path(self.memory_persist_directory)


class TestMemoryManager(unittest.TestCase):
    def setUp(self) -> None:
        from modules.memory_v2 import MemoryManager
        db_path = REPO / "memory" / "memory_v3_test.sqlite"
        for p in [db_path, db_path.with_suffix(".sqlite-shm"), db_path.with_suffix(".sqlite-wal")]:
            if p.exists():
                p.unlink()
        config = DummyConfig()
        config.memory_persist_directory = str(REPO / "memory")
        self.manager = MemoryManager(config)

    def tearDown(self) -> None:
        self.manager.shutdown()

    def test_add_and_get_memory(self):
        r = self.manager.add_memory("pref", memory_type="semantic", importance=0.8, confidence=0.9, tags=["x"])
        self.assertEqual(r.memory_type, "semantic")
        self.assertEqual(r.importance, 0.8)
        fetched = self.manager.get_memory(r.memory_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.content, "pref")

    def test_deduplication_returns_existing(self):
        r1 = self.manager.add_memory("same", memory_type="episodic", importance=0.4)
        r2 = self.manager.add_memory("same", memory_type="episodic", importance=0.4)
        self.assertEqual(r1.memory_id, r2.memory_id)
        self.assertGreaterEqual(r2.access_count, 1)

    def test_search_returns_ranked_results(self):
        self.manager.add_memory("organize files", memory_type="episodic", importance=0.4, tags=["files"])
        results = self.manager.search("organize files", types=["episodic"], limit=5)
        self.assertTrue(any(x["content"] == "organize files" for x in results))

    def test_get_recent_and_important(self):
        self.manager.add_memory("recent1", memory_type="semantic")
        self.manager.add_memory("recent2", memory_type="procedural", importance=0.9)
        recent = self.manager.get_recent(limit=5)
        self.assertTrue(any(x["content"] == "recent1" for x in recent))
        important = self.manager.get_important(limit=5)
        self.assertTrue(any(x["content"] == "recent2" for x in important))

    def test_decay_pass_returns_counts(self):
        stats = self.manager.decay_pass()
        self.assertIn("promoted", stats)
        self.assertIn("decayed", stats)

    def test_consolidate_merges_duplicates(self):
        self.manager.add_memory("dup", memory_type="episodic", tags=["a"])
        self.manager.add_memory("dup", memory_type="episodic", tags=["a"])
        merged = self.manager.consolidate()
        self.assertIn("merged", merged)

    def test_migrate_from_v2(self):
        v2_db = REPO / "memory" / "migrate_stub.sqlite"
        conn = sqlite3.connect(v2_db, check_same_thread=False)
        conn.execute("CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, role TEXT, content TEXT, ts REAL, metadata TEXT)")
        conn.execute("INSERT INTO messages(role, content, ts, metadata) VALUES(?,?,?,?)", ("user", "legacy", time.time(), "{}"))
        conn.commit()
        conn.close()
        count = self.manager.migrate_from_v2(v2_db)
        self.assertEqual(count, 1)
        if v2_db.exists():
            v2_db.unlink()

    def test_v2_compat_remains_usable(self):
        from modules.memory_v2 import JarvisMemoryV2
        v2 = JarvisMemoryV2(DummyConfig())
        v2.add_message("user", "compat check")
        v2.flush()
        ctx = v2.get_recent_context(max_messages=5)
        self.assertIn("compat check", ctx)


if __name__ == "__main__":
    unittest.main()
