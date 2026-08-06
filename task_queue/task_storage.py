"""Task persistence layer using SQLite."""
from __future__ import annotations

import atexit
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

from task_queue.task_serializer import TaskSerializer


class TaskStorage:
    def __init__(self, db_path: str | Path = "data/tasks.sqlite") -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()
        atexit.register(self.close)

    def close(self) -> None:
        try:
            conn = getattr(self, "_conn", None)
            if conn is not None:
                conn.close()
                self._conn = None
        except Exception:
            pass

    def _connect(self) -> sqlite3.Connection:
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tasks (
                        id TEXT PRIMARY KEY,
                        goal_id TEXT DEFAULT '',
                        plan_id TEXT DEFAULT '',
                        step_id TEXT DEFAULT '',
                        title TEXT NOT NULL,
                        description TEXT DEFAULT '',
                        status TEXT NOT NULL,
                        priority TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        finished_at TEXT,
                        scheduled_time TEXT,
                        retry_count INTEGER NOT NULL DEFAULT 0,
                        max_retries INTEGER NOT NULL DEFAULT 3,
                        estimated_duration REAL NOT NULL DEFAULT 0,
                        actual_duration REAL NOT NULL DEFAULT 0,
                        depends_on TEXT DEFAULT '',
                        tool TEXT DEFAULT '',
                        arguments TEXT DEFAULT '',
                        result TEXT DEFAULT '',
                        error TEXT DEFAULT ''
                    )
                    """
                )
                conn.commit()

    def upsert(self, task: Any) -> None:
        data = TaskSerializer.to_dict(task)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO tasks (
                        id, goal_id, plan_id, step_id, title, description, status, priority,
                        created_at, started_at, finished_at, scheduled_time, retry_count,
                        max_retries, estimated_duration, actual_duration, depends_on, tool,
                        arguments, result, error
                    ) VALUES (
                        :id, :goal_id, :plan_id, :step_id, :title, :description, :status, :priority,
                        :created_at, :started_at, :finished_at, :scheduled_time, :retry_count,
                        :max_retries, :estimated_duration, :actual_duration, :depends_on, :tool,
                        :arguments, :result, :error
                    )
                    ON CONFLICT(id) DO UPDATE SET
                        goal_id=excluded.goal_id,
                        plan_id=excluded.plan_id,
                        step_id=excluded.step_id,
                        title=excluded.title,
                        description=excluded.description,
                        status=excluded.status,
                        priority=excluded.priority,
                        started_at=excluded.started_at,
                        finished_at=excluded.finished_at,
                        scheduled_time=excluded.scheduled_time,
                        retry_count=excluded.retry_count,
                        max_retries=excluded.max_retries,
                        estimated_duration=excluded.estimated_duration,
                        actual_duration=excluded.actual_duration,
                        depends_on=excluded.depends_on,
                        tool=excluded.tool,
                        arguments=excluded.arguments,
                        result=excluded.result,
                        error=excluded.error
                    """,
                    {
                        "id": data["id"],
                        "goal_id": data.get("goal_id", ""),
                        "plan_id": data.get("plan_id", ""),
                        "step_id": data.get("step_id", ""),
                        "title": data["title"],
                        "description": data.get("description", ""),
                        "status": data["status"],
                        "priority": data["priority"],
                        "created_at": data["created_at"],
                        "started_at": data.get("started_at"),
                        "finished_at": data.get("finished_at"),
                        "scheduled_time": data.get("scheduled_time"),
                        "retry_count": int(data.get("retry_count", 0)),
                        "max_retries": int(data.get("max_retries", 3)),
                        "estimated_duration": float(data.get("estimated_duration", 0)),
                        "actual_duration": float(data.get("actual_duration", 0)),
                        "depends_on": ",".join(data.get("depends_on", [])),
                        "tool": data.get("tool", ""),
                        "arguments": json.dumps(data.get("arguments", {})),
                        "result": json.dumps(data.get("result")),
                        "error": data.get("error", ""),
                    },
                )
                conn.commit()

    def delete(self, task_id: str) -> bool:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                conn.commit()
                return cur.rowcount > 0

    def exists(self, task_id: str) -> bool:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,))
                return cur.fetchone() is not None

    def load(self, task_id: str) -> Optional[Any]:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
                row = cur.fetchone()
                if not row:
                    return None
                return TaskSerializer.from_dict(self._row_to_dict(dict(row)))

    def all(self) -> Iterable[Any]:
        with self._lock:
            with self._connect() as conn:
                for row in conn.execute("SELECT * FROM tasks"):
                    yield TaskSerializer.from_dict(self._row_to_dict(dict(row)))

    def load_by_status(self, status: str) -> list[Any]:
        tasks: list[Any] = []
        with self._lock:
            with self._connect() as conn:
                for row in conn.execute("SELECT * FROM tasks WHERE status = ?", (status,)):
                    tasks.append(TaskSerializer.from_dict(self._row_to_dict(dict(row))))
        return tasks

    def _row_to_dict(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "goal_id": row["goal_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "priority": row["priority"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "scheduled_time": row["scheduled_time"],
            "retry_count": int(row["retry_count"]),
            "max_retries": int(row["max_retries"]),
            "estimated_duration": float(row["estimated_duration"]),
            "actual_duration": float(row["actual_duration"]),
            "depends_on": row["depends_on"].split(",") if row["depends_on"] else [],
            "tool": row["tool"],
            "arguments": json.loads(row["arguments"]) if row["arguments"] else {},
            "result": json.loads(row["result"]) if row["result"] else None,
            "error": row["error"],
        }
