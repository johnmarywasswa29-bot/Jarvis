"""HabitStore: SQLite persistence for learned habits with schema, CRUD, and decay."""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4


@dataclass
class Habit:
    uuid: str = field(default_factory=lambda: uuid4().hex)
    name: str = ""
    confidence: float = 0.0
    frequency: int = 0
    recency: float = 0.0
    avg_duration_s: float = 0.0
    associated_apps: list[str] = field(default_factory=list)
    associated_documents: list[str] = field(default_factory=list)
    associated_folders: list[str] = field(default_factory=list)
    associated_intents: list[str] = field(default_factory=list)
    success_rate: float = 1.0
    last_executed: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())
    created_at: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


class HabitStore:
    def __init__(self, path: str | Path = "habits/habits.sqlite") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def __enter__(self) -> "HabitStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _migrate(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS habits (
                    uuid TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    frequency INTEGER NOT NULL,
                    recency REAL NOT NULL,
                    avg_duration_s REAL NOT NULL,
                    associated_apps TEXT NOT NULL DEFAULT '[]',
                    associated_documents TEXT NOT NULL DEFAULT '[]',
                    associated_folders TEXT NOT NULL DEFAULT '[]',
                    associated_intents TEXT NOT NULL DEFAULT '[]',
                    success_rate REAL NOT NULL DEFAULT 1.0,
                    last_executed TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}')"
            )

    def add_habit(self, habit: Habit) -> str:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO habits (
                    uuid, name, confidence, frequency, recency, avg_duration_s,
                    associated_apps, associated_documents, associated_folders,
                    associated_intents, success_rate, last_executed, created_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    habit.uuid,
                    habit.name,
                    float(habit.confidence),
                    int(habit.frequency),
                    float(habit.recency),
                    float(habit.avg_duration_s),
                    json.dumps(list(habit.associated_apps)),
                    json.dumps(list(habit.associated_documents)),
                    json.dumps(list(habit.associated_folders)),
                    json.dumps(list(habit.associated_intents)),
                    float(habit.success_rate),
                    habit.last_executed,
                    habit.created_at,
                    json.dumps(dict(habit.metadata)),
                ),
            )
        return habit.uuid

    def get_habit(self, uuid: str) -> Optional[Habit]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM habits WHERE uuid = ?", (uuid,)).fetchone()
            if not row:
                return None
            return self._row_to_habit(row)

    def list_habits(self) -> list[Habit]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM habits ORDER BY confidence DESC, last_executed DESC").fetchall()
            return [self._row_to_habit(r) for r in rows]

    def update_habit(self, habit: Habit) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE habits SET name = ?, confidence = ?, frequency = ?, recency = ?, avg_duration_s = ?,
                associated_apps = ?, associated_documents = ?, associated_folders = ?, associated_intents = ?,
                success_rate = ?, last_executed = ?, metadata = ? WHERE uuid = ?
                """,
                (
                    habit.name,
                    float(habit.confidence),
                    int(habit.frequency),
                    float(habit.recency),
                    float(habit.avg_duration_s),
                    json.dumps(list(habit.associated_apps)),
                    json.dumps(list(habit.associated_documents)),
                    json.dumps(list(habit.associated_folders)),
                    json.dumps(list(habit.associated_intents)),
                    float(habit.success_rate),
                    habit.last_executed,
                    json.dumps(dict(habit.metadata)),
                    habit.uuid,
                ),
            )

    def delete_habit(self, uuid: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM habits WHERE uuid = ?", (uuid,))

    def record_event(self, kind: str, payload: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
                (datetime.now(UTC).replace(tzinfo=None).isoformat(), kind, json.dumps(payload)),
            )

    def recent_events(self, since: Optional[datetime] = None, limit: int = 5000) -> list[dict[str, Any]]:
        with self._lock:
            if since is None:
                since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
            rows = self._conn.execute(
                "SELECT ts, kind, payload FROM events WHERE ts > ? ORDER BY ts DESC LIMIT ?",
                (since.isoformat(), limit),
            ).fetchall()
            out = []
            for row in rows:
                try:
                    p = json.loads(row["payload"])
                except Exception:
                    p = {}
                out.append({"ts": row["ts"], "kind": row["kind"], "payload": p})
            return out

    def prune_events(self, older_than: datetime) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM events WHERE ts < ?", (older_than.isoformat(),))
            return cur.rowcount

    def _row_to_habit(self, row: sqlite3.Row) -> Habit:
        return Habit(
            uuid=row["uuid"],
            name=row["name"],
            confidence=float(row["confidence"] or 0.0),
            frequency=int(row["frequency"] or 0),
            recency=float(row["recency"] or 0.0),
            avg_duration_s=float(row["avg_duration_s"] or 0.0),
            associated_apps=json.loads(row["associated_apps"] or "[]"),
            associated_documents=json.loads(row["associated_documents"] or "[]"),
            associated_folders=json.loads(row["associated_folders"] or "[]"),
            associated_intents=json.loads(row["associated_intents"] or "[]"),
            success_rate=float(row["success_rate"] or 1.0),
            last_executed=row["last_executed"],
            created_at=row["created_at"],
            metadata=json.loads(row["metadata"] or "{}"),
        )
