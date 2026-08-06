"""WorkflowPlanner: plans workflow steps using intent, tools, habits, RAG."""
from __future__ import annotations

import time
from typing import Any, Optional

from workflows.state import WorkflowState, WorkflowStep, StepStatus


class WorkflowPlanner:
    def __init__(
        self,
        tool_registry: Any = None,
        intent_analyzer: Any = None,
        max_steps: int = 20,
        max_parallel: int = 4,
    ) -> None:
        self.tool_registry = tool_registry
        self.intent_analyzer = intent_analyzer
        self.max_steps = max_steps
        self.max_parallel = max_parallel

    def plan(self, goal: str, context: Optional[dict[str, Any]] = None) -> WorkflowState:
        state = WorkflowState(name=goal, description=goal)
        tools = []
        if self.tool_registry:
            try:
                tools = self.tool_registry.select_tools(goal)
            except Exception:
                tools = []
        intent = ""
        if self.intent_analyzer:
            try:
                result = self.intent_analyzer.analyze(goal)
                intent = getattr(result, "intent", "") or ""
            except Exception:
                intent = ""
        if not tools:
            tools = [self._default_tool(goal)]
        for tool in tools:
            step = WorkflowStep(
                description=goal,
                intent=intent,
                tool=getattr(tool, "name", tool) if not isinstance(tool, str) else tool,
                parameters={"prompt": goal},
            )
            state.steps.append(step)
        return state

    def _default_tool(self, goal: str) -> str:
        low = goal.lower()
        if any(x in low for x in ["search", "find", "lookup"]):
            return "web_search"
        if any(x in low for x in ["open", "launch", "start"]):
            return "desktop_control"
        if any(x in low for x in ["file", "folder", "create file", "write"]):
            return "filesystem"
        if any(x in low for x in ["code", "python", "run code", "calculate"]):
            return "code_execution"
        return "web_search"
