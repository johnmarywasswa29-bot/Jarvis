"""WorkflowMemoryBridge: integrates workflows with Memory, Habits, RAG, and Intent."""
from __future__ import annotations

from typing import Any, Optional

from workflows.state import WorkflowState, WorkflowStep


class WorkflowMemoryBridge:
    def __init__(
        self,
        memory: Any = None,
        habits: Any = None,
        rag: Any = None,
        intent_analyzer: Any = None,
    ) -> None:
        self.memory = memory
        self.habits = habits
        self.rag = rag
        self.intent_analyzer = intent_analyzer

    def enrich_plan(self, state: WorkflowState) -> WorkflowState:
        if self.rag:
            try:
                ctx = self.rag.context(state.description, max_chars=2000, k=3)
                if ctx:
                    state.context.setdefault("rag_context", "")
                    state.context["rag_context"] = ctx
            except Exception:
                pass
        if self.memory:
            try:
                recent = getattr(self.memory, "messages", [])
                if recent:
                    state.context.setdefault("memory_hints", [])
                    state.context["memory_hints"] = recent[-5:]
            except Exception:
                pass
        if self.habits:
            try:
                habits = self.habits.suggest_habits(context={"intents": [s.intent for s in state.steps if s.intent]})
                if habits:
                    state.context.setdefault("habit_suggestions", [])
                    state.context["habit_suggestions"] = [{"habit": item["habit"].name, "score": item["score"]} for item in habits[:3]]
            except Exception:
                pass
        return state

    def enhance_step(self, step: WorkflowStep) -> WorkflowStep:
        if self.intent_analyzer and step.description and not step.intent:
            try:
                result = self.intent_analyzer.analyze(step.description)
                if result and getattr(result, "intent", None):
                    step.intent = result.intent
            except Exception:
                pass
        return step

    def record_success(self, state: WorkflowState) -> None:
        if self.memory:
            try:
                add = getattr(self.memory, "add_message", None)
                if callable(add):
                    add("assistant", f"Completed workflow: {state.name}", metadata={"workflow_id": state.workflow_id})
            except Exception:
                pass
        if self.habits:
            try:
                for step in state.steps:
                    if step.tool:
                        self.habits.record_event("tool_execution", {"tool": step.tool, "workflow": state.name})
            except Exception:
                pass
        if self.rag:
            try:
                from knowledge.rag import RAGService
                if isinstance(self.rag, RAGService):
                    self.rag.remember_query(state.name, success=True)
            except Exception:
                pass

    def record_failure(self, state: WorkflowState, step: WorkflowStep) -> None:
        if self.memory:
            try:
                add = getattr(self.memory, "add_message", None)
                if callable(add):
                    add("assistant", f"Failed workflow: {state.name} at {step.description}", metadata={"workflow_id": state.workflow_id, "error": step.error})
            except Exception:
                pass
