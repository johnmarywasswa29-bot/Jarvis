"""Goal Manager: long-term objective tracking."""
from __future__ import annotations

import threading
from typing import Any, Callable, Iterable, Optional

from goal_manager.goal import Goal, GoalPriority, GoalStatus, PlanAttachment
from goal_manager.goal_events import GoalEvent, GoalEventBus, GoalEventType
from goal_manager.goal_serializer import GoalSerializer
from goal_manager.goal_storage import GoalStorage


class GoalManager:
    def __init__(
        self,
        storage: Optional[GoalStorage] = None,
        event_bus: Optional[GoalEventBus] = None,
    ) -> None:
        self.storage = storage or GoalStorage()
        self.event_bus = event_bus or GoalEventBus()
        self._goals: dict[str, Goal] = {}
        self._lock = threading.RLock()

    def load_active(self) -> list[Goal]:
        with self._lock:
            goals = self.storage.load_active()
            self._goals.clear()
            for g in goals:
                self._goals[g.id] = g
            return list(self._goals.values())

    def create(
        self,
        title: str,
        description: str = "",
        *,
        priority: GoalPriority = GoalPriority.NORMAL,
        owner: str = "",
        category: str = "",
        tags: Optional[list[str]] = None,
        notes: str = "",
    ) -> Goal:
        with self._lock:
            goal = Goal(
                title=title,
                description=description,
                priority=priority,
                owner=owner,
                category=category,
                tags=tags,
                notes=notes,
            )
            self.storage.upsert(goal)
            self._goals[goal.id] = goal
            self.event_bus.publish(GoalEvent(GoalEventType.CREATED, goal.id, GoalSerializer.to_dict(goal)))
            return goal

    def get(self, goal_id: str) -> Optional[Goal]:
        with self._lock:
            return self._goals.get(goal_id) or self.storage.load(goal_id)

    def attach_plan(self, goal_id: str, plan_dict: dict[str, Any]) -> Optional[Goal]:
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal is None:
                goal = self.storage.load(goal_id)
            if goal is None:
                return None
            plan_id = str(plan_dict.get("id") or plan_dict.get("plan_id") or "unknown-plan")
            attachment = PlanAttachment(plan_id=plan_id, plan_dict=dict(plan_dict))
            goal.attach_plan(attachment)
            goal.progress = GoalManager._clamp_progress(self._count_completed_tasks(goal), self._count_total_tasks(goal))
            goal.status = self._infer_status(goal)
            if goal.status == GoalStatus.COMPLETED:
                goal.completed_at = goal.updated_at
            self.storage.upsert(goal)
            self.event_bus.publish(GoalEvent(GoalEventType.UPDATED, goal.id, GoalSerializer.to_dict(goal)))
            return goal

    def update_progress(self, goal_id: str, completed_tasks: int, total_tasks: int) -> Optional[Goal]:
        with self._lock:
            goal = self._goals.get(goal_id) or self.storage.load(goal_id)
            if goal is None:
                return None
            progress = GoalManager._clamp_progress(completed_tasks, total_tasks)
            goal.progress = progress
            goal.touch()
            goal.status = self._infer_status(goal)
            if goal.status == GoalStatus.COMPLETED and not goal.completed_at:
                goal.completed_at = goal.updated_at
            self.storage.upsert(goal)
            self.event_bus.publish(GoalEvent(GoalEventType.UPDATED, goal.id, GoalSerializer.to_dict(goal)))
            return goal

    def pause(self, goal_id: str) -> Optional[Goal]:
        with self._lock:
            goal = self._goals.get(goal_id) or self.storage.load(goal_id)
            if goal is None or goal.status == GoalStatus.ARCHIVED:
                return None
            goal.status = GoalStatus.PAUSED
            goal.touch()
            self.storage.upsert(goal)
            self.event_bus.publish(GoalEvent(GoalEventType.PAUSED, goal.id, GoalSerializer.to_dict(goal)))
            return goal

    def complete(self, goal_id: str) -> Optional[Goal]:
        with self._lock:
            goal = self._goals.get(goal_id) or self.storage.load(goal_id)
            if goal is None or goal.status in (GoalStatus.COMPLETED, GoalStatus.ARCHIVED):
                return None
            goal.status = GoalStatus.COMPLETED
            goal.progress = 100.0
            goal.completed_at = goal.updated_at
            self.storage.upsert(goal)
            self.event_bus.publish(GoalEvent(GoalEventType.COMPLETED, goal.id, GoalSerializer.to_dict(goal)))
            return goal

    def archive(self, goal_id: str) -> Optional[Goal]:
        with self._lock:
            goal = self._goals.get(goal_id) or self.storage.load(goal_id)
            if goal is None or goal.status == GoalStatus.ARCHIVED:
                return None
            goal.status = GoalStatus.ARCHIVED
            goal.touch()
            self.storage.upsert(goal)
            self.event_bus.publish(GoalEvent(GoalEventType.ARCHIVED, goal.id, GoalSerializer.to_dict(goal)))
            return goal

    def delete(self, goal_id: str) -> bool:
        with self._lock:
            if goal_id not in self._goals and not self.storage.exists(goal_id):
                return False
            self.storage.delete(goal_id)
            self._goals.pop(goal_id, None)
            self.event_bus.publish(GoalEvent(GoalEventType.DELETED, goal_id, {}))
            return True

    def search(self, query: str) -> list[Goal]:
        with self._lock:
            results: list[Goal] = []
            for goal in list(self._goals.values()):
                if GoalManager._matches(goal, query):
                    results.append(goal)
            for goal in self.storage.search(query):
                if goal.id not in self._goals:
                    results.append(goal)
            return results

    def get_active_goals(self) -> list[Goal]:
        with self._lock:
            combined: list[Goal] = []
            seen: set[str] = set()
            for goals in (
                [g for g in self._goals.values() if g.status == GoalStatus.ACTIVE],
                self.storage.load_active(),
            ):
                for g in goals:
                    if g.id not in seen:
                        seen.add(g.id)
                        combined.append(g)
            return combined

    def get_completed_goals(self) -> list[Goal]:
        with self._lock:
            combined: list[Goal] = []
            seen: set[str] = set()
            for goals in (
                [g for g in self._goals.values() if g.status == GoalStatus.COMPLETED],
                self.storage.load_completed(),
            ):
                for g in goals:
                    if g.id not in seen:
                        seen.add(g.id)
                        combined.append(g)
            return combined

    def get_upcoming_tasks(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            tasks: list[dict[str, Any]] = []
            for goal in self._goals.values():
                for plan in goal.plans:
                    for step in plan.plan_dict.get("steps", []):
                        tasks.append({
                            "goal_id": goal.id,
                            "goal_title": goal.title,
                            "plan_id": plan.plan_id,
                            "task_id": step.get("id"),
                            "description": step.get("description"),
                            "tool": step.get("tool"),
                            "confidence": step.get("confidence"),
                            "estimated_duration": step.get("estimated_duration"),
                            "requires_confirmation": step.get("requires_confirmation"),
                        })
                        if len(tasks) >= limit:
                            return tasks
            return tasks[:limit]

    @staticmethod
    def _clamp_progress(completed_tasks: int, total_tasks: int) -> float:
        if total_tasks <= 0:
            return 0.0
        return max(0.0, min(100.0, (completed_tasks / total_tasks) * 100.0))

    def _infer_status(self, goal: Goal) -> GoalStatus:
        if goal.progress >= 100.0:
            return GoalStatus.COMPLETED
        return goal.status

    @staticmethod
    def _matches(goal: Goal, query: str) -> bool:
        q = query.lower()
        return q in goal.title.lower() or q in goal.description.lower() or q in goal.category.lower() or any(q in t.lower() for t in goal.tags)

    @staticmethod
    def _count_total_tasks(goal: Goal) -> int:
        total = 0
        for plan in goal.plans:
            total += len(plan.plan_dict.get("steps", []))
        return total

    @staticmethod
    def _count_completed_tasks(goal: Goal) -> int:
        completed = 0
        for plan in goal.plans:
            for step in plan.plan_dict.get("steps", []):
                if step.get("status") == "completed":
                    completed += 1
        return completed
