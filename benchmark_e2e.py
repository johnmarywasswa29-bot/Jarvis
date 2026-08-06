"""End-to-end performance benchmark for Jarvis.

Benchmarks:
- Startup/init cost
- Tool paths: deterministic routing + execution
- Ollama warm single-call latency (timeout configurable)

On CPU-only hardware, Ollama latency dominates chat; the pipeline itself
targets sub-100ms tool stage and sub-1s chat stage excluding LLM.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
os.environ["JARVIS_BENCH"] = "1"

from modules.config import JarvisConfig
from modules.perf import enable, clear, events, summary
from modules.tools import ToolRegistry
from modules.brain_graph import JarvisBrain, OllamaLLM
from modules.memory_v2 import JarvisMemoryV2


def make_pipeline():
    config = JarvisConfig(project_root=REPO)
    mem = JarvisMemoryV2(config)
    reg = ToolRegistry(config)
    brain = JarvisBrain(config, reg, mem)
    return config, mem, reg, brain


def bench_tool(brain, prompt: str) -> dict[str, Any]:
    clear()
    enable()
    t0 = time.perf_counter()
    try:
        answer = brain.run(prompt)
    except Exception as exc:
        answer = f"ERROR: {exc}"
    dt = time.perf_counter() - t0
    return {
        "prompt": prompt,
        "answer": answer,
        "total_s": dt,
        "events": events(),
    }


def bench_ollama(timeout: float = 120.0) -> dict[str, Any]:
    config = JarvisConfig(project_root=REPO)
    llm = OllamaLLM(config)
    out: dict[str, Any] = {}
    try:
        clear()
        enable()
        t0 = time.perf_counter()
        out["warm_answer"] = llm.chat_with_timeout("Say OK", timeout=timeout)
        out["warm_s"] = time.perf_counter() - t0
    except Exception as exc:
        out["warm_answer"] = f"ERROR: {exc}"
        out["warm_s"] = None
    out["events"] = events()
    return out


def bench_startup() -> dict[str, Any]:
    clear()
    enable()
    t0 = time.perf_counter()
    config, mem, reg, brain = make_pipeline()
    dt = time.perf_counter() - t0
    return {"total_s": dt, "events": events()}


def main() -> None:
    print("=== Jarvis End-to-End Benchmark ===\n")

    print("--- Tool Paths ---")
    config, mem, reg, brain = make_pipeline()
    for prompt in ["open notepad", "calculate 2 + 2"]:
        res = bench_tool(brain, prompt)
        print(f"Prompt: {prompt}")
        print(f"Answer: {res['answer']}")
        print(f"Total: {res['total_s']*1000:.2f} ms")
        for ev in res["events"]:
            print(f"  {ev['name']}: {ev['elapsed_ms']:.2f} ms [{ev.get('stage')}]")
        print()

    print("--- Startup ---")
    startup = bench_startup()
    print(f"Total init: {startup['total_s']*1000:.2f} ms")
    for ev in startup["events"]:
        print(f"  {ev['name']}: {ev['elapsed_ms']:.2f} ms [{ev.get('stage')}]")
    print()

    print("--- LLM Baseline ---")
    ollama = bench_ollama()
    if ollama.get("warm_s") is not None:
        print(f"Warm single Ollama call: {ollama['warm_s']*1000:.2f} ms")
        print(f"Response: {ollama['warm_answer']!r}")
    else:
        print(ollama["warm_answer"])
    print()

    print("--- Perf Summary ---")
    print(summary())


if __name__ == "__main__":
    main()
