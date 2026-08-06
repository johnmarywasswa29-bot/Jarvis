"""
Router: decides whether the transcript should go through tool planning or direct chat.

Decision policy:
- If the transcript matches any registered tool's can_handle(), route to planner.
- Otherwise go straight to a direct_chat stub node.
"""
from __future__ import annotations

import re
from typing import Any

from modules.config import JarvisConfig
from modules.logger import get_logger
from modules.tools import ToolRegistry

logger = get_logger("router")


def route_node(state: dict[str, Any], config: JarvisConfig, registry: ToolRegistry) -> str:
    transcript: str = state.get("transcript", "")
    prompt = transcript.lower()

    matched_tools = []
    for tool in registry.tools:
        if tool.enabled and tool.can_handle(transcript):
            matched_tools.append(tool.name)

    if matched_tools:
        state["selected_tools"] = matched_tools
        state["needs_tool"] = True
        logger.info("Router -> planner: %s", matched_tools)
        return "planner"
    state["needs_tool"] = False
    logger.info("Router -> planner without tools")
    return "planner"
