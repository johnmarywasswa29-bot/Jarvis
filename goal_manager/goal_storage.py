"""Goal Storage: SQLite persistence backend."""
from __future__ import annotations

import atexit
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

from goal_manager.goal_serializer import GoalSerializer


class GoalStorage:
    def __init__(self, db_path: str | Path = "data/goals.sqlite") -> None:
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
                    CREATE TABLE IF NOT EXISTS goals (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        description TEXT DEFAULT '',
                        status TEXT NOT NULL,
                        priority TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        completed_at TEXT,
                        progress REAL NOT NULL DEFAULT 0,
                        owner TEXT DEFAULT '',
                        category TEXT DEFAULT '',
                        tags TEXT DEFAULT '',
                        notes TEXT DEFAULT '',
                        depends_on TEXT DEFAULT '',
                        plans TEXT DEFAULT '[]'
                    )
                    """
                )
                conn.commit()

    def upsert(self, goal: Any) -> None:
        data = GoalSerializer.to_dict(goal)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO goals (
                        id, title, description, status, priority, created_at, updated_at,
                        completed_at, progress, owner, category, tags, notes, depends_on, plans
                    ) VALUES (
                        :id, :title, :description, :status, :priority, :created_at, :updated_at,
                        :completed_at, :progress, :owner, :category, :tags, :notes, :depends_on, :plans
                    )
                    ON CONFLICT(id) DO UPDATE SET
                        title=excluded.title,
                        description=excluded.description,
                        status=excluded.status,
                        priority=excluded.priority,
                        updated_at=excluded.updated_at,
                        completed_at=excluded.completed_at,
                        progress=excluded.progress,
                        owner=excluded.owner,
                        category=excluded.category,
                        tags=excluded.tags,
                        notes=excluded.notes,
                        depends_on=excluded.depends_on,
                        plans=excluded.plans
                    """,
                    {
                        "id": data["id"],
                        "title": data["title"],
                        "description": data["description"],
                        "status": data["status"],
                        "priority": data["priority"],
                        "created_at": data["created_at"],
                        "updated_at": data["updated_at"],
                        "completed_at": data["completed_at"],
                        "progress": data["progress"],
                        "owner": data["owner"],
                        "category": data["category"],
                        "tags": ",".join(data.get("tags", [])),
                        "notes": data.get("notes", ""),
                        "depends_on": ",".join(data.get("depends_on", [])),
                        "plans": json.dumps(data.get("plans", [])),
                    },
                )
                conn.commit()

    def delete(self, goal_id: str) -> bool:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
                conn.commit()
                return cur.rowcount > 0

    def exists(self, goal_id: str) -> bool:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("SELECT 1 FROM goals WHERE id = ?", (goal_id,))
                return cur.fetchone() is not None

    def load(self, goal_id: str) -> Optional[Any]:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,))
                row = cur.fetchone()
                if not row:
                    return None
                return GoalSerializer.from_dict(self._row_to_dict(dict(row)))

    def load_active(self) -> list[Any]:
        goals: list[Any] = []
        with self._lock:
            with self._connect() as conn:
                for row in conn.execute("SELECT * FROM goals WHERE status = 'active'"):
                    goals.append(GoalSerializer.from_dict(self._row_to_dict(dict(row))))
        return goals

    def load_completed(self) -> list[Any]:
        goals: list[Any] = []
        with self._lock:
            with self._connect() as conn:
                for row in conn.execute("SELECT * FROM goals WHERE status = 'completed'"):
                    goals.append(GoalSerializer.from_dict(self._row_to_dict(dict(row))))
        return goals

    def search(self, query: str) -> Iterable[Any]:
        with self._lock:
            q = f"%{query.lower()}%"
            with self._connect() as conn:
                cur = conn.execute(
                    "SELECT * FROM goals WHERE lower(title) LIKE ? OR lower(description) LIKE ? OR lower(category) LIKE ? OR lower(notes) LIKE ?",
                    (q, q, q, q),
                )
                for row in cur:
                    yield GoalSerializer.from_dict(self._row_to_dict(dict(row)))

    def all(self) -> Iterable[Any]:
        with self._lock:
            with self._connect() as conn:
                for row in conn.execute("SELECT * FROM goals"):
                    yield GoalSerializer.from_dict(self._row_to_dict(dict(row)))

    def _row_to_dict(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "priority": row["priority"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
            "progress": float(row["progress"]),
            "owner": row["owner"],
            "category": row["category"],
            "tags": row["tags"].split(",") if row["tags"] else [],
            "notes": row["notes"],
            "depends_on": row["depends_on"].split(",") if row["depends_on"] else [],
            "plans": json.loads(row["plans"]) if row["plans"] else [],
        }
