"""Context analysis from Memory, Habits, RAG, Workflow, Workspace, Intent."""
from __future__ import annotations

from typing import Any, Optional


class ContextAnalyzer:
    def __init__(
        self,
        memory: Any = None,
        habits: Any = None,
        rag: Any = None,
        workflow_manager: Any = None,
        workspace_manager: Any = None,
        intent_analyzer: Any = None,
    ) -> None:
        self.memory = memory
        self.habits = habits
        self.rag = rag
        self.workflow_manager = workflow_manager
        self.workspace_manager = workspace_manager
        self.intent_analyzer = intent_analyzer

    def analyze(self, prompt: Optional[str] = None) -> dict[str, Any]:
        ctx: dict[str, Any] = {}
        try:
            if self.workspace_manager:
                snap = self.workspace_manager.snapshot()
                if snap:
                    ctx["workspace"] = {
                        "active_application": snap.active_application,
                        "active_project": snap.active_project,
                        "working_directory": snap.working_directory,
                        "git_repository": snap.git_repository,
                        "open_applications": snap.open_applications,
                        "confidence": snap.confidence,
                    }
                    project = self.workspace_manager.current_project()
                    if project:
                        ctx["project"] = {
                            "name": project.name,
                            "path": project.path,
                            "language": project.language,
                            "git_repo": project.git_repo,
                            "ide": project.ide,
                        }
        except Exception:
            pass
        try:
            if self.habits:
                habits = self.habits.get_habits()
                ctx["habits"] = habits[:20]
        except Exception:
            pass
        try:
            if self.rag and prompt:
                docs = self.rag.query(prompt, top_k=3)
                ctx["rag"] = docs
        except Exception:
            pass
        try:
            if self.memory and prompt:
                msgs = self.memory.get_messages(limit=5)
                ctx["recent_memory"] = msgs
        except Exception:
            pass
        try:
            if self.workflow_manager:
                wfs = self.workflow_manager.list_workflows()
                ctx["workflows"] = wfs[:10]
        except Exception:
            pass
        try:
            if self.intent_analyzer and prompt:
                intent = self.intent_analyzer.analyze(prompt)
                ctx["intent"] = getattr(intent, "intent", None)
                ctx["intent_confidence"] = getattr(intent, "confidence", None)
        except Exception:
            pass
        return ctx
