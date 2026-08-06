"""Workspace history persistence."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from workspace.state import WorkspaceSnapshot, WorkspaceHistoryEntry, ProjectContext


class WorkspaceHistory:
    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        if db_path is None:
            db_path = Path(__file__).resolve().parent.parent / "data" / "workspace.sqlite"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                snapshot_id TEXT PRIMARY KEY,
                timestamp TEXT,
                active_application TEXT,
                open_applications TEXT,
                open_windows TEXT,
                active_project TEXT,
                working_directory TEXT,
                git_repository TEXT,
                open_files TEXT,
                terminal_path TEXT,
                browser_domains TEXT,
                clipboard_hash TEXT,
                confidence REAL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS history (
                entry_id TEXT PRIMARY KEY,
                timestamp TEXT,
                snapshot_id TEXT,
                project_name TEXT,
                project_path TEXT,
                project_language TEXT,
                project_git_repo TEXT,
                project_ide TEXT,
                event_type TEXT,
                metadata TEXT
            )
            """
        )
        self._conn.commit()

    def save_snapshot(self, snapshot: WorkspaceSnapshot) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO snapshots (
                    snapshot_id, timestamp, active_application, open_applications, open_windows,
                    active_project, working_directory, git_repository, open_files,
                    terminal_path, browser_domains, clipboard_hash, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.timestamp,
                    snapshot.active_application,
                    json.dumps(snapshot.open_applications),
                    json.dumps(snapshot.open_windows),
                    snapshot.active_project,
                    snapshot.working_directory,
                    snapshot.git_repository,
                    json.dumps(snapshot.open_files),
                    snapshot.terminal_path,
                    json.dumps(snapshot.browser_domains),
                    snapshot.clipboard_hash,
                    snapshot.confidence,
                ),
            )
            self._conn.commit()

    def save_entry(self, entry: WorkspaceHistoryEntry) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO history (
                    entry_id, timestamp, snapshot_id, project_name, project_path,
                    project_language, project_git_repo, project_ide, event_type, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.entry_id,
                    entry.timestamp,
                    entry.snapshot.snapshot_id if entry.snapshot else None,
                    entry.project.name if entry.project else None,
                    entry.project.path if entry.project else None,
                    entry.project.language if entry.project else None,
                    entry.project.git_repo if entry.project else None,
                    entry.project.ide if entry.project else None,
                    entry.event_type,
                    json.dumps(entry.metadata),
                ),
            )
            self._conn.commit()

    def recent_snapshots(self, limit: int = 20) -> list[WorkspaceSnapshot]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM snapshots ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for row in rows:
            s = WorkspaceSnapshot(
                snapshot_id=row["snapshot_id"],
                timestamp=row["timestamp"],
                active_application=row["active_application"],
                open_applications=json.loads(row["open_applications"] or "[]"),
                open_windows=json.loads(row["open_windows"] or "[]"),
                active_project=row["active_project"],
                working_directory=row["working_directory"],
                git_repository=row["git_repository"],
                open_files=json.loads(row["open_files"] or "[]"),
                terminal_path=row["terminal_path"],
                browser_domains=json.loads(row["browser_domains"] or "[]"),
                clipboard_hash=row["clipboard_hash"],
                confidence=row["confidence"] or 0.0,
            )
            out.append(s)
        return out

    def recent_projects(self, limit: int = 20) -> list[ProjectContext]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT project_name, project_path, project_language, project_git_repo, project_ide FROM history WHERE project_name != '' ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for row in rows:
            out.append(
                ProjectContext(
                    name=row["project_name"],
                    path=row["project_path"],
                    language=row["project_language"],
                    git_repo=row["project_git_repo"],
                    ide=row["project_ide"],
                )
            )
        return out

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
