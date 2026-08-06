"""Goal Serializer."""
from __future__ import annotations

from typing import Any

from goal_manager.goal import Goal, GoalStatus, GoalPriority, PlanAttachment


class GoalSerializer:
    @staticmethod
    def to_dict(goal: Goal) -> dict[str, Any]:
        return {
            "id": goal.id,
            "title": goal.title,
            "description": goal.description,
            "status": str(goal.status.value),
            "priority": str(goal.priority.value),
            "created_at": goal.created_at,
            "updated_at": goal.updated_at,
            "completed_at": goal.completed_at,
            "progress": goal.progress,
            "owner": goal.owner,
            "category": goal.category,
            "tags": list(goal.tags),
            "notes": goal.notes,
            "depends_on": list(goal.depends_on),
            "plans": [
                {
                    "plan_id": p.plan_id,
                    "plan_dict": dict(p.plan_dict),
                    "attached_at": p.attached_at,
                }
                for p in goal.plans
            ],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Goal:
        goal = Goal(
            title=data["title"],
            description=data.get("description", ""),
            status=data.get("status", GoalStatus.ACTIVE),
            priority=data.get("priority", GoalPriority.NORMAL),
            owner=data.get("owner", ""),
            category=data.get("category", ""),
            tags=data.get("tags", []),
            notes=data.get("notes", ""),
            goal_id=data.get("id"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            completed_at=data.get("completed_at"),
            progress=data.get("progress", 0.0),
            depends_on=data.get("depends_on", []),
        )
        for p in data.get("plans", []):
            goal.attach_plan(PlanAttachment(plan_id=p["plan_id"], plan_dict=p.get("plan_dict", {}), attached_at=p.get("attached_at", "")))
        return goal
