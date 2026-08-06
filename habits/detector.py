"""HabitDetector: merges mined patterns into learnable habits."""
from __future__ import annotations

import hashlib
from datetime import datetime, UTC
from typing import Any, Optional

from habits.habit_store import Habit, HabitStore


class HabitDetector:
    def __init__(self, store: HabitStore, min_confidence: float = 0.3) -> None:
        self.store = store
        self.min_confidence = min_confidence

    def detect(self, patterns: list[dict[str, Any]], now: Optional[datetime] = None) -> list[Habit]:
        now = now or datetime.now(UTC).replace(tzinfo=None)
        existing = {self._pattern_key(h): h for h in self.store.list_habits()}
        seen = set()
        out: list[Habit] = []

        for p in patterns:
            key = self._pattern_key_from_pattern(p)
            if key in seen:
                continue
            seen.add(key)
            habit = existing.get(key)
            if habit is None:
                habit = Habit(
                    name=self._name_from_pattern(p),
                    confidence=float(p.get("confidence", 0.0)),
                    frequency=int(p.get("frequency", 1)),
                    recency=1.0,
                    avg_duration_s=0.0,
                    associated_apps=p.get("apps", []) or p.get("associated_apps", []),
                    associated_documents=p.get("documents", []) or p.get("associated_documents", []),
                    associated_folders=p.get("folders", []) or p.get("associated_folders", []),
                    associated_intents=p.get("intents", []) or p.get("associated_intents", []),
                    success_rate=1.0,
                    last_executed=p.get("last_seen", now.isoformat()),
                    created_at=p.get("created_at", now.isoformat()),
                    metadata=p.get("metadata", {}),
                )
                if habit.confidence < self.min_confidence:
                    continue
                self.store.add_habit(habit)
                out.append(habit)
            else:
                habit.frequency += int(p.get("frequency", 1))
                habit.confidence = min(1.0, habit.confidence + 0.05)
                habit.recency = 1.0
                habit.last_executed = p.get("last_seen", habit.last_executed)
                self.store.update_habit(habit)
                out.append(habit)
        return out

    def decay(self, half_life_days: float = 14.0) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        for habit in self.store.list_habits():
            last = datetime.fromisoformat(habit.last_executed)
            age_days = max((now - last).total_seconds() / 86400.0, 0.0)
            decay_factor = 0.5 ** (age_days / max(half_life_days, 1e-6))
            habit.confidence = max(0.0, habit.confidence * decay_factor)
            habit.recency = max(0.0, 1.0 - age_days / 30.0)
            if habit.confidence <= 0.05:
                self.store.delete_habit(habit.uuid)
            else:
                self.store.update_habit(habit)

    def _pattern_key(self, habit: Habit) -> str:
        parts = [habit.name] + sorted({a for a in habit.associated_apps if a})
        return hashlib.sha1("|".join(parts).encode()).hexdigest()

    def _pattern_key_from_pattern(self, pattern: dict[str, Any]) -> str:
        seq = pattern.get("sequence") or pattern.get("apps") or []
        return hashlib.sha1("|".join(str(x) for x in seq).encode()).hexdigest()

    def _name_from_pattern(self, pattern: dict[str, Any]) -> str:
        name = pattern.get("name")
        if name:
            return name
        seq = pattern.get("sequence") or pattern.get("apps") or []
        return " + ".join(str(x) for x in seq[:4])
