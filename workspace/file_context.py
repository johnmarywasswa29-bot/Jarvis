"""File context tracking."""
from __future__ import annotations

import os
from typing import Optional

from workspace.state import WorkspaceSnapshot


class FileContext:
    @staticmethod
    def enrich(snapshot: WorkspaceSnapshot, cwd: Optional[str] = None) -> WorkspaceSnapshot:
        root = cwd or getattr(snapshot, "working_directory", None) or os.getcwd()
        try:
            entries = os.listdir(root)
            snapshot.open_files = entries[:20]
        except Exception:
            snapshot.open_files = []
        return snapshot
