"""Document watcher for incremental indexing."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional


class KnowledgeWatcher:
    def __init__(self, *, poll_interval: float = 5.0) -> None:
        self.poll_interval = poll_interval
        self._watch_dirs: dict[str, dict[str, Any]] = {}
        self._watch_tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def add_watch(self, folder: str | Path, index_fn: Callable[[str], Any], *, ignore_dirs: Optional[set[str]] = None, extensions: Optional[set[str]] = None, max_size: Optional[int] = None) -> None:
        folder = str(Path(folder).resolve())
        with self._lock:
            self._watch_dirs[folder] = {
                "index_fn": index_fn,
                "ignore_dirs": ignore_dirs or {".git", "__pycache__", "node_modules", "venv", ".venv", "dist", "build", "chroma", "store"},
                "extensions": extensions or {".pdf", ".txt", ".md", ".docx", ".py", ".json", ".csv", ".html", ".java", ".js", ".c", ".cpp", ".h", ".log", ".rtf", ".pptx", ".ppt", ".xlsx", ".xls", ".eml"},
                "max_size": max_size or 20 * 1024 * 1024,
                "last_snapshot": {},
                "last_mtime": 0.0,
            }
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._watch_loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def _watch_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                pass
            self._stop_event.wait(self.poll_interval)

    def _tick(self) -> None:
        with self._lock:
            for folder, cfg in list(self._watch_dirs.items()):
                try:
                    self._scan_folder(folder, cfg)
                except Exception:
                    pass

    def _scan_folder(self, folder: str, cfg: dict[str, Any]) -> None:
        base = Path(folder)
        if not base.exists() or not base.is_dir():
            return
        snapshot: dict[str, tuple[float, int]] = {}
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in cfg["ignore_dirs"]]
            for name in files:
                p = Path(root) / name
                try:
                    stat = p.stat()
                except Exception:
                    continue
                snapshot[str(p)] = (stat.st_mtime, stat.st_size)

        last_snapshot = cfg.get("last_snapshot", {})
        added = [p for p in snapshot if p not in last_snapshot]
        removed = [p for p in last_snapshot if p not in snapshot]
        changed = [p for p in snapshot if p in last_snapshot and snapshot[p] != last_snapshot[p]]

        cfg["last_snapshot"] = snapshot

        for path in added:
            self._maybe_index(path, cfg, reason="added")
        for path in changed:
            self._maybe_index(path, cfg, reason="changed")
        for path in removed:
            self._maybe_unindex(path, cfg)

    def _maybe_index(self, path: str, cfg: dict[str, Any], *, reason: str) -> None:
        p = Path(path)
        if p.suffix.lower() not in cfg["extensions"]:
            return
        try:
            if p.stat().st_size > cfg["max_size"]:
                return
        except Exception:
            return
        cfg["index_fn"](str(p))

    def _maybe_unindex(self, path: str, cfg: dict[str, Any]) -> None:
        key = path
        cfg.pop(key, None)
