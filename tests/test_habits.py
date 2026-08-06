"""Phase 2 tests: HabitManager, HabitStore, HabitDetector, PatternMiner, HabitScorer."""
from __future__ import annotations

import os
import sys
import time
import unittest
from datetime import datetime, timedelta, UTC
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from habits.habit_store import Habit, HabitStore
from habits.detector import HabitDetector
from habits.pattern_miner import PatternMiner
from habits.scorer import HabitScorer
from habits.habit_manager import HabitManager
from modules.memory_v2 import MemoryManager
from modules.intent.analyzer import IntentAnalyzer


def _clean_dir(path: Path) -> None:
    import shutil
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


TMP_DIR = REPO / "tests" / "tmp_habits"


class TestHabitStore(unittest.TestCase):
    def setUp(self):
        _clean_dir(TMP_DIR)
        self.store = HabitStore(TMP_DIR / "habits.sqlite")

    def tearDown(self):
        self.store.close()

    def test_add_and_get(self):
        h = Habit(name="coding", confidence=0.5, frequency=2, recency=1.0)
        uid = self.store.add_habit(h)
        loaded = self.store.get_habit(uid)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "coding")

    def test_update(self):
        h = Habit(name="study", confidence=0.2, frequency=1, recency=0.5)
        uid = self.store.add_habit(h)
        h.confidence = 0.7
        self.store.update_habit(h)
        self.assertEqual(self.store.get_habit(uid).confidence, 0.7)

    def test_delete(self):
        h = Habit(name="old")
        uid = self.store.add_habit(h)
        self.store.delete_habit(uid)
        self.assertIsNone(self.store.get_habit(uid))

    def test_list(self):
        self.store.add_habit(Habit(name="a"))
        self.store.add_habit(Habit(name="b"))
        names = [h.name for h in self.store.list_habits()]
        self.assertIn("a", names)
        self.assertIn("b", names)

    def test_events(self):
        self.store.record_event("app_launch", {"app": "code"})
        evts = self.store.recent_events(since=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5))
        self.assertEqual(len(evts), 1)
        self.assertEqual(evts[0]["kind"], "app_launch")
        self.assertEqual(evts[0]["payload"]["app"], "code")

    def test_prune(self):
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=31)
        old_ts = (datetime.now(UTC).replace(tzinfo=None) - timedelta(days=32)).isoformat()
        self.store.record_event("old", {"x": 1})
        with self.store._lock, self.store._conn:
            self.store._conn.execute("UPDATE events SET ts = ? WHERE kind = 'old'", (old_ts,))
        self.store.record_event("new", {"x": 2})
        n = self.store.prune_events(cutoff)
        self.assertEqual(n, 1)
        self.assertEqual(len(self.store.recent_events(since=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=40))), 1)


class TestPatternMiner(unittest.TestCase):
    def setUp(self):
        _clean_dir(TMP_DIR)
        self.miner = PatternMiner()

    def test_app_sequence(self):
        events = [
            {"ts": datetime.now(UTC).replace(tzinfo=None).isoformat(), "kind": "app_launch", "payload": {"app": "code"}},
            {"ts": datetime.now(UTC).replace(tzinfo=None).isoformat(), "kind": "app_launch", "payload": {"app": "terminal"}},
            {"ts": datetime.now(UTC).replace(tzinfo=None).isoformat(), "kind": "app_launch", "payload": {"app": "code"}},
            {"ts": datetime.now(UTC).replace(tzinfo=None).isoformat(), "kind": "app_launch", "payload": {"app": "terminal"}},
        ]
        patterns = self.miner.analyze(events)
        seqs = [p for p in patterns if p["pattern_type"] == "app_sequence"]
        self.assertTrue(len(seqs) >= 1)

    def test_time_habits(self):
        morning = datetime.now(UTC).replace(tzinfo=None).replace(hour=8)
        events = [{"ts": morning.isoformat(), "kind": "app_launch", "payload": {"app": "code"}}]
        habits = self.miner.detect_time_habits(events)
        self.assertTrue(len(habits) >= 1)
        self.assertEqual(habits[0]["metadata"]["time_bucket"], "morning")

    def test_day_habits(self):
        monday = datetime.now(UTC).replace(tzinfo=None).replace(hour=10)
        while monday.weekday() != 0: monday -= timedelta(days=1)
        events = [{"ts": monday.isoformat(), "kind": "app_launch", "payload": {"app": "browser"}}]
        habits = self.miner.detect_day_habits(events)
        self.assertTrue(len(habits) >= 1)

    def test_project_habits(self):
        p = REPO / "tests" / "tmp_habits" / "proj" / "main.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
        events = [
            {"ts": datetime.now(UTC).replace(tzinfo=None).isoformat(), "kind": "file_open", "payload": {"path": str(p), "app": "code"}},
            {"ts": datetime.now(UTC).replace(tzinfo=None).isoformat(), "kind": "file_open", "payload": {"path": str(p), "app": "code"}},
        ]
        habits = self.miner.detect_project_habits(events)
        self.assertTrue(len(habits) >= 1)
        self.assertEqual(habits[0]["pattern_type"], "project_habit")


class TestHabitDetector(unittest.TestCase):
    def setUp(self):
        _clean_dir(TMP_DIR)
        self.store = HabitStore(TMP_DIR / "habits.sqlite")
        self.detector = HabitDetector(self.store)

    def tearDown(self):
        self.store.close()

    def test_detect_new_habit(self):
        patterns = [
            {
                "pattern_type": "app_sequence",
                "apps": ["code", "terminal"],
                "frequency": 5,
                "confidence": 0.6,
                "last_seen": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "created_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "metadata": {},
            }
        ]
        habits = self.detector.detect(patterns)
        self.assertEqual(len(habits), 1)
        self.assertEqual(habits[0].name, "code + terminal")

    def test_update_existing(self):
        self.store.add_habit(Habit(name="code + terminal", confidence=0.5, frequency=1, recency=1.0, associated_apps=["code", "terminal"]))
        patterns = [
            {
                "pattern_type": "app_sequence",
                "apps": ["code", "terminal"],
                "frequency": 2,
                "confidence": 0.4,
                "last_seen": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "created_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "metadata": {},
            }
        ]
        habits = self.detector.detect(patterns)
        self.assertEqual(len(habits), 1)
        self.assertGreaterEqual(habits[0].frequency, 2)

    def test_decay_removes_old(self):
        old = Habit(name="old", confidence=0.04, frequency=1, recency=0.0, last_executed=(datetime.now(UTC).replace(tzinfo=None) - timedelta(days=60)).isoformat())
        self.store.add_habit(old)
        self.detector.decay(half_life_days=14)
        self.assertIsNone(self.store.get_habit(old.uuid))


class TestHabitScorer(unittest.TestCase):
    def test_score_ordering(self):
        h1 = Habit(name="a", confidence=0.9, frequency=10, recency=1.0, success_rate=0.95)
        h2 = Habit(name="b", confidence=0.2, frequency=1, recency=0.1, success_rate=0.4)
        s = HabitScorer()
        self.assertGreater(s.score(h1), s.score(h2))

    def test_suggest_threshold(self):
        habits = [Habit(name="a", confidence=0.1, frequency=1, recency=0.0), Habit(name="b", confidence=0.9, frequency=10, recency=1.0)]
        s = HabitScorer()
        out = s.suggest(habits, threshold=0.6)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["habit"].name, "b")

    def test_context_bonus(self):
        h = Habit(name="coding", confidence=0.5, frequency=1, recency=1.0, associated_apps=["code"], associated_intents=["open_file"])
        s = HabitScorer()
        ctx = {"apps": ["code"], "intents": ["open_file"]}
        out = s.context_scores([h], ctx)
        self.assertTrue(out[0]["score"] > s.score(h))


class TestHabitManager(unittest.TestCase):
    def setUp(self):
        _clean_dir(TMP_DIR)
        self.mgr = HabitManager(store=HabitStore(TMP_DIR / "habits.sqlite"), background=False)

    def tearDown(self):
        self.mgr.close()

    def test_record_event(self):
        self.mgr.record_event("app_launch", {"app": "code"})
        evts = self.mgr.store.recent_events(since=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5))
        self.assertEqual(len(evts), 1)

    def test_learn_patterns(self):
        for _ in range(5):
            self.mgr.record_event("app_launch", {"app": "code"})
        habits = self.mgr.learn_patterns()
        self.assertTrue(len(habits) >= 1)

    def test_suggest_habits(self):
        for _ in range(6):
            self.mgr.record_event("app_launch", {"app": "code"})
        self.mgr.learn_patterns()
        out = self.mgr.suggest_habits()
        self.assertTrue(len(out) >= 1)

    def test_execute_habit(self):
        h = Habit(name="routine", confidence=0.5, frequency=1, recency=1.0)
        uid = self.mgr.store.add_habit(h)
        updated = self.mgr.execute_habit(uid)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.frequency, 2)

    def test_feedback(self):
        h = Habit(name="x", confidence=0.5, frequency=1, recency=1.0, success_rate=1.0)
        uid = self.mgr.store.add_habit(h)
        self.mgr.feedback(uid, success=False, duration_s=5.0)
        loaded = self.mgr.store.get_habit(uid)
        self.assertLess(loaded.success_rate, 1.0)

    def test_forget_habit(self):
        h = Habit(name="z", confidence=0.5, frequency=1, recency=1.0)
        uid = self.mgr.store.add_habit(h)
        self.mgr.forget_habit(uid)
        self.assertIsNone(self.mgr.store.get_habit(uid))


class TestHabitIntegration(unittest.TestCase):
    def test_memory_intent_tools(self):
        mgr = HabitManager(background=False)
        mgr.record_event("app_launch", {"app": "code"})
        mgr.record_event("intent", {"intent": "open_file", "intent": "open_file"})
        habits = mgr.learn_patterns()
        self.assertTrue(len(habits) >= 1)
        mgr.close()

    def test_no_auto_execute(self):
        mgr = HabitManager(background=False)
        mgr.record_event("app_launch", {"app": "code"})
        mgr.learn_patterns()
        out = mgr.suggest_habits()
        for item in out:
            self.assertIn("score", item)
            self.assertIn("habit", item)
        mgr.close()


if __name__ == "__main__":
    unittest.main()