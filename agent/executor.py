"""Executor node: runs selected tools or chats, writes result back to state."""
from __future__ import annotations

import re
from typing import Any

from modules.config import JarvisConfig
from modules.logger import get_logger
from modules.memory import JarvisMemory
from modules.tools import ToolRegistry


logger = get_logger("executor")


def executor_node(
    state: dict[str, Any],
    config: JarvisConfig,
    registry: ToolRegistry,
    memory: JarvisMemory,
    *,
    llm_builder: Any | None = None,
) -> dict[str, Any]:
    transcript: str = state.get("transcript", "")
    selected = state.get("selected_tools", [])
    results: list[dict[str, Any]] = []

    if selected:
        for tool_name in selected:
            tool = next((t for t in registry.tools if t.name == tool_name and t.enabled), None)
            if tool is None:
                state.setdefault("error", "")
                state["error"] += f"Tool '{tool_name}' not available. "
                continue
            kwargs = registry._extract_kwargs(tool.name, transcript)
            result = registry.run_tool(tool, kwargs.get("prompt", transcript))
            results.append(
                {
                    "tool": tool.name,
                    "success": result.success,
                    "output": result.output,
                    "error": result.error,
                    "duration_s": getattr(result, "duration_s", 0.0),
                }
            )
        state["tool_results"] = results

        followup_prompt = (
            "Tool results:\n"
            + "\n".join(
                f"- {r['tool']}: {r['output']}" if r["success"] else f"- {r['tool']} failed: {r['error']}"
                for r in results
            )
            + "\n\n"
            f"User said: {transcript}\n"
            "Compose a short helpful natural-language answer. Do not mention JSON."
        )

        if llm_builder is not None:
            answer = llm_builder.chat(followup_prompt)
        else:
            answer = results[0].get("output", "") if results else ""

        state["answer"] = answer.strip()
        memory.add_message("assistant", state["answer"])
        return state

    # No tools selected: direct chat
    if llm_builder is not None:
        state["answer"] = llm_builder.chat(transcript).strip()
    else:
        state["answer"] = "I couldn't reach the local language model. Ollama may be offline."
    memory.add_message("assistant", state["answer"])
    return state
