"""Workspace state and data models."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Optional


@dataclass
class WorkspaceSnapshot:
    snapshot_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())
    active_application: str = ""
    open_applications: list[str] = field(default_factory=list)
    open_windows: list[str] = field(default_factory=list)
    active_project: str = ""
    working_directory: str = ""
    git_repository: str = ""
    open_files: list[str] = field(default_factory=list)
    terminal_path: str = ""
    browser_domains: list[str] = field(default_factory=list)
    clipboard_hash: Optional[str] = None
    confidence: float = 0.0


@dataclass
class ProjectContext:
    name: str = ""
    path: str = ""
    language: str = ""
    git_repo: str = ""
    ide: str = ""
    files: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class WorkspaceHistoryEntry:
    entry_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())
    snapshot: Optional[WorkspaceSnapshot] = None
    project: Optional[ProjectContext] = None
    event_type: str = "snapshot"
    metadata: dict = field(default_factory=dict)
