"""
JarvisBrain - redesigned execution pipeline.

Routing order:
1. FastIntentRouter: deterministic, no LLM for obvious tool commands.
2. Simple chat: single Ollama call, no LangGraph.
3. Complex reasoning: optional LangGraph planner/executor/reflection.

LLM calls are bounded by configurable timeouts and fallback to canned responses.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

from modules.config import JarvisConfig
from modules.logger import get_logger
from modules.memory import JarvisMemory
from modules.memory_v2 import JarvisMemoryV2
from modules.tools import ToolRegistry
from modules.fast_intent import FastIntentRouter

try:
    from langgraph.graph import END, StateGraph  # type: ignore
    _HAS_LANGGRAPH = True
except Exception:
    _HAS_LANGGRAPH = False

logger = get_logger("brain")

SYSTEM_PROMPT = (
    "You are Jarvis, a private local AI assistant running on the user's Windows laptop. "
    "You are helpful, concise, and can use tools when needed. "
    "Available tools: web_search, desktop_control, code_execution, filesystem. "
    "When a tool is needed, respond with ONLY a JSON object in this format:\n"
    '{"tool": "tool_name", "args": {...}}\n'
    "Otherwise just answer conversationally in plain text."
)

COMPLEX_KEYWORDS = [
    "plan", "steps", "research", "compare", "analyze", "analyze", "design", "implement", "debug", "refactor", "multi", "project", "workflow", "goal", "strategy"
]

_LLM_PROFILE_LOG_NAME = "llm_profile.log"
_OLLAMA_BENCH_LOG_NAME = "ollama_benchmark.log"


def _estimated_tokens(text: str) -> int:
    try:
        import tiktoken  # type: ignore
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def _log_llm_profile(prompt: str, first_token_s: Optional[float], total_s: float, tokens_generated: int) -> None:
    try:
        from pathlib import Path
        system_size = len(SYSTEM_PROMPT)
        prompt_size = len(prompt)
        prompt_tokens = _estimated_tokens(prompt)
        system_tokens = _estimated_tokens(SYSTEM_PROMPT)
        tps = tokens_generated / total_s if total_s > 0 else 0.0
        first_token_label = f"{first_token_s:.3f}" if first_token_s is not None else "n/a"
        line = (
            f"prompt_chars={prompt_size} prompt_tokens~={prompt_tokens} "
            f"system_chars={system_size} system_tokens~={system_tokens} "
            f"first_token_s={first_token_label} "
            f"total_s={total_s:.3f} tokens_generated={tokens_generated} tokens_per_s={tps:.2f}\n"
        )
        log_path = Path(__file__).resolve().parent.parent / "logs" / _LLM_PROFILE_LOG_NAME
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _log_ollama_benchmark(line: str) -> None:
    try:
        from pathlib import Path
        log_path = Path(__file__).resolve().parent.parent / "logs" / _OLLAMA_BENCH_LOG_NAME
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


class OllamaLLM:
    def __init__(self, config: JarvisConfig, *, ollama_health: Any = None) -> None:
        self.config = config
        self.logger = get_logger("ollama")
        self.timeout_s = float(getattr(config, "llm_timeout_s", 12))
        self.ollama_health = ollama_health
        self._client = None
        self._model_loaded = False

    def _get_client(self):
        if self._client is None:
            try:
                from ollama import Client  # type: ignore
                self._client = Client(host=self.config.llm_base_url)
            except Exception:
                return None
        return self._client

    def _warmup_model(self, model: str) -> None:
        client = self._get_client()
        if client is None or self._model_loaded:
            return
        try:
            client.chat(
                model=model,
                messages=[{"role": "user", "content": "Say OK"}],
                options={"num_predict": 1, "temperature": 0.0},
                keep_alive=-1,
            )
            self._model_loaded = True
        except Exception:
            pass

    def chat(self, prompt: str, *, model: Optional[str] = None, stream: bool = False, on_chunk: Any = None) -> str:
        if not prompt:
            return ""
        use_model = model or self.config.llm_model
        client = self._get_client()
        if client is None:
            return ""
        first_token_s: Optional[float] = None
        tokens_generated = 0
        model_load_s = None
        generation_s = None
        t0 = time.perf_counter()
        try:
            if stream:
                response = client.chat(
                    model=use_model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    options={"num_predict": 512, "temperature": 0.1},
                    stream=True,
                    keep_alive=-1,
                )
                assembled = ""
                for chunk in response:
                    msg = getattr(chunk, "message", None)
                    if msg is not None:
                        token = getattr(msg, "content", "") or ""
                        if token:
                            if first_token_s is None:
                                first_token_s = time.perf_counter() - t0
                            assembled += token
                            tokens_generated += 1
                            if callable(on_chunk):
                                on_chunk(token)
                total_s = time.perf_counter() - t0
                generation_s = total_s - (first_token_s if first_token_s is not None else 0.0)
                _log_llm_profile(prompt, first_token_s, total_s, tokens_generated)
                _log_ollama_benchmark(
                    f"model={use_model} prompt_tokens~={_estimated_tokens(prompt)} first_token_s={first_token_s if first_token_s is not None else -1:.3f} "
                    f"total_s={total_s:.3f} tokens_generated={tokens_generated} tokens_per_s={tokens_generated/total_s if total_s>0 else 0:.2f} "
                    f"model_load_s={model_load_s if model_load_s is not None else -1:.3f} generation_s={generation_s:.3f} stream=True"
                )
                return assembled.strip()
            response = client.chat(
                model=use_model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                options={"num_predict": 512, "temperature": 0.1},
                keep_alive=-1,
            )
        except Exception as exc:
            self.logger.error("Ollama chat failed: %s", exc)
            total_s = time.perf_counter() - t0
            _log_llm_profile(prompt, None, total_s, 0)
            return ""
        total_s = time.perf_counter() - t0
        tokens_generated = 0
        generation_s = total_s
        msg = getattr(response, "message", None)
        if msg is not None:
            content = getattr(msg, "content", "") or ""
            tokens_generated = len(content.split())
        _log_llm_profile(prompt, first_token_s, total_s, tokens_generated)
        _log_ollama_benchmark(
            f"model={use_model} prompt_tokens~={_estimated_tokens(prompt)} first_token_s={first_token_s if first_token_s is not None else -1:.3f} "
            f"total_s={total_s:.3f} tokens_generated={tokens_generated} tokens_per_s={tokens_generated/total_s if total_s>0 else 0:.2f} "
            f"model_load_s={model_load_s if model_load_s is not None else -1:.3f} generation_s={generation_s:.3f} stream=False"
        )
        if msg is None:
            return ""
        return (getattr(msg, "content", "") or "").strip()

    def chat_with_timeout(self, prompt: str, *, timeout: Optional[float] = None, on_chunk: Any = None) -> str:
        timeout = timeout or self.timeout_s
        t0 = time.perf_counter()
        try:
            from concurrent.futures import ThreadPoolExecutor
        except Exception:
            try:
                if callable(on_chunk):
                    out = self.chat(prompt, stream=True, on_chunk=on_chunk)
                else:
                    out = self.chat(prompt)
                _log_llm_profile(prompt, None, time.perf_counter() - t0, len(out.split()) if out else 0)
                return out
            except Exception:
                return ""
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                if callable(on_chunk):
                    future = pool.submit(self.chat, prompt, stream=True, on_chunk=on_chunk)
                else:
                    future = pool.submit(self.chat, prompt)
                out = future.result(timeout=timeout)
                if out:
                    _log_llm_profile(prompt, None, time.perf_counter() - t0, len(out.split()))
                return out or ""
        except TimeoutError:
            self.logger.warning("Ollama timeout after %.1fs", timeout)
            _log_llm_profile(prompt, None, time.perf_counter() - t0, 0)
            return ""
        except Exception as exc:
            self.logger.error("Ollama chat_with_timeout error: %s", exc)
            _log_llm_profile(prompt, None, time.perf_counter() - t0, 0)
            return ""


class JarvisBrain:
    def __init__(
        self,
        config: JarvisConfig,
        tools: Any,
        memory: Any,
        *,
        use_graph: bool = True,
    ) -> None:
        self.config = config
        self.tools = tools
        self.logger = get_logger("brain")
        self.memory_v2 = memory if isinstance(memory, JarvisMemoryV2) else None
        self.memory_v1 = memory if self.memory_v2 is None else None
        self.llm = OllamaLLM(config)
        self.router = FastIntentRouter(tools)

        self._graph = None
        if use_graph and _HAS_LANGGRAPH:
            try:
                self._build_graph()
            except Exception as exc:
                self.logger.warning("Graph disabled: %s", exc)

    # ------------------ graph ------------------
    def _build_graph(self) -> None:
        graph = StateGraph(dict)

        def run_router(st: dict[str, Any]) -> dict[str, Any]:
            return self._node_router(st)

        def run_planner(st: dict[str, Any]) -> dict[str, Any]:
            return self._node_planner(st)

        def run_executor(st: dict[str, Any]) -> dict[str, Any]:
            return self._node_executor(st)

        def run_reflection(st: dict[str, Any]) -> dict[str, Any]:
            return self._node_reflection(st)

        graph.add_node("router", run_router)
        graph.add_node("planner", run_planner)
        graph.add_node("executor", run_executor)
        graph.add_node("reflection", run_reflection)

        graph.set_entry_point("router")
        graph.add_edge("router", "planner")
        graph.add_edge("planner", "executor")
        graph.add_edge("executor", "reflection")

        def after_reflection(st: dict[str, Any]) -> str:
            if st.get("reflection") in {"refine", "fallback"}:
                return END
            return "planner"

        graph.add_conditional_edges("reflection", after_reflection)
        self._graph = graph.compile()

    def _node_router(self, st: dict[str, Any]) -> dict[str, Any]:
        return self._route(st)

    def _node_planner(self, st: dict[str, Any]) -> dict[str, Any]:
        return self._plan(st)

    def _node_executor(self, st: dict[str, Any]) -> dict[str, Any]:
        return self._execute(st)

    def _node_reflection(self, st: dict[str, Any]) -> dict[str, Any]:
        return self._reflect(st)

    def _route(self, state: dict[str, Any]) -> dict[str, Any]:
        transcript = state.get("transcript", "")
        intent = self.router.route(transcript)
        if intent:
            state.setdefault("selected_tools", [])
            if intent.get("tool") not in state["selected_tools"]:
                state["selected_tools"].append(intent.get("tool"))
            state["needs_tool"] = True
            state["tool_intent"] = intent
            self.logger.info("Router -> planner: %s", state["selected_tools"])
            return state
        state["needs_tool"] = False
        self.logger.info("Router -> planner: no tools matched")
        return state

    def _plan(self, state: dict[str, Any]) -> dict[str, Any]:
        transcript = state.get("transcript", "")
        mem = self.memory_v2 or self.memory_v1
        recent = ""
        try:
            recent = mem.get_recent_context()
        except Exception:
            pass
        state["plan"] = (
            "Tools: " + ", ".join(state.get("selected_tools", []) or ["none"]) + "\n"
            "Recent context:\n"
            f"{recent}\n\n"
            f"User: {transcript}"
        ).strip()
        return state

    def _execute(self, state: dict[str, Any]) -> dict[str, Any]:
        transcript = state.get("transcript", "")
        selected = state.get("selected_tools", [])
        mem = self.memory_v2 or self.memory_v1
        results: list[dict[str, Any]] = []

        if selected:
            for tool_name in selected:
                tool = next((t for t in self.tools.tools if t.name == tool_name and t.enabled), None)
                if tool is None:
                    state.setdefault("error", "")
                    state["error"] += f"Tool '{tool_name}' not available. "
                    continue
                intent = state.get("tool_intent") or {}
                kwargs = intent.get("args", {}) or {}
                kwargs.setdefault("prompt", transcript)
                t0 = time.time()
                result = self.tools.run_tool(tool, transcript, **kwargs)
                results.append(
                    {
                        "tool": tool.name,
                        "success": result.success,
                        "output": result.output,
                        "error": result.error,
                        "duration_s": getattr(result, "duration_s", time.time() - t0),
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
            answer = self.llm.chat_with_timeout(followup_prompt)
            state["answer"] = answer.strip()
            try:
                mem.add_message("assistant", state["answer"])
            except Exception:
                pass
            return state

        answer = self.llm.chat_with_timeout(transcript)
        state["answer"] = answer.strip() or "I couldn't reach the local language model. Ollama may be offline."
        try:
            mem.add_message("assistant", state["answer"])
        except Exception:
            pass
        return state

    def _reflect(self, state: dict[str, Any]) -> dict[str, Any]:
        state["reflection"] = "refine"
        retries = int(state.get("retries", 0))
        if not state.get("answer") and state.get("tool_results"):
            state["answer"] = "Tool action did not complete successfully."
            state["reflection"] = "fallback"
        elif len(state.get("answer", "").split()) < 8 and retries == 0 and state.get("needs_tool"):
            state["retries"] = 1
            state["reflection"] = "retry"
        return state

    # ------------------ public API ------------------
    def _fallback_response(self, prompt: str) -> str:
        p = prompt.lower()
        if any(x in p for x in ["hello", "hi ", "hi,", "hey"]):
            return "Hello. How can I help?"
        if "thank" in p:
            return "You're welcome."
        if any(x in p for x in ["who are you", "your name"]):
            return "I'm Jarvis, your local assistant."
        return "I couldn't reach the local language model. Ollama may be offline. Try starting Ollama and retrying."

    def _parse_tool_call(self, text: str) -> Optional[dict[str, Any]]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict) and "tool" in data and "args" in data:
            return data
        return None

    def _is_complex(self, prompt: str) -> bool:
        low = prompt.lower()
        return any(k in low for k in COMPLEX_KEYWORDS)

    def run(self, prompt: str, extra_context: Optional[list[str]] = None) -> str:
        mem = self.memory_v2 or self.memory_v1
        try:
            mem.add_message("user", prompt)
        except Exception:
            pass

        intent = self.router.route(prompt)
        if intent:
            tool_name = intent.get("tool", "")
            tool = next((t for t in self.tools.tools if t.name == tool_name and t.enabled), None)
            if tool:
                kwargs = intent.get("args", {}) or {}
                kwargs.setdefault("prompt", prompt)
                result = self.tools.run_tool(tool, **kwargs)
                if result.success:
                    answer = result.output.strip() or f"Done: {tool_name}."
                else:
                    answer = result.error.strip() or f"{tool_name} did not complete successfully."
                try:
                    mem.add_message("assistant", answer)
                except Exception:
                    pass
                return answer
            return f"Tool '{tool_name}' is not available."

        if self._graph is not None and self._is_complex(prompt):
            try:
                out = self._graph.invoke(
                    {
                        "transcript": prompt,
                        "context": list(extra_context or []),
                        "plan": "",
                        "selected_tools": [],
                        "tool_results": [],
                        "answer": "",
                        "reflection": "",
                        "needs_tool": False,
                        "retries": 0,
                        "error": "",
                    }
                )
                if isinstance(out, dict):
                    answer = (out.get("answer") or "").strip()
                    if answer:
                        return answer
            except Exception as exc:
                self.logger.error("Graph run failed: %s", exc)

        return self._simple_chat(prompt, extra_context=extra_context)

    def _simple_chat(self, prompt: str, *, extra_context: Optional[list[str]] = None, on_chunk: Any = None) -> str:
        mem = self.memory_v2 or self.memory_v1
        context = ""
        try:
            context = mem.get_recent_context()
        except Exception:
            pass
        extra = "\n".join(extra_context) if extra_context else ""
        full_prompt = (
            "Recent conversation:\n"
            f"{context}\n\n"
            f"{extra}\n\n"
            f"User: {prompt}\n"
            "Jarvis:"
        )
        streaming = callable(on_chunk)
        try:
            from modules.perf import record as perf_record
            t0 = time.perf_counter()
            if streaming:
                answer = self.llm.chat(full_prompt, stream=True, on_chunk=on_chunk)
            else:
                answer = self.llm.chat_with_timeout(full_prompt)
            perf_record("llm.simple_chat", start=t0, end=time.perf_counter(), stage="llm")
            try:
                token_count = len(answer.split()) if answer else 0
                _log_llm_profile(full_prompt, None, time.perf_counter() - t0, token_count)
            except Exception:
                pass
        except Exception as exc:
            self.logger.error("Simple chat failed: %s", exc)
            return self._fallback_response(prompt)
        if not answer:
            return self._fallback_response(prompt)
        self.logger.debug("[chat] prompt_build_s=%.3f total_prompt_chars=%d", time.perf_counter() - t0, len(full_prompt))
        return answer

    def run_stream(self, prompt: str, on_chunk: Any = None, extra_context: Optional[list[str]] = None) -> str:
        mem = self.memory_v2 or self.memory_v1
        try:
            mem.add_message("user", prompt)
        except Exception:
            pass

        streaming = callable(on_chunk)

        # Fast path: deterministic tools before any LLM call.
        intent = self.router.route(prompt)
        if intent:
            tool_name = intent.get("tool", "")
            tool = next((t for t in self.tools.tools if t.name == tool_name and t.enabled), None)
            if tool:
                kwargs = intent.get("args", {}) or {}
                kwargs.setdefault("prompt", prompt)
                result = self.tools.run_tool(tool, **kwargs)
                if result.success:
                    answer = result.output.strip() or f"Done: {tool_name}."
                else:
                    answer = result.error.strip() or f"{tool_name} did not complete successfully."
                try:
                    mem.add_message("assistant", answer)
                except Exception:
                    pass
                return answer
            return f"Tool '{tool_name}' is not available."

        t0 = time.time()
        q_t0 = time.perf_counter()
        context = ""
        try:
            context = mem.get_recent_context()
        except Exception:
            pass
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
            if streaming:
                assembled = ""
                for part in self.llm.chat(full_prompt, stream=True, on_chunk=on_chunk):
                    assembled += part
                answer_text = assembled
            else:
                answer_text = self.llm.chat_with_timeout(full_prompt)
        except Exception as exc:
            self.logger.error("Ollama streaming failed: %s", exc)
            if not answer_text:
                return self._fallback_response(prompt)

        self.logger.debug("[chat] completion elapsed=%.3fms", (time.perf_counter() - q_t0) * 1000)
        tool_call = self._parse_tool_call(answer_text)
        if tool_call:
            return self.run(prompt, extra_context=extra_context)
        try:
            mem.add_message("assistant", answer_text.strip() or "")
        except Exception:
            pass
        return answer_text.strip()
