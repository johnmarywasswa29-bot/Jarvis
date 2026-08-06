"""Workspace watcher: background monitoring, snapshotting, caching."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from workspace.state import ProjectContext, WorkspaceSnapshot
from workspace.history import WorkspaceHistory
from workspace.tracker import ApplicationTracker, WindowTracker
from workspace.project_detector import ProjectDetector
from workspace.git_context import GitContext
from workspace.file_context import FileContext
from workspace.terminal_context import TerminalContext
from workspace.browser_context import BrowserContext


class WorkspaceWatcher:
    def __init__(
        self,
        cache_path: Optional[str | Path] = None,
        refresh_interval_s: float = 2.0,
        callback: Optional[Callable[[WorkspaceSnapshot], None]] = None,
    ) -> None:
        self.cache_path = Path(cache_path) if cache_path else Path.cwd() / "data" / "workspace_cache.json"
        self.refresh_interval_s = refresh_interval_s
        self.callback = callback
        self.history = WorkspaceHistory()
        self.tracker = ApplicationTracker()
        self.window_tracker = WindowTracker()
        self.detector = ProjectDetector()
        self._latest: Optional[WorkspaceSnapshot] = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.history.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:
                pass
            self._stop.wait(self.refresh_interval_s)

    def refresh(self) -> WorkspaceSnapshot:
        with self._lock:
            snap = self._snapshot()
            snap.confidence = self._confidence(snap)
            self._latest = snap
            self.history.save_snapshot(snap)
            if self.callback:
                try:
                    self.callback(snap)
                except Exception:
                    pass
            return snap

    def cached(self) -> Optional[WorkspaceSnapshot]:
        with self._lock:
            return self._latest

    def _snapshot(self) -> WorkspaceSnapshot:
        cwd = os.getcwd()
        snap = WorkspaceSnapshot(
            active_application="",
            open_applications=[],
            active_project="",
            working_directory=cwd,
        )
        try:
            import win32gui
            def enum_cb(hwnd, windows):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if title:
                        windows.append(title)
            windows = []
            win32gui.EnumWindows(enum_cb, windows)
            snap.open_windows = windows[:50]
            hwnd = win32gui.GetForegroundWindow()
            snap.active_application = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            pass
        project = self.detector.detect(cwd)
        snap.active_project = project.name
        snap = GitContext.enrich(snap, cwd)
        snap = FileContext.enrich(snap, cwd)
        snap = TerminalContext.enrich(snap)
        snap = BrowserContext.enrich(snap)
        self.tracker.update(snap.active_application, snap.open_applications)
        self.window_tracker.update(snap.open_windows)
        return snap

    @staticmethod
    def _confidence(snap: WorkspaceSnapshot) -> float:
        score = 0.1
        if snap.active_application:
            score += 0.2
        if snap.active_project:
            score += 0.3
        if snap.git_repository:
            score += 0.3
        if snap.open_files:
            score += 0.1
        return max(0.0, min(1.0, score))
