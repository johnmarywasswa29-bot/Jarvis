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
        research_decider: Any = None,
    ) -> None:
        self.config = config
        self.tools = tools
        self.memory = memory
        self.ollama_health = ollama_health
        self.logger = get_logger("brain")
        self.llm_provider = get_llm_provider(config)
        # Optional research-workflow bridge (9G). If a decider is provided the
        # brain can route research/plan/execute requests through it; otherwise
        # the bridge is only used for research-only classification (no execution
        # without an explicit decider).
        self.research_decider = research_decider
        self._research_bridge: Any = None

    def _get_research_bridge(self) -> Any:
        """Lazily build the 9G research bridge (reusing ResearchWorkflow)."""
        if self._research_bridge is None:
            try:
                from research.orchestrator import ResearchWorkflow
                from research.bridge import ResearchBridge, ResearchIntent

                workflow = ResearchWorkflow(
                    config=self.config,
                    tool_registry=self.tools,
                    permission_manager=None,
                    decider=self.research_decider,
                )
                self._research_bridge = ResearchBridge(
                    workflow, decider=self.research_decider
                )
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.warning("Research bridge unavailable: %s", exc)
                self._research_bridge = False
        return self._research_bridge or None

    def _maybe_research(self, prompt: str) -> Optional[str]:
        """If the prompt needs the research workflow, run it and return text.

        Returns None for ordinary requests so the existing brain path is used
        unchanged. Never executes consequential actions without the configured
        decider.
        """
        bridge = self._get_research_bridge()
        if bridge is None:
            return None
        from research.bridge import ResearchIntent
        intent = bridge.classify(prompt)
        if intent == ResearchIntent.NONE:
            return None
        try:
            return bridge.handle(prompt, intent, decider=self.research_decider)
        except Exception as exc:
            self.logger.error("Research bridge failed: %s", exc)
            return (
                f"I started research but hit an error: {exc}. "
                "Your other requests still work normally."
            )

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

    async def run_stream_async(
        self,
        prompt: str,
        on_chunk: Callable[[str], None] | None = None,
        extra_context: Optional[list[str]] = None,
    ) -> str:
        """Native async streaming - can be awaited directly in FastAPI event loop."""
        streaming = on_chunk is not None
        t0 = time.time()
        q_t0 = time.perf_counter()

        # 9G: route research/plan/execute requests through the research bridge.
        # For research-only/accepted flows we return the complete rendered text
        # as a single answer (the Web UI can later stream progress separately).
        routed = self._maybe_research(prompt)
        if routed is not None:
            if on_chunk:
                on_chunk(routed)
            return routed

        messages = self._build_messages(prompt, extra_context=extra_context)
        q_t1 = time.perf_counter()
        self.logger.debug("[chat] request_queued t0=%.3f q_context=%.3fms", t0, (q_t1 - q_t0) * 1000)

        answer_text = ""
        try:
            if streaming:
                first_token_seen = False
                assembled = ""
                async for part in self._stream_provider(messages):
                    if not first_token_seen:
                        first_token_seen = True
                        self.logger.debug("[chat] first_token_received elapsed=%.3fms", (time.perf_counter() - q_t0) * 1000)
                    if on_chunk:
                        on_chunk(part)
                    assembled += part
                answer_text = assembled
            else:
                answer_text = self._call_provider(messages)
        except Exception as exc:
            self.logger.error("Provider call failed: %s", exc)
            if not answer_text:
                return self._fallback_response(prompt)

        self.logger.debug("[chat] completion elapsed=%.3fms", (time.perf_counter() - q_t0) * 1000)
        return answer_text.strip()

    def run_stream(
        self,
        prompt: str,
        on_chunk: Callable[[str], None] | None = None,
        extra_context: Optional[list[str]] = None,
    ) -> str:
        """Synchronous wrapper for backward compatibility with desktop UI."""
        return asyncio.run(self.run_stream_async(prompt, on_chunk, extra_context))

    def run(self, prompt: str, extra_context: Optional[list[str]] = None) -> str:
        t0 = time.time()
        # 9G: route research/plan/execute requests through the research bridge.
        # Ordinary requests return None here and continue down the existing path.
        routed = self._maybe_research(prompt)
        if routed is not None:
            return routed

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