"""Planner node: builds a short plan and optionally rewrites the prompt."""
from __future__ import annotations

import re
from typing import Any, Optional

from modules.config import JarvisConfig
from modules.logger import get_logger
from modules.memory import JarvisMemory


logger = get_logger("planner")


def planner_node(
    state: dict[str, Any],
    config: JarvisConfig,
    memory: JarvisMemory,
    *,
    llm_builder: Any | None = None,
    goals: Any | None = None,
) -> dict[str, Any]:
    transcript: str = state.get("transcript", "")
    context = state.get("context", [])
    recent = ""
    try:
        recent = memory.get_recent_context()
    except Exception:
        pass

    goal_context = ""
    if hasattr(goals, "to_context"):
        try:
            goal_context = goals.to_context(max_goals=5)
        except Exception:
            goal_context = ""

    state.setdefault("selected_tools", [])
    selected_tools = state.get("selected_tools", []) or []

    try:
        from modules.planner_v2 import improve_plan

        raw_plan = improve_plan(
            transcript=transcript,
            selected_tools=selected_tools,
            recent_context=recent,
            goals_context=goal_context,
            llm_builder=llm_builder,
        )
        plan_text = raw_plan.strip()
        plan_obj = None
        try:
            from modules.planner_v3 import build_plan_from_transcript, PlanValidationError

            plan_obj = build_plan_from_transcript(
                transcript=transcript,
                selected_tools=selected_tools,
                llm_builder=llm_builder,
            )
            plan_text = plan_obj.to_legacy()
        except (PlanValidationError, Exception):
            plan_obj = None
    except Exception:
        plan_prompt = (
            "Plan how to help the user best, given available tools: "
            f"{', '.join(selected_tools) or 'none'}.\n"
            "Recent context:\n"
            f"{recent}\n\n"
        )
        if goal_context:
            plan_prompt += f"Active goals:\n{goal_context}\n\n"
        plan_prompt += (
            f"User: {transcript}\n"
            "Respond in 1-2 lines as a plan only, no JSON."
        )

        if llm_builder is not None:
            plan_text = llm_builder.chat(plan_prompt).strip()
        else:
            plan_text = re.split(r"(?:\n|\. )", transcript.strip())[0]

        plan_obj = None

    state["plan"] = plan_text.strip()
    if plan_obj is not None:
        state["plan_object"] = plan_obj.to_dict()
    state["goal_context"] = goal_context.strip()
    logger.info("Planned: %s", state["plan"])
    return state
