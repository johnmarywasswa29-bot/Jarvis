"""Jarvis Goal Manager: optional goals planning + tracking."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class GoalStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Goal:
    id: str
    title: str
    status: GoalStatus = GoalStatus.PENDING
    priority: int = 5
    steps: list[str] = field(default_factory=list)
    completed_steps: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class GoalManager:
    def __init__(self, persist_path: Optional[Path] = None) -> None:
        self._goals: dict[str, Goal] = {}
        self._lock = threading.Lock()
        self._persist_path = persist_path or Path("memory") / "goals.json"
        self._load()

    def create(self, title: str, steps: Optional[list[str]] = None, priority: int = 5, goal_id: Optional[str] = None, metadata: Optional[dict[str, Any]] = None) -> Goal:
        gid = goal_id or str(int(time.time() * 1000))
        goal = Goal(id=gid, title=title, steps=list(steps or []), priority=priority, metadata=dict(metadata or {}))
        with self._lock:
            self._goals[gid] = goal
        self._persist()
        return goal

    def get(self, goal_id: str) -> Optional[Goal]:
        with self._lock:
            g = self._goals.get(goal_id)
            if g is not None:
                return Goal(**g.__dict__)
            return None

    def update(self, goal_id: str, *, title: Optional[str] = None, status: Optional[GoalStatus] = None, priority: Optional[int] = None, steps: Optional[list[str]] = None, completed_steps: Optional[list[str]] = None, metadata: Optional[dict[str, Any]] = None) -> Optional[Goal]:
        with self._lock:
            g = self._goals.get(goal_id)
            if g is None:
                return None
            if title is not None:
                g.title = title
            if status is not None:
                g.status = status
            if priority is not None:
                g.priority = priority
            if steps is not None:
                g.steps = list(steps)
            if completed_steps is not None:
                g.completed_steps = list(completed_steps)
            if metadata is not None:
                g.metadata = dict(metadata)
            g.updated_at = time.time()
            out = Goal(**g.__dict__)
        self._persist()
        return out

    def complete_step(self, goal_id: str, step: str) -> Optional[Goal]:
        with self._lock:
            g = self._goals.get(goal_id)
            if g is None:
                return None
            if step not in g.completed_steps:
                g.completed_steps.append(step)
            if g.steps and all(s in g.completed_steps for s in g.steps):
                g.status = GoalStatus.DONE
            g.updated_at = time.time()
            out = Goal(**g.__dict__)
        self._persist()
        return out

    def list_active(self) -> list[Goal]:
        with self._lock:
            return [Goal(**g.__dict__) for g in self._goals.values() if g.status == GoalStatus.ACTIVE]

    def list_pending(self) -> list[Goal]:
        with self._lock:
            x =  [Goal(**g.__dict__) for g in self._goals.values() if g.status == GoalStatus.PENDING]
            return x

    def mark_active(self, goal_id: str) -> Optional[Goal]:
        return self.update(goal_id, status=GoalStatus.ACTIVE)

    def mark_done(self, goal_id: str) -> Optional[Goal]:
        return self.update(goal_id, status=GoalStatus.DONE)

    def to_context(self, max_goals: int = 5) -> str:
        items: list[Goal] = []
        with self._lock:
            items = sorted(self._goals.values(), key=lambda g: (g.priority, g.created_at), reverse=True)[:max_goals]
        lines = []
        for g in items:
            lines.append(f"- {g.title} [{g.status.value}] steps={len(g.completed_steps)}/{len(g.steps)}")
        return "\n".join(lines)

    def _load(self) -> None:
        if not self._persist_path.exists():
            return
        try:
            import json
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            with self._lock:
                for item in data:
                    goal = Goal(
                        id=item.get("id", ""),
                        title=item.get("title", ""),
                        status=GoalStatus(item.get("status", GoalStatus.PENDING.value)),
                        priority=int(item.get("priority", 5)),
                        steps=list(item.get("steps", [])),
                        completed_steps=list(item.get("completed_steps", [])),
                        metadata=dict(item.get("metadata", {})),
                        created_at=float(item.get("created_at", time.time())),
                        updated_at=float(item.get("updated_at", time.time())),
                    )
                    if goal.id:
                        self._goals[goal.id] = goal
        except Exception:
            pass

    def _persist(self) -> None:
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            data = []
            with self._lock:
                for g in self._goals.values():
                    data.append(
                        {
                            "id": g.id,
                            "title": g.title,
                            "status": g.status.value,
                            "priority": g.priority,
                            "steps": list(g.steps),
                            "completed_steps": list(g.completed_steps),
                            "metadata": dict(g.metadata),
                            "created_at": g.created_at,
                            "updated_at": g.updated_at,
                        }
                    )
            self._persist_path.write_text(json_dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


def json_dumps(obj, **kwargs):
    import json
    return json.dumps(obj, **kwargs)
