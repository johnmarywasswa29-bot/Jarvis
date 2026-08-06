"""Shared state container for the Jarvis LangGraph flow."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class JarvisState:
    """Facts/shared state that flows through agent nodes."""

    transcript: str = ""
    context: list[str] = field(default_factory=list)
    plan: str = ""
    selected_tools: list[str] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    answer: str = ""
    reflection: str = ""
    needs_tool: bool = False
    retries: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "transcript": self.transcript,
            "context": list(self.context),
            "plan": self.plan,
            "selected_tools": list(self.selected_tools),
            "tool_results": list(self.tool_results),
            "answer": self.answer,
            "reflection": self.reflection,
            "needs_tool": self.needs_tool,
            "retries": self.retries,
            "error": self.error,
        }
