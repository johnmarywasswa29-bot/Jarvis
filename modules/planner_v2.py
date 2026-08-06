"""Planner improvements: optional enhanced planner compatible with existing nodes."""
from __future__ import annotations

import re
from typing import Any, Optional


def improve_plan(
    transcript: str,
    selected_tools: list[str],
    recent_context: str,
    goals_context: str,
    *,
    llm_builder: Any | None = None,
) -> str:
    """Return a concise, structured 1-3 step plan for the transcript."""
    tools_note = ", ".join(selected_tools) or "none"
    goals_note = goals_context.strip()

    if llm_builder is not None:
        prompt = (
            "Create a concise 1-3 step plan to satisfy the user.\n"
            f"Tools available: {tools_note}.\n"
            "Recent context:\n"
            f"{recent_context}\n\n"
        )
        if goals_note:
            prompt += f"Active goals:\n{goals_note}\n\n"
        prompt += f"User request: {transcript}\nPlan:"
        plan = llm_builder.chat(prompt).strip()
        if plan:
            return plan

    # Heuristic fallback
    parts: list[str] = []
    if selected_tools:
        parts.append(f"Use {tools_note}")
    if goals_note:
        parts.append("Align with active goals")
    parts.append(transcript.strip())
    return "; ".join(parts[:3])
