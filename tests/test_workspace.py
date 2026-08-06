"""Workspace tests: snapshot, tracker, detector, git/file/terminal/browser, watcher, manager."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]

from workspace.state import WorkspaceSnapshot, ProjectContext, WorkspaceHistoryEntry
from workspace.history import WorkspaceHistory
from workspace.tracker import ApplicationTracker, WindowTracker
from workspace.project_detector import ProjectDetector
from workspace.git_context import GitContext
from workspace.file_context import FileContext
from workspace.terminal_context import TerminalContext
from workspace.browser_context import BrowserContext
from workspace.watcher import WorkspaceWatcher
from workspace.workspace_manager import WorkspaceManager


def tmp_db(name: str) -> Path:
    d = REPO / "tests" / "tmp_workspace" / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    return d / "workspace.sqlite"


class TestWorkspaceSnapshot(unittest.TestCase):
    def test_defaults(self):
        snap = WorkspaceSnapshot()
        assert snap.snapshot_id
        assert snap.timestamp
        assert snap.open_applications == []
        assert snap.confidence == 0.0

    def test_fields(self):
        snap = WorkspaceSnapshot(active_application="code.exe", active_project="jarvis", confidence=0.8)
        assert snap.active_application == "code.exe"
        assert snap.active_project == "jarvis"
        assert snap.confidence == 0.8


class TestProjectContext(unittest.TestCase):
    def test_defaults(self):
        p = ProjectContext()
        assert p.name == ""
        assert p.confidence == 0.0


class TestApplicationTracker(unittest.TestCase):
    def test_update_and_snapshot(self):
        t = ApplicationTracker()
        t.update("code.exe", ["code.exe", "explorer.exe"])
        snap = t.snapshot()
        assert snap.active_application == "code.exe"
        assert snap.open_applications == ["code.exe", "explorer.exe"]


class TestWindowTracker(unittest.TestCase):
    def test_update(self):
        w = WindowTracker()
        w.update(["jarvis.py - VS Code", "Command Prompt"])
        snap = w.snapshot()
        assert snap.open_windows == ["jarvis.py - VS Code", "Command Prompt"]


class TestProjectDetector(unittest.TestCase):
    def test_detect_no_path(self):
        d = ProjectDetector()
        p = d.detect(None)
        assert p.path == ""

    def test_detect_non_existent(self):
        d = ProjectDetector()
        p = d.detect("Z:/nonexistent")
        assert p.path == ""

    def test_detect_python_project(self):
        tmp_path = REPO / "tests" / "tmp_workspace" / "pyproj"
        if tmp_path.exists():
            shutil.rmtree(tmp_path)
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "requirements.txt").write_text("flask", encoding="utf-8")
        (tmp_path / ".git").mkdir(exist_ok=True)
        d = ProjectDetector()
        p = d.detect(str(tmp_path))
        assert p.language == "Python"
        assert p.git_repo == str(tmp_path)
        shutil.rmtree(tmp_path, ignore_errors=True)

    def test_detect_rust_project(self):
        rust_dir = REPO / "tests" / "tmp_workspace" / "rustproj"
        if rust_dir.exists():
            shutil.rmtree(rust_dir)
        rust_dir.mkdir(parents=True, exist_ok=True)
        (rust_dir / "Cargo.toml").write_text("[package]\nname=\"x\"\n", encoding="utf-8")
        d = ProjectDetector()
        p = d.detect(str(rust_dir))
        assert p.language == "Rust"
        shutil.rmtree(rust_dir, ignore_errors=True)


class TestGitContext(unittest.TestCase):
    def test_enrich_repo(self):
        snap = WorkspaceSnapshot(working_directory=str(REPO))
        snap = GitContext.enrich(snap, str(REPO))
        if (REPO / ".git").exists():
            assert snap.git_repository
        else:
            assert snap.git_repository == ""

    def test_enrich_non_repo(self):
        snap = WorkspaceSnapshot(working_directory="Z:/nonexistent")
        snap = GitContext.enrich(snap, "Z:/nonexistent")
        assert snap.git_repository == ""


class TestFileContext(unittest.TestCase):
    def test_enrich_lists_files(self):
        snap = WorkspaceSnapshot()
        snap = FileContext.enrich(snap, str(REPO))
        assert len(snap.open_files) > 0


class TestTerminalContext(unittest.TestCase):
    def test_enrich_sets_path(self):
        snap = WorkspaceSnapshot()
        snap = TerminalContext.enrich(snap)
        assert snap.terminal_path == os.getcwd()


class TestBrowserContext(unittest.TestCase):
    def test_enrich_noop(self):
        snap = WorkspaceSnapshot()
        snap = BrowserContext.enrich(snap)
        assert snap.browser_domains == []


class TestWorkspaceHistory(unittest.TestCase):
    def test_save_and_recent(self):
        hist = WorkspaceHistory(tmp_db("recent"))
        snap = WorkspaceSnapshot(active_application="code.exe", active_project="jarvis")
        hist.save_snapshot(snap)
        snaps = hist.recent_snapshots(5)
        assert len(snaps) == 1
        assert snaps[0].active_application == "code.exe"
        hist.close()

    def test_recent_projects(self):
        hist = WorkspaceHistory(tmp_db("projects"))
        entry = WorkspaceHistoryEntry(project=ProjectContext(name="demo", path="/tmp", language="Python"))
        hist.save_entry(entry)
        projs = hist.recent_projects(5)
        assert len(projs) == 1
        assert projs[0].name == "demo"
        hist.close()


class TestWorkspaceWatcher(unittest.TestCase):
    def test_refresh_returns_snapshot(self):
        watcher = WorkspaceWatcher(cache_path=REPO / "tests" / "tmp_workspace" / "cache.json", refresh_interval_s=0)
        try:
            snap = watcher.refresh()
            assert isinstance(snap, WorkspaceSnapshot)
        finally:
            watcher.stop()

    def test_cached(self):
        watcher = WorkspaceWatcher(cache_path=REPO / "tests" / "tmp_workspace" / "cache2.json", refresh_interval_s=0)
        try:
            snap = watcher.refresh()
            assert watcher.cached() is snap
        finally:
            watcher.stop()


class TestWorkspaceManager(unittest.TestCase):
    def test_snapshot_and_project(self):
        mgr = WorkspaceManager()
        try:
            snap = mgr.snapshot()
            assert isinstance(snap, WorkspaceSnapshot)
            proj = mgr.current_project()
            assert isinstance(proj, ProjectContext)
        finally:
            mgr.stop()

    def test_enrich_workflow_context(self):
        mgr = WorkspaceManager()
        try:
            ctx = mgr.enrich_workflow_context({})
            assert isinstance(ctx, dict)
        finally:
            mgr.stop()

    def test_enrich_intent(self):
        mgr = WorkspaceManager()
        try:
            out = mgr.enrich_intent("Open ${project} notes", {})
            assert isinstance(out, str)
        finally:
            mgr.stop()


if __name__ == "__main__":
    unittest.main()
