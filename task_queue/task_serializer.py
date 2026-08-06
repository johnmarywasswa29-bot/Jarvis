"""Task serializer."""
from __future__ import annotations

from typing import Any

from task_queue.task import Task, TaskPriority, TaskStatus


class TaskSerializer:
    @staticmethod
    def to_dict(task: Task) -> dict[str, Any]:
        return {
            "id": task.id,
            "goal_id": task.goal_id,
            "plan_id": task.plan_id,
            "step_id": task.step_id,
            "title": task.title,
            "description": task.description,
            "status": str(task.status.value if hasattr(task.status, "value") else task.status),
            "priority": str(task.priority.value if hasattr(task.priority, "value") else task.priority),
            "created_at": task.created_at,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "scheduled_time": task.scheduled_time,
            "retry_count": task.retry_count,
            "max_retries": task.max_retries,
            "estimated_duration": task.estimated_duration,
            "actual_duration": task.actual_duration,
            "depends_on": list(task.depends_on),
            "tool": task.tool,
            "arguments": dict(task.arguments),
            "result": task.result,
            "error": task.error,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Task:
        return Task(
            id=data["id"],
            goal_id=data.get("goal_id", ""),
            plan_id=data.get("plan_id", ""),
            step_id=data.get("step_id", ""),
            title=data["title"],
            description=data.get("description", ""),
            status=TaskStatus(str(data.get("status", TaskStatus.PENDING))) if not isinstance(data.get("status"), TaskStatus) else data.get("status"),
            priority=TaskPriority(str(data.get("priority", TaskPriority.NORMAL))) if not isinstance(data.get("priority"), TaskPriority) else data.get("priority"),
            created_at=data.get("created_at"),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            scheduled_time=data.get("scheduled_time"),
            retry_count=int(data.get("retry_count", 0)),
            max_retries=int(data.get("max_retries", 3)),
            estimated_duration=float(data.get("estimated_duration", 0)),
            actual_duration=float(data.get("actual_duration", 0)),
            depends_on=data.get("depends_on", []),
            tool=data.get("tool", ""),
            arguments=data.get("arguments", {}),
            result=data.get("result"),
            error=data.get("error"),
        )
