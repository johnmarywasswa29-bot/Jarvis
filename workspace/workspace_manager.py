"""WorkspaceManager: public API for workspace awareness."""
from __future__ import annotations

from typing import Any, Optional

from workspace.state import ProjectContext, WorkspaceSnapshot
from workspace.history import WorkspaceHistory
from workspace.watcher import WorkspaceWatcher


class WorkspaceManager:
    def __init__(
        self,
        history: Optional[WorkspaceHistory] = None,
        watcher: Optional[WorkspaceWatcher] = None,
    ) -> None:
        self.history = history or WorkspaceHistory()
        self.watcher = watcher or WorkspaceWatcher()
        self._project: Optional[ProjectContext] = None

    def start(self) -> None:
        self.watcher.start()

    def stop(self) -> None:
        self.watcher.stop()
        self.history.close()

    def snapshot(self) -> Optional[WorkspaceSnapshot]:
        return self.watcher.cached() or self.watcher.refresh()

    def current_project(self) -> Optional[ProjectContext]:
        snap = self.snapshot()
        if not snap:
            return None
        if self._project is None or self._project.path != snap.working_directory:
            self._project = self.watcher.detector.detect(snap.working_directory)
        return self._project

    def recent_projects(self, limit: int = 20) -> list[ProjectContext]:
        return self.history.recent_projects(limit)

    def recent_snapshots(self, limit: int = 20) -> list[WorkspaceSnapshot]:
        return self.history.recent_snapshots(limit)

    def enrich_workflow_context(self, context: dict[str, Any]) -> dict[str, Any]:
        snap = self.snapshot()
        if snap:
            context.setdefault("current_project", snap.active_project)
            context.setdefault("working_directory", snap.working_directory)
            context.setdefault("git_repository", snap.git_repository)
            context.setdefault("open_applications", snap.open_applications)
            context.setdefault("workspace_confidence", snap.confidence)
        project = self.current_project()
        if project:
            context.setdefault("project_language", project.language)
            context.setdefault("project_ide", project.ide)
        return context

    def enrich_intent(self, intent: str, context: dict[str, Any]) -> str:
        snap = self.snapshot()
        if not snap:
            return intent
        project = self.current_project() or ProjectContext(name=snap.active_project, path=snap.working_directory)
        placeholders = {
            "${workspace}": snap.working_directory,
            "${project}": project.name,
            "${repo}": snap.git_repository or project.git_repo,
        }
        for k, v in placeholders.items():
            if v:
                intent = intent.replace(k, v)
        return intent
