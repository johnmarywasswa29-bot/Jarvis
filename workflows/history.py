"""WorkflowHistory: persistence for workflows and steps."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any, Optional

from workflows.state import WorkflowState, WorkflowStep, StepStatus


class WorkflowHistory:
    def __init__(self, path: str | Path = "workflows/workflows.sqlite") -> None:
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

    def __enter__(self) -> "WorkflowHistory":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _migrate(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    current_step_index INTEGER NOT NULL DEFAULT 0,
                    context TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_steps (
                    uuid TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    intent TEXT NOT NULL DEFAULT '',
                    tool TEXT NOT NULL DEFAULT '',
                    parameters TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    execution_time_s REAL NOT NULL DEFAULT 0.0,
                    dependencies TEXT NOT NULL DEFAULT '[]',
                    result TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (workflow_id) REFERENCES workflows(workflow_id)
                )
                """
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS workflow_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT, ts TEXT NOT NULL, level TEXT NOT NULL DEFAULT 'info', message TEXT NOT NULL DEFAULT '')"
            )

    def save_workflow(self, state: WorkflowState) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO workflows (workflow_id, name, description, status, current_step_index, context, created_at, updated_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.workflow_id,
                    state.name,
                    state.description,
                    state.status.value,
                    state.current_step_index,
                    json.dumps(dict(state.context)),
                    state.created_at,
                    state.updated_at,
                    json.dumps(dict(state.metadata)),
                ),
            )
            for step in state.steps:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO workflow_steps (uuid, workflow_id, description, intent, tool, parameters, status, retry_count, execution_time_s, dependencies, result, error, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        step.uuid,
                        state.workflow_id,
                        step.description,
                        step.intent,
                        step.tool,
                        json.dumps(dict(step.parameters)),
                        step.status.value,
                        int(step.retry_count),
                        float(step.execution_time_s),
                        json.dumps(list(step.dependencies)),
                        json.dumps(dict(step.result) if step.result else {}),
                        step.error,
                        step.created_at,
                        step.updated_at,
                    ),
                )

    def load_workflow(self, workflow_id: str) -> Optional[WorkflowState]:
        with self._lock:
            wf = self._conn.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
            if not wf:
                return None
            steps = []
            rows = self._conn.execute("SELECT * FROM workflow_steps WHERE workflow_id = ? ORDER BY created_at", (workflow_id,)).fetchall()
            for row in rows:
                steps.append(
                    WorkflowStep(
                        uuid=row["uuid"],
                        description=row["description"],
                        intent=row["intent"],
                        tool=row["tool"],
                        parameters=json.loads(row["parameters"] or "{}"),
                        status=StepStatus(row["status"]),
                        retry_count=int(row["retry_count"] or 0),
                        execution_time_s=float(row["execution_time_s"] or 0.0),
                        dependencies=json.loads(row["dependencies"] or "[]"),
                        result=json.loads(row["result"] or "{}") or None,
                        error=row["error"] or "",
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                )
            return WorkflowState(
                workflow_id=wf["workflow_id"],
                name=wf["name"],
                description=wf["description"],
                steps=steps,
                status=StepStatus(wf["status"]),
                current_step_index=int(wf["current_step_index"] or 0),
                context=json.loads(wf["context"] or "{}"),
                created_at=wf["created_at"],
                updated_at=wf["updated_at"],
                metadata=json.loads(wf["metadata"] or "{}"),
            )

    def list_workflows(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT workflow_id, name, status, created_at, updated_at FROM workflows ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(row) for row in rows]

    def delete_workflow(self, workflow_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM workflow_steps WHERE workflow_id = ?", (workflow_id,))
            self._conn.execute("DELETE FROM workflows WHERE workflow_id = ?", (workflow_id,))

    def log(self, workflow_id: str, level: str, message: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO workflow_logs (workflow_id, ts, level, message) VALUES (?, ?, ?, ?)", (workflow_id, datetime.now(UTC).replace(tzinfo=None).isoformat(), level, message))

    def recent_logs(self, workflow_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT ts, level, message FROM workflow_logs WHERE workflow_id = ? ORDER BY ts DESC LIMIT ?", (workflow_id, limit)).fetchall()
            return [dict(row) for row in rows]
