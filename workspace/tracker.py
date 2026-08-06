"""Application and window tracking."""
from __future__ import annotations

import threading
import time
from typing import Optional

from workspace.state import WorkspaceSnapshot


class ApplicationTracker:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_active: str = ""
        self._open_apps: list[str] = []

    def snapshot(self) -> WorkspaceSnapshot:
        with self._lock:
            return WorkspaceSnapshot(
                active_application=self._last_active,
                open_applications=list(self._open_apps),
            )

    def update(self, active: str, open_apps: Optional[list[str]] = None) -> None:
        with self._lock:
            self._last_active = active
            if open_apps is not None:
                self._open_apps = list(open_apps)


class WindowTracker:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._windows: list[str] = []

    def snapshot(self) -> WorkspaceSnapshot:
        with self._lock:
            s = WorkspaceSnapshot(open_windows=list(self._windows))
            return s

    def update(self, windows: list[str]) -> None:
        with self._lock:
            self._windows = list(windows)
