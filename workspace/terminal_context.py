"""Terminal context extraction."""
from __future__ import annotations

import os
from typing import Optional

from workspace.state import WorkspaceSnapshot


class TerminalContext:
    @staticmethod
    def enrich(snapshot: WorkspaceSnapshot) -> WorkspaceSnapshot:
        try:
            snapshot.terminal_path = os.getcwd()
        except Exception:
            pass
        return snapshot
