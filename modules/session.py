"""Session persistence and workspace switching."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


_DEFAULT_WORKSPACE = "default"
_DB_NAME = "jarvis_sessions.sqlite"


@dataclass
class Session:
    workspace: str = _DEFAULT_WORKSPACE
    goal_id: Optional[str] = None
    task_id: Optional[str] = None
    conversation_summary: str = ""
    panel: str = "chat"
    window_width: int = 1280
    window_height: int = 840
    window_x: Optional[int] = None
    window_y: Optional[int] = None
    theme: str = "dark"
    ollama_status: str = "unknown"
    voice_enabled: bool = False
    active_model: str = ""
    recent_files: list[str] = field(default_factory=list)
    recent_tools: list[str] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)


class SessionStore:
    def __init__(self, base_dir: Optional[str | Path] = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else Path(".jarvis")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base_dir / _DB_NAME
        self._lock = threading.RLock()
        self._init_schema()
        self._current_workspace = _DEFAULT_WORKSPACE
        self._current = Session()
        self._autosave_timer: Optional[threading.Timer] = None

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    workspace TEXT PRIMARY KEY,
                    goal_id TEXT,
                    task_id TEXT,
                    conversation_summary TEXT,
                    panel TEXT,
                    window_width INTEGER,
                    window_height INTEGER,
                    window_x INTEGER,
                    window_y INTEGER,
                    theme TEXT,
                    ollama_status TEXT,
                    voice_enabled INTEGER,
                    active_model TEXT,
                    recent_files TEXT,
                    recent_tools TEXT,
                    updated_at REAL
                );
                CREATE TABLE IF NOT EXISTS workspaces (
                    name TEXT PRIMARY KEY,
                    display_name TEXT,
                    created_at REAL,
                    last_opened_at REAL
                );
            """)

    def workspaces(self) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT name FROM workspaces ORDER BY last_opened_at DESC").fetchall()
        return [r[0] for r in rows]

    def create_workspace(self, name: str, display_name: Optional[str] = None) -> None:
        with self._lock:
            now = time.time()
            with self._conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO workspaces(name, display_name, created_at, last_opened_at) VALUES(?,?,?,?)",
                    (name, display_name or name, now, now),
                )
            self._current_workspace = name

    def switch_workspace(self, name: str) -> None:
        if name == self._current_workspace:
            return
        self.persist()
        self._current_workspace = name
        with self._lock:
            with self._conn() as conn:
                conn.execute("UPDATE workspaces SET last_opened_at=? WHERE name=?", (time.time(), name))
                row = conn.execute("SELECT * FROM sessions WHERE workspace=?", (name,)).fetchone()
            if row:
                self._current = self._row_to_session(row)
            else:
                self._current = Session(workspace=name)
                self.persist()

    def current_workspace(self) -> str:
        return self._current_workspace

    def current(self) -> Session:
        return self._current

    def update(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self._current, k):
                    setattr(self._current, k, v)
            self._current.updated_at = time.time()
            self._schedule_persist()

    def _schedule_persist(self) -> None:
        if self._autosave_timer:
            self._autosave_timer.cancel()
        self._autosave_timer = threading.Timer(5.0, self._autosave)
        self._autosave_timer.daemon = True
        self._autosave_timer.start()

    def _autosave(self) -> None:
        self.persist()

    def persist(self) -> None:
        with self._lock:
            s = self._current
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO sessions(
                        workspace, goal_id, task_id, conversation_summary, panel,
                        window_width, window_height, window_x, window_y,
                        theme, ollama_status, voice_enabled, active_model,
                        recent_files, recent_tools, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(workspace) DO UPDATE SET
                        goal_id=excluded.goal_id,
                        task_id=excluded.task_id,
                        conversation_summary=excluded.conversation_summary,
                        panel=excluded.panel,
                        window_width=excluded.window_width,
                        window_height=excluded.window_height,
                        window_x=excluded.window_x,
                        window_y=excluded.window_y,
                        theme=excluded.theme,
                        ollama_status=excluded.ollama_status,
                        voice_enabled=excluded.voice_enabled,
                        active_model=excluded.active_model,
                        recent_files=excluded.recent_files,
                        recent_tools=excluded.recent_tools,
                        updated_at=excluded.updated_at
                    """,
                    (
                        s.workspace,
                        s.goal_id,
                        s.task_id,
                        s.conversation_summary,
                        s.panel,
                        int(s.window_width),
                        int(s.window_height),
                        s.window_x,
                        s.window_y,
                        s.theme,
                        s.ollama_status,
                        1 if s.voice_enabled else 0,
                        s.active_model,
                        json.dumps(s.recent_files),
                        json.dumps(s.recent_tools),
                        s.updated_at,
                    ),
                )

    def restore(self, workspace: Optional[str] = None) -> Session:
        name = workspace or self._current_workspace
        with self._lock:
            with self._conn() as conn:
                row = conn.execute("SELECT * FROM sessions WHERE workspace=?", (name,)).fetchone()
            if row:
                self._current = self._row_to_session(row)
            else:
                self._current = Session(workspace=name)
            self._current_workspace = name
            return self._current

    def close(self) -> None:
        if self._autosave_timer:
            self._autosave_timer.cancel()
        try:
            self.persist()
        except Exception:
            pass

    def _row_to_session(self, row: tuple[Any, ...]) -> Session:
        (
            workspace,
            goal_id,
            task_id,
            conversation_summary,
            panel,
            window_width,
            window_height,
            window_x,
            window_y,
            theme,
            ollama_status,
            voice_enabled,
            active_model,
            recent_files_json,
            recent_tools_json,
            updated_at,
        ) = row
        try:
            recent_files = json.loads(recent_files_json or "[]")
        except Exception:
            recent_files = []
        try:
            recent_tools = json.loads(recent_tools_json or "[]")
        except Exception:
            recent_tools = []
        return Session(
            workspace=workspace or _DEFAULT_WORKSPACE,
            goal_id=goal_id,
            task_id=task_id,
            conversation_summary=conversation_summary or "",
            panel=panel or "chat",
            window_width=int(window_width or 1280),
            window_height=int(window_height or 840),
            window_x=int(window_x) if window_x is not None else None,
            window_y=int(window_y) if window_y is not None else None,
            theme=theme or "dark",
            ollama_status=ollama_status or "unknown",
            voice_enabled=bool(voice_enabled),
            active_model=active_model or "",
            recent_files=recent_files,
            recent_tools=recent_tools,
            updated_at=float(updated_at or time.time()),
        )
