"""HabitManager: main API for recording events, learning patterns, suggesting habits."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, UTC
from typing import Any, Optional

from habits.habit_store import Habit, HabitStore
from habits.detector import HabitDetector
from habits.pattern_miner import PatternMiner
from habits.scorer import HabitScorer


class HabitManager:
    def __init__(
        self,
        store: Optional[HabitStore] = None,
        detector: Optional[HabitDetector] = None,
        analyzer: Optional[PatternMiner] = None,
        scorer: Optional[HabitScorer] = None,
        *,
        background: bool = True,
        decay_interval_s: float = 3600.0,
    ) -> None:
        self.store = store or HabitStore()
        self.detector = detector or HabitDetector(self.store)
        self.analyzer = analyzer or PatternMiner()
        self.scorer = scorer or HabitScorer()
        self._background = background
        self._decay_interval_s = max(decay_interval_s, 1.0)
        self._lock = threading.RLock()
        self._last_learned: Optional[datetime] = None

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "HabitManager":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def record_event(self, kind: str, payload: dict[str, Any]) -> None:
        self.store.record_event(kind, payload)

    def learn_patterns(self, now: Optional[datetime] = None) -> list[Habit]:
        now = now or datetime.now(UTC).replace(tzinfo=None)
        with self._lock:
            since = now - timedelta(days=30)
            events = self.store.recent_events(since=since, limit=5000)
            patterns = []
            patterns.extend(self.analyzer.analyze(events, now))
            patterns.extend(self.analyzer.detect_time_habits(events))
            patterns.extend(self.analyzer.detect_day_habits(events))
            patterns.extend(self.analyzer.detect_project_habits(events))
            habits = self.detector.detect(patterns, now)
            self.detector.decay()
            self._last_learned = now
            return habits

    def get_habits(self) -> list[Habit]:
        with self._lock:
            return self.store.list_habits()

    def suggest_habits(self, context: Optional[dict[str, Any]] = None, threshold: float = 0.5) -> list[dict[str, Any]]:
        with self._lock:
            habits = self.store.list_habits()
        if context:
            return self.scorer.context_scores(habits, context, now=datetime.now(UTC).replace(tzinfo=None))
        return self.scorer.suggest(habits, threshold=threshold, now=datetime.now(UTC).replace(tzinfo=None))

    def execute_habit(self, uuid: str) -> Optional[Habit]:
        with self._lock:
            habit = self.store.get_habit(uuid)
            if not habit:
                return None
            habit.frequency += 1
            habit.recency = 1.0
            habit.last_executed = datetime.now(UTC).replace(tzinfo=None).isoformat()
            habit.confidence = min(1.0, habit.confidence + 0.1)
            self.store.update_habit(habit)
            return habit

    def forget_habit(self, uuid: str) -> None:
        with self._lock:
            self.store.delete_habit(uuid)

    def feedback(self, uuid: str, success: bool, duration_s: float = 0.0) -> None:
        with self._lock:
            habit = self.store.get_habit(uuid)
            if not habit:
                return
            habit.success_rate = (habit.success_rate * habit.frequency + (1.0 if success else 0.0)) / max(habit.frequency + 1, 1)
            if habit.frequency > 0:
                habit.avg_duration_s = (habit.avg_duration_s * (habit.frequency - 1) + duration_s) / habit.frequency
            habit.frequency += 1
            habit.recency = 1.0
            habit.last_executed = datetime.now(UTC).replace(tzinfo=None).isoformat()
            self.store.update_habit(habit)
