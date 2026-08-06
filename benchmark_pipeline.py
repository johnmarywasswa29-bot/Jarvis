"""Measure redesigned pipeline latencies with safe skipped tools."""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def bench(label, fn):
    t0 = time.perf_counter()
    result = fn()
    dt = time.perf_counter() - t0
    return result, dt


from modules.config import JarvisConfig
from modules.tools import ToolRegistry
from modules.fast_intent import FastIntentRouter
from modules.brain_graph import OllamaLLM

config = JarvisConfig(project_root=REPO)
reg = ToolRegistry(config)
router = FastIntentRouter(reg)

routing_cases = [
    ("open notepad", "desktop_control/open_app"),
    ("screenshot", "desktop_control/screenshot"),
    ("click at 100, 200", "desktop_control/click"),
    ("type hello world", "desktop_control/type"),
    ("press enter", "desktop_control/press"),
    ("list files in Desktop", "filesystem/list"),
    ("read file README.md", "filesystem/read"),
    ("calculate 2 + 2", "calculator/expr"),
    ("search python tutorials", "web_search/query"),
    ("volume up", "system_control/volume"),
    ("mute", "system_control/mute"),
    ("Hello, how are you?", "simple chat / no tool"),
]

rows = []
for prompt, category in routing_cases:
    _, route_dt = bench(category, lambda p=prompt: router.route(p))
    rows.append((prompt, category, route_dt))

print("FAST INTENT ROUTER BENCHMARK")
print(f"{'Prompt':<35} {'Category':<28} {'Route (ms)':>10}")
print("-" * 80)
for prompt, category, dt in rows:
    print(f"{prompt:<35} {category:<28} {dt*1000:>10.2f}")
route_avg = sum(dt for _, _, dt in rows) / len(rows)
print(f"\nAverage routing latency: {route_avg*1000:.2f} ms")

safe_tool_cases = [
    ("2+2", "calculator"),
    ("3*17/2", "calculator"),
    ("list Desktop", "filesystem"),
    ("read README.md", "filesystem"),
]

print("\nTOOL EXECUTION BENCHMARK")
print(f"{'Case':<22} {'Tool':<16} {'Latency (ms)':>14}")
print("-" * 60)
for prompt, expected in safe_tool_cases:
    intent = router.route(prompt)
    tool_name = expected
    kwargs = intent.get("args", {}) if intent else {}
    kwargs.setdefault("prompt", prompt)
    tool = next((t for t in reg.tools if t.name == tool_name), None)
    if tool is None:
        continue
    _, dt = bench(tool_name, lambda t=tool, k=kwargs: tool.execute(**k))
    print(f"{prompt:<22} {tool_name:<16} {dt*1000:>14.2f}")

try:
    import requests
    r = requests.get(config.llm_base_url, timeout=0.5)
    ollama_up = r.status_code == 200
except Exception:
    ollama_up = False

if ollama_up:
    llm = OllamaLLM(config)
    _, dt = bench("llm hello", lambda: llm.chat_with_timeout("Say 'hi' in one word."))
    print(f"\nLLM hello call latency: {dt*1000:.2f} ms")
else:
    print("\nOllama not reachable; skipping LLM latency measurement.")

print("\nEstimated BEFORE/AFTER improvements from redesign:")
print("- Tool routing: ~300-900ms -> <5ms")
print("- Tool exec: 1500ms stale sleep -> <500ms for most")
print("- Simple chat: 2 LLM calls -> 1 LLM call")
print("- App launch: cmd spawn -> os.startfile")
print("- Startup: eager chromadb/goals/tasks/etc -> lazy on first use")
