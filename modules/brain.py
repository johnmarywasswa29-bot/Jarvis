from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Callable, Optional, AsyncGenerator
from modules.config import JarvisConfig
from modules.logger import get_logger
from modules.memory import JarvisMemory
from core.ollama_health import OllamaHealthState
from modules.llm_providers import get_llm_provider

logger = get_logger("brain")

SYSTEM_PROMPT = (
    "You are Jarvis, a private local AI assistant running on the user's Windows laptop. "
    "You are helpful, concise, and can use tools when needed. "
    "Available tools: web_search, desktop_control, code_execution, filesystem. "
    "When a tool is needed, respond with ONLY a JSON object in this format:\n"
    '{"tool": "tool_name", "args": {...}}\n'
    "Otherwise just answer conversationally in plain text."
)


class JarvisBrain:
    def __init__(
        self,
        config: JarvisConfig,
        tools: Any,
        memory: JarvisMemory,
        *,
        ollama_health: Any = None,
    ) -> None:
        self.config = config
        self.tools = tools
        self.memory = memory
        self.ollama_health = ollama_health
        self.logger = get_logger("brain")
        self.llm_provider = get_llm_provider(config)

    def _healthy(self) -> bool:
        if self.ollama_health is None:
            return True
        return self.ollama_health.is_available()

    def _status(self) -> str:
        if self.ollama_health is None:
            return "unknown"
        try:
            state = self.ollama_health.current_state()
        except Exception:
            return "unknown"
        # Accept both enum and plain string from mocks/real implementations.
        value = state.value if hasattr(state, "value") else str(state)
        if value == "degraded":
            return "degraded"
        return value

    def _fallback_response(self, prompt: str, reason: str = "") -> str:
        status = self._status()
        if reason:
            status = f"{status}: {reason}"
        p = prompt.lower()
        if any(x in p for x in ["hello", "hi ", "hi,", "hey"]):
            return "Hello. How can I help?"
        if any(x in p for x in ["thank"]):
            return "You're welcome."
        if any(x in p for x in ["who are you", "your name"]):
            return "I'm Jarvis, your local assistant."
        return f"LLM unavailable ({status}). Local tools, workflows, calendar, and memory remain available."

    def _build_messages(self, prompt: str, system: str = SYSTEM_PROMPT, extra_context: Optional[list[str]] = None) -> list[dict[str, str]]:
        context = self.memory.get_recent_context()
        extra = "\n".join(extra_context) if extra_context else ""
        full_prompt = (
            "Recent conversation:\n"
            f"{context}\n\n"
            f"{extra}\n\n"
            f"User: {prompt}\n"
            "Jarvis:"
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": full_prompt},
        ]

    def _call_provider(self, messages: list[dict[str, str]], *, stream: bool = False) -> str:
        """Call the configured LLM provider."""
        return self.llm_provider.chat(messages, stream=stream)

    async def _stream_provider(self, messages: list[dict[str, str]]) -> AsyncGenerator[str, None]:
        """Stream from the configured LLM provider."""
        async for token in self.llm_provider.stream_chat(messages):
            yield token

    def _parse_tool_call(self, text: str) -> Optional[dict[str, Any]]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict) and "tool" in data and "args" in data:
            return data
        return None

    def run_stream(
        self,
        prompt: str,
        on_chunk: Callable[[str], None] | None = None,
        extra_context: Optional[list[str]] = None,
    ) -> str:
        streaming = on_chunk is not None
        t0 = time.time()
        q_t0 = time.perf_counter()

        messages = self._build_messages(prompt, extra_context=extra_context)
        q_t1 = time.perf_counter()
        self.logger.debug("[chat] request_queued t0=%.3f q_context=%.3fms", t0, (q_t1 - q_t0) * 1000)

        answer_text = ""
        try:
            if streaming:
                first_token_seen = False
                assembled = ""
                # Run async streaming in event loop
                async def stream_and_collect():
                    nonlocal first_token_seen, assembled, answer_text
                    async for part in self._stream_provider(self._build_messages(prompt, extra_context=extra_context)):
                        if not first_token_seen:
                            first_token_seen = True
                            self.logger.debug("[chat] first_token_received elapsed=%.3fms", (time.perf_counter() - q_t0) * 1000)
                        if on_chunk:
                            on_chunk(part)
                        assembled += part
                    answer_text = assembled

                asyncio.run(stream_and_collect())
            else:
                answer_text = self._call_provider(self._build_messages(prompt))
        except Exception as exc:
            self.logger.error("Provider call failed: %s", exc)
            if not answer_text:
                return self._fallback_response(prompt)

        self.logger.debug("[chat] completion elapsed=%.3fms", (time.perf_counter() - q_t0) * 1000)
        return answer_text.strip()

    def run(self, prompt: str, extra_context: Optional[list[str]] = None) -> str:
        t0 = time.time()
        context = self.memory.get_recent_context()
        t1 = time.time()
        self.logger.debug("[chat] memory_context elapsed=%.3f", t1 - t0)

        messages = self._build_messages(prompt, extra_context=extra_context)

        try:
            # Check provider availability
            if not self.llm_provider.is_available():
                provider_name = self.config.llm_provider
                if provider_name == "ollama" and self.ollama_health is not None and not self.ollama_health.is_available():
                    return self._fallback_response(prompt, reason="Ollama is unavailable")
                return self._fallback_response(prompt, reason=f"{provider_name} provider unavailable")

            answer = self._call_provider(messages)
        except Exception as exc:
            self.logger.error("Provider call failed: %s", exc)
            return self._fallback_response(prompt)

        t2 = time.time()
        self.logger.debug("[chat] provider_first elapsed=%.3f", t2 - t1)

        if not answer:
            return self._fallback_response(prompt)

        tool_call = self._parse_tool_call(answer)
        if tool_call:
            tool_name = tool_call.get("tool", "")
            tool_args = tool_call.get("args", {}) or {}
            self.logger.info("Planned tool: %s %s", tool_name, tool_args)

            for candidate in self.tools.tools:
                if candidate.name == tool_name and candidate.enabled:
                    t_tool0 = time.time()
                    result = self.tools.run_tool(candidate, tool_args.get("prompt", prompt))
                    t_tool1 = time.time()
                    self.logger.debug("[chat] tool_run elapsed=%.3f", t_tool1 - t_tool0)
                    followup_prompt = (
                        f"Tool {tool_name} returned:\n{result}\n\n"
                        "Compose a short helpful natural-language answer for the user. "
                        "Do not mention JSON."
                    )
                    try:
                        followup_messages = self._build_messages(followup_prompt)
                        final = self._call_provider(followup_messages)
                        t_tool2 = time.time()
                        self.logger.debug("[chat] ollama_followup elapsed=%.3f", t_tool2 - t_tool1)
                        if not final:
                            final = str(result)
                        return final
                    except Exception:
                        return str(result)

            self.logger.warning("Tool '%s' not available", tool_name)
            followup = f"Tool '{tool_name}' is not available on this system. Please tell the user."
            try:
                followup_messages = self._build_messages(followup)
                return self._call_provider(followup_messages)
            except Exception:
                return f"Tool '{tool_name}' is not available."

        # Plain answer
        return answer