"""Task Queue: executable task orchestration with dependencies and scheduler."""
from __future__ import annotations

import heapq
import threading
import time
from typing import Any, Callable, Iterable, Optional

from task_queue.task import Task, TaskPriority, TaskStatus
from task_queue.task_events import TaskEvent, TaskEventBus, TaskEventType
from task_queue.task_serializer import TaskSerializer
from task_queue.task_scheduler import TaskScheduler
from task_queue.task_storage import TaskStorage


class DependencyCycleError(Exception):
    """Raised when circular task dependencies are detected."""


class TaskQueue:
    def __init__(
        self,
        storage: Optional[TaskStorage] = None,
        event_bus: Optional[TaskEventBus] = None,
        scheduler: Optional[TaskScheduler] = None,
        permission_manager: Optional[Any] = None,
        goal_manager: Optional[Any] = None,
    ) -> None:
        self.storage = storage or TaskStorage()
        self.event_bus = event_bus or TaskEventBus()
        self.scheduler = scheduler or TaskScheduler()
        self.permission_manager = permission_manager
        self.goal_manager = goal_manager
        self._tasks: dict[str, Task] = {}
        self._lock = threading.RLock()

    # Lifecycle operations

    def enqueue(self, task: Task) -> Task:
        with self._lock:
            if task.id in self._tasks:
                raise ValueError(f"Duplicate task id: {task.id}")
            if self._has_cycle(task):
                raise DependencyCycleError(f"Task {task.id} introduces a dependency cycle")
            old_status = task.status
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                keep_status = task.status
            elif task.scheduled_time:
                task.status = TaskStatus.WAITING
                keep_status = False
            else:
                task.status = TaskStatus.READY if self._deps_satisfied(task) else TaskStatus.BLOCKED
                keep_status = False
            self.storage.upsert(task)
            self._tasks[task.id] = task
            if old_status != task.status:
                self._publish(TaskEventType.CREATED, task.id, TaskSerializer.to_dict(task))
            return task

    def dequeue(self) -> Optional[Task]:
        with self._lock:
            ready = self._ready()
            if not ready:
                return None
            task = min(ready, key=lambda t: (TaskQueue._priority_rank(t.priority), t.created_at))
            task.status = TaskStatus.RUNNING
            task.started_at = TaskQueue._now()
            self.storage.upsert(task)
            self._tasks[task.id] = task
            self._publish(TaskEventType.STARTED, task.id, TaskSerializer.to_dict(task))
            return task

    def pause(self, task_id: str) -> Optional[Task]:
        with self._lock:
            task = self._require(task_id)
            if task.status != TaskStatus.RUNNING:
                return task
            task.status = TaskStatus.WAITING
            task.touch()
            self.storage.upsert(task)
            self._publish(TaskEventType.PAUSED, task.id, TaskSerializer.to_dict(task))
            return task

    def resume(self, task_id: str) -> Optional[Task]:
        with self._lock:
            task = self._require(task_id)
            if task.status != TaskStatus.WAITING:
                return task
            if not self._deps_satisfied(task):
                task.status = TaskStatus.BLOCKED
            else:
                task.status = TaskStatus.READY
            task.touch()
            self.storage.upsert(task)
            self._publish(TaskEventType.RESUMED, task.id, TaskSerializer.to_dict(task))
            return task

    def cancel(self, task_id: str) -> Optional[Task]:
        with self._lock:
            task = self._require(task_id)
            if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
                return task
            task.status = TaskStatus.CANCELLED
            task.finished_at = TaskQueue._now()
            task.touch()
            self.storage.upsert(task)
            self._publish(TaskEventType.CANCELLED, task.id, TaskSerializer.to_dict(task))
            return task

    def retry(self, task_id: str) -> Optional[Task]:
        with self._lock:
            task = self._require(task_id)
            if task.status != TaskStatus.FAILED:
                return task
            if task.retry_count >= task.max_retries:
                return task
            task.retry_count += 1
            task.status = TaskStatus.READY if self._deps_satisfied(task) else TaskStatus.BLOCKED
            task.error = None
            task.result = None
            task.touch()
            self.storage.upsert(task)
            self._publish(TaskEventType.RETRIED, task.id, TaskSerializer.to_dict(task))
            return task

    def complete(self, task_id: str, result: Any = None) -> Optional[Task]:
        with self._lock:
            task = self._require(task_id)
            if task.status == TaskStatus.COMPLETED:
                return task
            task.status = TaskStatus.COMPLETED
            task.finished_at = TaskQueue._now()
            task.result = result
            task.touch()
            self.storage.upsert(task)
            self._publish(TaskEventType.COMPLETED, task.id, TaskSerializer.to_dict(task))
            self._update_goal_on_completion(task)
            self._release_dependents(task)
            return task

    def fail(self, task_id: str, error: Optional[str] = None) -> Optional[Task]:
        with self._lock:
            task = self._require(task_id)
            if task.status == TaskStatus.FAILED:
                return task
            task.status = TaskStatus.FAILED
            task.finished_at = TaskQueue._now()
            task.error = error
            task.touch()
            self.storage.upsert(task)
            self._publish(TaskEventType.FAILED, task.id, TaskSerializer.to_dict(task))
            return task

    def requeue_failed(self, task_id: str, *, max_retries: Optional[int] = None) -> Optional[Task]:
        with self._lock:
            task = self._require(task_id)
            if task.status != TaskStatus.FAILED:
                return task
            if max_retries is not None:
                task.max_retries = int(max_retries)
            task.retry_count = 0
            task.status = TaskStatus.BLOCKED if not self._deps_satisfied(task) else TaskStatus.READY
            task.error = None
            task.result = None
            task.touch()
            self.storage.upsert(task)
            self._publish(TaskEventType.RETRIED, task.id, TaskSerializer.to_dict(task))
            return task

    # Query helpers

    def get_queue(self) -> list[Task]:
        with self._lock:
            return [t for t in self._tasks.values() if t.status in (TaskStatus.READY, TaskStatus.BLOCKED, TaskStatus.WAITING)]

    def get_running_tasks(self) -> list[Task]:
        with self._lock:
            seen: set[str] = set()
            combined: list[Task] = []
            for t in list(self._tasks.values()) + self.storage.load_by_status(TaskStatus.RUNNING):
                if t.id not in seen and t.status == TaskStatus.RUNNING:
                    seen.add(t.id)
                    combined.append(t)
            return combined

    def get_failed_tasks(self) -> list[Task]:
        with self._lock:
            seen: set[str] = set()
            combined: list[Task] = []
            for t in list(self._tasks.values()) + self.storage.load_by_status(TaskStatus.FAILED):
                if t.id not in seen and t.status == TaskStatus.FAILED:
                    seen.add(t.id)
                    combined.append(t)
            return combined

    def get_history(self) -> list[Task]:
        with self._lock:
            history: list[Task] = [
                t for t in self._tasks.values() if t.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED)
            ]
            seen: set[str] = {t.id for t in history}
            for status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED):
                for t in self.storage.load_by_status(status):
                    if t.id not in seen:
                        history.append(t)
                        seen.add(t.id)
            return history

    def get_upcoming(self, limit: int = 50) -> list[Task]:
        with self._lock:
            candidates = [
                t for t in self._tasks.values()
                if t.status in (TaskStatus.READY, TaskStatus.BLOCKED, TaskStatus.WAITING)
            ]
            candidates.sort(key=lambda t: (TaskQueue._priority_rank(t.priority), t.created_at))
            return candidates[:limit]

    def load_active(self) -> list[Task]:
        with self._lock:
            return [self._tasks[tid] for tid in sorted(self._tasks)]

    def recover(self) -> None:
        with self._lock:
            for task in self.storage.all():
                self._tasks[task.id] = task
                if task.status in (TaskStatus.RUNNING, TaskStatus.WAITING):
                    task.status = TaskStatus.READY if self._deps_satisfied(task) else TaskStatus.BLOCKED
                    self.storage.upsert(task)

    # Scheduler tick

    def tick(self) -> None:
        with self._lock:
            now = TaskQueue._now()
            for task in list(self._tasks.values()):
                if task.status == TaskStatus.WAITING and task.scheduled_time and task.scheduled_time <= now and self._deps_satisfied(task):
                    task.status = TaskStatus.READY
                    self.storage.upsert(task)

    # Internal helpers

    def _require(self, task_id: str) -> Task:
        task = self._tasks.get(task_id) or self.storage.load(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def _ready(self) -> list[Task]:
        tasks = [t for t in self._tasks.values() if t.status == TaskStatus.READY]
        tasks.sort(key=lambda t: (TaskQueue._priority_rank(t.priority), t.created_at))
        return tasks

    def _deps_satisfied(self, task: Task) -> bool:
        for dep_id in task.depends_on:
            dep = self._tasks.get(dep_id) or self.storage.load(dep_id)
            if dep is None or dep.status != TaskStatus.COMPLETED:
                return False
        return True

    def _has_cycle(self, new_task: Task) -> bool:
        ids = {t.id for t in self._tasks.values()} | {new_task.id}
        adj = {tid: [] for tid in ids}
        for t in list(self._tasks.values()) + [new_task]:
            for dep in t.depends_on:
                if dep in adj:
                    adj[t.id].append(dep)

        WHITE, GRAY, BLACK = 0, 1, 2
        color = {tid: WHITE for tid in ids}

        def dfs(node: str) -> bool:
            color[node] = GRAY
            for nb in adj.get(node, []):
                if color.get(nb) == GRAY:
                    return True
                if color.get(nb) == WHITE and dfs(nb):
                    return True
            color[node] = BLACK
            return False

        return any(dfs(n) for n in ids if color[n] == WHITE)

    def _release_dependents(self, completed_task: Task) -> None:
        for task in list(self._tasks.values()):
            if completed_task.id in task.depends_on and self._deps_satisfied(task):
                if task.status == TaskStatus.BLOCKED:
                    task.status = TaskStatus.READY
                    task.touch()
                    self.storage.upsert(task)

    def _update_goal_on_completion(self, task: Task) -> None:
        goal_manager = self.goal_manager
        if goal_manager is None or not task.goal_id:
            return
        try:
            goal = goal_manager.get(task.goal_id)
            if goal is None:
                return
            total_goals = 0
            completed_goals = 0
            counted: set[str] = set()
            for t in list(self._tasks.values()):
                if t.goal_id == goal.id and t.step_id not in counted:
                    counted.add(t.step_id)
                    total_goals += 1
                    if t.status == TaskStatus.COMPLETED:
                        completed_goals += 1
            goal_manager.update_progress(goal.id, completed_goals, total_goals)
        except Exception:
            pass

    def _publish(self, event_type: TaskEventType, task_id: str, payload: dict[str, Any]) -> None:
        try:
            self.event_bus.publish(TaskEvent(event_type, task_id, payload))
        except Exception:
            pass

    # Priority helpers

    @staticmethod
    def _priority_rank(priority: TaskPriority) -> int:
        return {TaskPriority.CRITICAL: 0, TaskPriority.HIGH: 1, TaskPriority.NORMAL: 2, TaskPriority.LOW: 3}.get(priority, 2)

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def _is_future(scheduled_time: Optional[str]) -> bool:
        if not scheduled_time:
            return False
        now = TaskQueue._now()
        try:
            return scheduled_time > now
        except Exception:
            return False
