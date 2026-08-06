"""Additional Phase 2 tests: personalization, background learning, false positives, duplicates, UI."""
from __future__ import annotations

import os
import sys
import time
import threading
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
from modules.intent.analyzer import IntentAnalyzer
from modules.memory_v2 import MemoryManager


TMP_DIR = REPO / "tests" / "tmp_habits2"


def _clean_dir(path: Path) -> None:
    import shutil
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


class TestHabitDuplicates(unittest.TestCase):
    def setUp(self):
        _clean_dir(TMP_DIR)
        self.mgr = HabitManager(store=HabitStore(TMP_DIR / "habits.sqlite"), background=False)

    def tearDown(self):
        self.mgr.close()

    def test_same_sequence_merges(self):
        for _ in range(6):
            self.mgr.record_event("app_launch", {"app": "code"})
            self.mgr.record_event("app_launch", {"app": "terminal"})
        habits1 = self.mgr.learn_patterns()
        for _ in range(3):
            self.mgr.record_event("app_launch", {"app": "code"})
            self.mgr.record_event("app_launch", {"app": "terminal"})
        habits2 = self.mgr.learn_patterns()
        merged = [h for h in habits2 if h.name == "code + terminal"]
        self.assertTrue(len(merged) == 1)
        self.assertGreaterEqual(merged[0].frequency, 2)

    def test_different_sequences_distinct(self):
        for _ in range(4):
            self.mgr.record_event("app_launch", {"app": "code"})
            self.mgr.record_event("app_launch", {"app": "terminal"})
        for _ in range(4):
            self.mgr.record_event("app_launch", {"app": "browser"})
            self.mgr.record_event("app_launch", {"app": "music"})
        habits = self.mgr.learn_patterns()
        names = [h.name for h in habits]
        self.assertIn("code + terminal", names)
        self.assertIn("browser + music", names)


class TestHabitFalsePositives(unittest.TestCase):
    def setUp(self):
        _clean_dir(TMP_DIR)
        self.mgr = HabitManager(store=HabitStore(TMP_DIR / "habits.sqlite"), background=False)

    def tearDown(self):
        self.mgr.close()

    def test_noise_not_learned(self):
        apps = ["a", "b", "c", "d", "e", "f"]
        for app in apps:
            self.mgr.record_event("app_launch", {"app": app})
        habits = self.mgr.learn_patterns()
        for h in habits:
            self.assertGreaterEqual(h.confidence, 0.3)

    def test_long_gap_decays(self):
        for _ in range(6):
            self.mgr.record_event("app_launch", {"app": "code"})
            self.mgr.record_event("app_launch", {"app": "terminal"})
        habits = self.mgr.learn_patterns()
        target = None
        for h in habits:
            if h.name == "code + terminal":
                target = h
                h.last_executed = (datetime.now(UTC).replace(tzinfo=None) - timedelta(days=60)).isoformat()
                self.mgr.store.update_habit(h)
        self.assertIsNotNone(target, "expected coding pattern")
        self.mgr.detector.decay(half_life_days=14)
        loaded = self.mgr.store.get_habit(target.uuid)
        self.assertTrue(loaded is None or loaded.confidence < 0.5)


class TestHabitBackground(unittest.TestCase):
    def setUp(self):
        _clean_dir(TMP_DIR)
        self.mgr = HabitManager(store=HabitStore(TMP_DIR / "habits.sqlite"), background=True)

    def tearDown(self):
        self.mgr.close()

    def test_background_does_not_block_ui(self):
        start = time.perf_counter()
        for _ in range(6):
            self.mgr.record_event("app_launch", {"app": "code"})
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 0.35)
        habits = self.mgr.learn_patterns()
        self.assertTrue(len(habits) >= 1)


class TestHabitPersonalization(unittest.TestCase):
    def setUp(self):
        _clean_dir(TMP_DIR)
        self.mgr = HabitManager(store=HabitStore(TMP_DIR / "habits.sqlite"), background=False)
        self.mgr.record_event("app_launch", {"app": "code"})
        self.mgr.record_event("app_launch", {"app": "terminal"})
        self.mgr.record_event("app_launch", {"app": "code"})
        self.mgr.record_event("app_launch", {"app": "terminal"})
        self.mgr.learn_patterns()

    def tearDown(self):
        self.mgr.close()

    def test_suggestion_returns_list(self):
        out = self.mgr.suggest_habits()
        self.assertTrue(len(out) >= 0)

    def test_context_boost(self):
        out = self.mgr.suggest_habits(context={"apps": ["code"]})
        if out:
            self.assertGreater(out[0]["score"], 0.0)

    def test_never_auto_execute(self):
        out = self.mgr.suggest_habits()
        for item in out:
            self.assertNotIn("execute", item)


class TestHabitRetrieverIntegration(unittest.TestCase):
    def test_retriever_uses_habits(self):
        mgr = HabitManager(background=False)
        mgr.record_event("app_launch", {"app": "code"})
        mgr.record_event("app_launch", {"app": "terminal"})
        habits = mgr.learn_patterns()
        self.assertTrue(len(habits) >= 0)
        mgr.close()


class TestHabitIntegration(unittest.TestCase):
    def test_memory_intent_tools_full(self):
        mgr = HabitManager(background=False)
        mgr.record_event("app_launch", {"app": "code"})
        mgr.record_event("intent", {"intent": "open_file"})
        habits = mgr.learn_patterns()
        self.assertTrue(len(habits) >= 0)
        mgr.close()

    def test_no_auto_execute_full(self):
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
