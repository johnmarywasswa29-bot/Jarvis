"""HabitScorer: ranks and filters habits for suggestions and personalization."""
from __future__ import annotations

import math
from datetime import datetime, UTC
from typing import Any, Optional

from habits.habit_store import Habit


class HabitScorer:
    def __init__(
        self,
        confidence_weight: float = 0.4,
        frequency_weight: float = 0.3,
        recency_weight: float = 0.2,
        success_weight: float = 0.1,
    ) -> None:
        self.confidence_weight = confidence_weight
        self.frequency_weight = frequency_weight
        self.recency_weight = recency_weight
        self.success_weight = success_weight

    def score(self, habit: Habit, now: Optional[datetime] = None) -> float:
        now = now or datetime.now(UTC).replace(tzinfo=None)
        c = max(0.0, min(1.0, habit.confidence))
        f = math.log1p(max(habit.frequency, 0))
        max_f = math.log1p(20)
        f_norm = min(f / max_f, 1.0) if max_f else 0.0
        r = max(0.0, min(1.0, habit.recency))
        s = max(0.0, min(1.0, habit.success_rate))
        return self.confidence_weight * c + self.frequency_weight * f_norm + self.recency_weight * r + self.success_weight * s

    def suggest(self, habits: list[Habit], threshold: float = 0.5, now: Optional[datetime] = None) -> list[dict[str, Any]]:
        now = now or datetime.now(UTC).replace(tzinfo=None)
        ranked = []
        for habit in habits:
            sc = self.score(habit, now)
            ranked.append({"habit": habit, "score": sc})
        ranked.sort(key=lambda x: x["score"], reverse=True)
        return [item for item in ranked if item["score"] >= threshold]

    def context_scores(self, habits: list[Habit], context: dict[str, Any], now: Optional[datetime] = None) -> list[dict[str, Any]]:
        now = now or datetime.now(UTC).replace(tzinfo=None)
        context_apps = {a.lower() for a in context.get("apps", []) if a}
        context_intents = {a.lower() for a in context.get("intents", []) if a}
        context_folders = {a.lower() for a in context.get("folders", []) if a}
        ranked = []
        for habit in habits:
            base = self.score(habit, now)
            bonus = 0.0
            if any(str(a).lower() in context_apps for a in habit.associated_apps):
                bonus += 0.15
            if any(str(i).lower() in context_intents for i in habit.associated_intents):
                bonus += 0.1
            if any(str(f).lower() in context_folders for f in habit.associated_folders):
                bonus += 0.1
            ranked.append({"habit": habit, "score": min(base + bonus, 1.0)})
        ranked.sort(key=lambda x: x["score"], reverse=True)
        return ranked
