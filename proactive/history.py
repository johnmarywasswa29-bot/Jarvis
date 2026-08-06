"""Proactive persistence: suggestions, triggers, queue, history, dismissal memory."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, UTC
from pathlib import Path
from typing import Optional

from proactive.state import Suggestion, Trigger, NotificationQueueItem


class ProactiveHistory:
    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        if db_path is None:
            db_path = Path(__file__).resolve().parent.parent / "data" / "proactive.sqlite"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS suggestions (
                suggestion_id TEXT PRIMARY KEY,
                category TEXT,
                title TEXT,
                body TEXT,
                priority REAL,
                confidence REAL,
                urgency REAL,
                context_relevance REAL,
                expected_usefulness REAL,
                dismissed INTEGER,
                dismissed_at TEXT,
                created_at TEXT,
                metadata TEXT
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS triggers (
                trigger_id TEXT PRIMARY KEY,
                name TEXT,
                category TEXT,
                condition TEXT,
                cooldown_s REAL,
                last_fired TEXT,
                enabled INTEGER,
                metadata TEXT
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_queue (
                item_id TEXT PRIMARY KEY,
                suggestion_id TEXT,
                status TEXT,
                created_at TEXT,
                delivered_at TEXT
            )
            """
        )
        self._conn.commit()

    def save_suggestion(self, suggestion: Suggestion) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO suggestions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    suggestion.suggestion_id,
                    suggestion.category,
                    suggestion.title,
                    suggestion.body,
                    suggestion.priority,
                    suggestion.confidence,
                    suggestion.urgency,
                    suggestion.context_relevance,
                    suggestion.expected_usefulness,
                    int(suggestion.dismissed),
                    suggestion.dismissed_at,
                    suggestion.created_at,
                    json.dumps(suggestion.metadata),
                ),
            )
            self._conn.commit()

    def save_trigger(self, trigger: Trigger) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO triggers VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trigger.trigger_id,
                    trigger.name,
                    trigger.category,
                    trigger.condition,
                    trigger.cooldown_s,
                    trigger.last_fired,
                    int(trigger.enabled),
                    json.dumps(trigger.metadata),
                ),
            )
            self._conn.commit()

    def enqueue(self, item: NotificationQueueItem) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO notification_queue VALUES (?, ?, ?, ?, ?)
                """,
                (
                    item.item_id,
                    item.suggestion.suggestion_id if item.suggestion else None,
                    item.status,
                    item.created_at,
                    item.delivered_at,
                ),
            )
            self._conn.commit()

    def recent_suggestions(self, limit: int = 50) -> list[Suggestion]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM suggestions ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for row in rows:
            out.append(
                Suggestion(
                    suggestion_id=row["suggestion_id"],
                    category=row["category"],
                    title=row["title"],
                    body=row["body"],
                    priority=row["priority"],
                    confidence=row["confidence"],
                    urgency=row["urgency"],
                    context_relevance=row["context_relevance"],
                    expected_usefulness=row["expected_usefulness"],
                    dismissed=bool(row["dismissed"]),
                    dismissed_at=row["dismissed_at"],
                    created_at=row["created_at"],
                    metadata=json.loads(row["metadata"] or "{}"),
                )
            )
        return out

    def dismiss(self, suggestion_id: str) -> None:
        with self._lock:
            now = datetime.now(UTC).replace(tzinfo=None).isoformat()
            self._conn.execute(
                "UPDATE suggestions SET dismissed=1, dismissed_at=? WHERE suggestion_id=?",
                (now, suggestion_id),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
