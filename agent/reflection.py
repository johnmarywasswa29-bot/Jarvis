"""
Reflection: reviews assistant answer quality and decides whether to retry.
Output:
- refine: keep answer
- retry: send back to planner/executor once
- fallback: return raw tool output / concise note
"""
from __future__ import annotations

import re
from typing import Any

from modules.logger import get_logger

logger = get_logger("reflection")


def reflection_node(state: dict[str, Any], *, llm_builder: Any | None = None) -> dict[str, Any]:
    answer = state.get("answer", "") or ""
    tool_results = state.get("tool_results", [])
    tool_failed = any(not r.get("success", True) for r in tool_results)

    if not answer and tool_failed:
        fallbacks = [r.get("error", "") for r in tool_results if not r.get("success", True)]
        fallback = "Tool action did not complete: " + "; ".join(fallbacks[:2])
        state["answer"] = fallback.strip()
        state["reflection"] = "fallback"
        return state

    if len(answer.split()) < 6 and tool_failed:
        if llm_builder is not None:
            retry_prompt = (
                "The previous tool results had failures. "
                "Give the user one concise plain-language sentence."
                f" Raw output: {answer}"
            )
            answer = llm_builder.chat(retry_prompt).strip() or answer
            state["answer"] = answer
        state["reflection"] = "retry" if state.get("retries", 0) == 0 else "fallback"
        return state

    state["reflection"] = "refine"
    return state
