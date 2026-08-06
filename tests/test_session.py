"""Tests for modules/session.py."""
from __future__ import annotations

import gc
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from modules.session import Session, SessionStore, _DEFAULT_WORKSPACE, _DB_NAME


def _fresh_temp_dir() -> Path:
    td = Path(tempfile.gettempdir()) / f"jarvis_test_session_{time.time_ns()}"
    td.mkdir(parents=True, exist_ok=True)
    return td


class TestSessionDefaults(unittest.TestCase):
    def tearDown(self):
        pass

    def test_defaults(self):
        s = Session()
        self.assertEqual(s.workspace, _DEFAULT_WORKSPACE)
        self.assertEqual(s.panel, "chat")
        self.assertEqual(s.window_width, 1280)
        self.assertEqual(s.window_height, 840)
        self.assertIsNone(s.goal_id)

    def test_persistence_roundtrip(self):
        td = _fresh_temp_dir()
        store = SessionStore(base_dir=td)
        store.persist()
        loaded = store.restore()
        store.close()
        self.assertEqual(loaded.workspace, _DEFAULT_WORKSPACE)

    def test_update_and_autosave(self):
        td = _fresh_temp_dir()
        db = td / SessionStore._DB_NAME if hasattr(SessionStore, "_DB_NAME") else td / "jarvis_sessions.sqlite"
        store = SessionStore(base_dir=td)
        store.update(goal_id="goal-1", panel="goals", window_width=1024, window_height=768)
        store.persist()
        store.close()
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT panel, window_width FROM sessions WHERE workspace=?", (_DEFAULT_WORKSPACE,)).fetchone()
        conn.close()
        self.assertEqual(row[0], "goals")
        self.assertEqual(row[1], 1024)

    def test_workspace_switch(self):
        td = _fresh_temp_dir()
        store = SessionStore(base_dir=td)
        store.create_workspace("work", display_name="School")
        store.update(panel="memory", goal_id="g1")
        store.switch_workspace("work")
        store.persist()
        self.assertEqual(store.current_workspace(), "work")
        store.switch_workspace(_DEFAULT_WORKSPACE)
        store.persist()
        self.assertEqual(store.current_workspace(), _DEFAULT_WORKSPACE)
        store.close()

    def test_default_workspace_fallback(self):
        td = _fresh_temp_dir()
        store = SessionStore(base_dir=td)
        s = store.restore("nonexistent")
        store.close()
        self.assertEqual(s.workspace, "nonexistent")
        self.assertIsNone(s.goal_id)

    def test_multiple_workspaces(self):
        td = _fresh_temp_dir()
        store = SessionStore(base_dir=td)
        store.create_workspace("ws1", display_name="One")
        store.create_workspace("ws2", display_name="Two")
        names = store.workspaces()
        store.close()
        self.assertEqual(names, ["ws2", "ws1"])

    def test_corrupted_session_json_handling(self):
        td = _fresh_temp_dir()
        db = td / SessionStore._DB_NAME if hasattr(SessionStore, "_DB_NAME") else td / "jarvis_sessions.sqlite"
        store = SessionStore(base_dir=td)
        store.update(recent_files=["ok"], recent_tools=["tool"])
        store.persist()
        store.close()
        conn = sqlite3.connect(db)
        try:
            conn.execute("UPDATE sessions SET recent_files='bad', recent_tools='bad' WHERE workspace=?", (_DEFAULT_WORKSPACE,))
            conn.commit()
        finally:
            conn.close()
        store = SessionStore(base_dir=td)
        restored = store.restore()
        store.close()
        self.assertEqual(restored.recent_files, [])
        self.assertEqual(restored.recent_tools, [])

    def test_close_persists(self):
        td = _fresh_temp_dir()
        db = td / SessionStore._DB_NAME if hasattr(SessionStore, "_DB_NAME") else td / "jarvis_sessions.sqlite"
        store = SessionStore(base_dir=td)
        store.update(panel="settings")
        store.close()
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT panel FROM sessions WHERE workspace=?", (_DEFAULT_WORKSPACE,)).fetchone()
        conn.close()
        self.assertEqual(row[0], "settings")


if __name__ == "__main__":
    unittest.main()
