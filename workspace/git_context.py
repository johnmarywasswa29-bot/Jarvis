"""Git context extraction."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from workspace.state import WorkspaceSnapshot


class GitContext:
    @staticmethod
    def enrich(snapshot: WorkspaceSnapshot, cwd: Optional[str] = None) -> WorkspaceSnapshot:
        root = Path(cwd) if cwd else Path.cwd()
        if not root.exists():
            return snapshot
        try:
            import subprocess
            out = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=root, text=True, stderr=subprocess.DEVNULL)
            repo = out.strip()
            snapshot.git_repository = repo
            branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
            snapshot.open_windows.append(f"git:{branch}")
        except Exception:
            pass
        return snapshot
