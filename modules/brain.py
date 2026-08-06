from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Callable, Optional
from modules.config import JarvisConfig
from modules.logger import get_logger
from modules.memory import JarvisMemory
from core.ollama_health import OllamaHealthState

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

    def _call_ollama(
        self,
        prompt: str,
        system: str = SYSTEM_PROMPT,
        *,
        options: Optional[dict[str, Any]] = None,
    ) -> str:
        merged_options = {"num_predict": 192, "temperature": 0.1}
        if options:
            merged_options.update(options)

        try:
            from ollama import Client, ChatResponse
        except Exception:
            return ""

        client = Client(host=self.config.llm_base_url)
        try:
            response: ChatResponse = client.chat(
                model=self.config.llm_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                options=merged_options,
                stream=False,
            )
        except Exception as exc:
            self.logger.error("Ollama chat failed: %s", exc)
            return ""

        msg = getattr(response, "message", None)
        if msg is None:
            return ""
        content = getattr(msg, "content", "")
        return content.strip()

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

        context = self.memory.get_recent_context()
        q_t1 = time.perf_counter()
        self.logger.debug("[chat] request_queued t0=%.3f q_context=%.3fms", t0, (q_t1 - q_t0) * 1000)

        extra = "\n".join(extra_context) if extra_context else ""
        full_prompt = (
            "Recent conversation:\n"
            f"{context}\n\n"
            f"{extra}\n\n"
            f"User: {prompt}\n"
            "Jarvis:"
        )
        answer_text = ""
        try:
            answer_text = self._call_ollama(full_prompt, stream=streaming)
            if streaming:
                first_token_seen = False
                assembled = ""
                for part in self._stream_tokens(full_prompt):
                    if not first_token_seen:
                        first_token_seen = True
                        self.logger.debug("[chat] first_token_received elapsed=%.3fms", (time.perf_counter() - q_t0) * 1000)
                    if on_chunk:
                        on_chunk(part)
                    assembled += part
                answer_text = assembled
        except Exception as exc:
            self.logger.error("Ollama streaming failed: %s", exc)
            if not answer_text:
                return self._fallback_response(prompt)

        self.logger.debug("[chat] completion elapsed=%.3fms", (time.perf_counter() - q_t0) * 1000)
        return answer_text.strip()

    def _stream_tokens(self, prompt: str):
        try:
            from ollama import Client
        except Exception:
            return iter([])

        client = Client(host=self.config.llm_base_url)
        try:
            response = client.chat(
                model=self.config.llm_model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                options={"num_predict": 192, "temperature": 0.1},
                stream=True,
                keep_alive=-1,
            )
        except Exception as exc:
            self.logger.error("Ollama stream open failed: %s", exc)
            return iter([])

        for chunk in response:
            msg = getattr(chunk, "message", None)
            if msg is not None:
                token = getattr(msg, "content", "")
                if token:
                    yield token

    def run(self, prompt: str, extra_context: Optional[list[str]] = None) -> str:
        t0 = time.time()
        context = self.memory.get_recent_context()
        t1 = time.time()
        self.logger.debug("[chat] memory_context elapsed=%.3f", t1 - t0)
        extra = "\n".join(extra_context) if extra_context else ""
        full_prompt = (
            "Recent conversation:\n"
            f"{context}\n\n"
            f"{extra}\n\n"
            f"User: {prompt}\n"
            "Jarvis:"
        )

        try:
            if self.ollama_health is not None and not self.ollama_health.is_available():
                return self._fallback_response(prompt, reason="Ollama is unavailable")
            answer = self._call_ollama(full_prompt)
        except Exception as exc:
            self.logger.error("Ollama call failed: %s", exc)
            return self._fallback_response(prompt)

        t2 = time.time()
        self.logger.debug("[chat] ollama_first elapsed=%.3f", t2 - t1)

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
                        final = self._call_ollama(followup_prompt)
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
                return self._call_ollama(followup)
            except Exception:
                return f"Tool '{tool_name}' is not available."

        # Plain answer
        return answer
