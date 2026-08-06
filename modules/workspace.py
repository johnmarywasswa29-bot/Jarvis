"""Workspace awareness: project detection, git metadata, change tracking, language/test detection."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional


@dataclass
class WorkspaceContext:
    root: Optional[Path] = None
    git_repo: bool = False
    git_branch: Optional[str] = None
    dirty: bool = False
    modified_files: list[str] = field(default_factory=list)
    recently_edited: list[str] = field(default_factory=list)
    languages: dict[str, int] = field(default_factory=dict)
    test_framework: Optional[str] = None
    updated_at: float = field(default_factory=time.time)


class WorkspaceWatcher:
    def __init__(self, cache_path: Optional[str | Path] = None, poll_interval_s: float = 1.5) -> None:
        self.cache_path = Path(cache_path) if cache_path else Path(".jarvis") / "workspace_cache.json"
        self.poll_interval_s = max(0.1, float(poll_interval_s))
        self._state: Optional[WorkspaceContext] = None
        self._polling = False
        self._poll_thread: Optional[threading.Thread] = None

    def current_project(self) -> Optional[Path]:
        cwd = Path.cwd()
        if (cwd / "pyproject.toml").exists() or (cwd / "setup.py").exists() or (cwd / "requirements.txt").exists():
            return cwd
        return cwd

    def snapshot(self, root: Optional[Path] = None) -> WorkspaceContext:
        root = root or self.current_project()
        if root is None or not root.exists():
            return WorkspaceContext(root=None)
        ctx = WorkspaceContext(root=root, updated_at=time.time())
        ctx.git_repo = self._is_git_repo(root)
        if ctx.git_repo:
            ctx.git_branch = self._git_branch(root)
            ctx.dirty = self._git_dirty(root)
            ctx.modified_files = self._git_modified(root)
        ctx.recently_edited = self._recently_edited(root)
        ctx.languages = self._detect_languages(root)
        ctx.test_framework = self._detect_test_framework(root)
        return ctx

    def start(self) -> None:
        if self._polling:
            return
        self._polling = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def stop(self) -> None:
        self._polling = False

    def _poll_loop(self) -> None:
        while self._polling:
            self._state = self.snapshot()
            time.sleep(self.poll_interval_s)

    def cached(self) -> Optional[WorkspaceContext]:
        if self._state is not None:
            return self._state
        try:
            if self.cache_path.exists():
                data = json.loads(self.cache_path.read_text(encoding="utf-8"))
                return WorkspaceContext(**data)
        except Exception:
            pass
        return None

    def save_cache(self, ctx: Optional[WorkspaceContext] = None) -> None:
        ctx = ctx or self._state
        if ctx is None:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "root": str(ctx.root) if ctx.root else None,
                "git_repo": ctx.git_repo,
                "git_branch": ctx.git_branch,
                "dirty": ctx.dirty,
                "modified_files": ctx.modified_files,
                "recently_edited": ctx.recently_edited,
                "languages": ctx.languages,
                "test_framework": ctx.test_framework,
                "updated_at": ctx.updated_at,
            }
            self.cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _shallow_file_walk(self, root: Path, limit: int = 400) -> Iterable[Path]:
        yielded = 0
        try:
            for dirpath, _, filenames in os.walk(root):
                try:
                    if yielded >= limit:
                        break
                    for name in filenames:
                        p = Path(dirpath) / name
                        if len(p.parts) - len(root.parts) > 3:
                            continue
                        yield p
                        yielded += 1
                        if yielded >= limit:
                            break
                except Exception:
                    continue
        except Exception:
            return

    def _recently_edited(self, root: Path, max_items: int = 20) -> list[str]:
        items: list[tuple[float, Path]] = []
        for p in self._shallow_file_walk(root):
            try:
                st = p.stat()
                items.append((st.st_mtime, p))
            except Exception:
                pass
        items.sort(reverse=True)
        return [str(p) for _, p in items[:max_items]]

    def _detect_languages(self, root: Path) -> dict[str, int]:
        counts: dict[str, int] = {}
        mapping = {
            ".py": "Python",
            ".js": "JavaScript",
            ".ts": "TypeScript",
            ".rs": "Rust",
            ".go": "Go",
            ".java": "Java",
            ".c": "C",
            ".cpp": "C++",
            ".cs": "C#",
            ".rb": "Ruby",
            ".ex": "Elixir",
        }
        for p in self._shallow_file_walk(root):
            lang = mapping.get(p.suffix.lower())
            if not lang:
                continue
            counts[lang] = counts.get(lang, 0) + 1
        return counts

    def _detect_test_framework(self, root: Path) -> Optional[str]:
        for p in self._shallow_file_walk(root, limit=200):
            name = p.name.lower()
            if name == "pytest.ini" or name.startswith("conftest.py"):
                return "pytest"
            if name == "setup.cfg" or name == "pyproject.toml":
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")[:4000]
                    if "pytest" in text or "[tool.pytest" in text:
                        return "pytest"
                    if "unittest" in text:
                        return "unittest"
                except Exception:
                    pass
            if name.startswith("test_") and p.suffix == ".py":
                return "unittest/pytest"
        return None

    def _is_git_repo(self, root: Path) -> bool:
        return (root / ".git").exists()

    def _git(self, root: Path, *args: str) -> str:
        try:
            out = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
                timeout=4.0,
            )
            return out.stdout.strip()
        except Exception:
            return ""

    def _git_branch(self, root: Path) -> Optional[str]:
        b = self._git(root, "rev-parse", "--abbrev-ref", "HEAD")
        return b or None

    def _git_dirty(self, root: Path) -> bool:
        return bool(self._git(root, "status", "--porcelain"))

    def _git_modified(self, root: Path) -> list[str]:
        raw = self._git(root, "diff", "--name-only")
        if not raw:
            return []
        return [line.strip() for line in raw.splitlines() if line.strip()]
