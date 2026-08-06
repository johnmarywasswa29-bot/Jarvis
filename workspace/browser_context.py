"""Browser context (best-effort, privacy respecting)."""
from __future__ import annotations

from typing import Optional

from workspace.state import WorkspaceSnapshot


class BrowserContext:
    @staticmethod
    def enrich(snapshot: WorkspaceSnapshot) -> WorkspaceSnapshot:
        # Placeholder for browser-domain extraction; do not parse full page content.
        return snapshot
