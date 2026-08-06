"""Tests for intent confidence engine."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


class DummyMemory:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def add_memory(self, *args: Any, **kwargs: Any) -> Any:
        class FakeRecord:
            memory_id = "dummy"
        return FakeRecord()

    def search(self, query: str, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []


class DummyRouter:
    def route(self, prompt: str) -> dict[str, Any] | None:
        return None


def _make_test(prompt: str, expected_intent: str, min_conf: float, expect_destructive: bool = False):
    def test(self):
        result = self.analyzer.analyze(prompt)
        self.assertEqual(result.intent, expected_intent, msg=prompt)
        self.assertGreaterEqual(result.confidence, min_conf, msg=f"{prompt}: {result.confidence}")
        if expect_destructive:
            self.assertEqual(result.strategy.value, "require_confirmation", msg=prompt)
        else:
            self.assertIn(result.strategy.value, {"execute_immediately", "require_confirmation", "ask_clarification", "llm_reasoning"}, msg=prompt)
    return test


cases = [
    ("Open Chrome", "desktop.open_application", 0.85),
    ("Launch VS Code", "desktop.open_application", 0.85),
    ("Open Notepad", "desktop.open_application", 0.9),
    ("Search Python tutorials", "web_search.search", 0.75),
    ("Look up weather", "web_search.search", 0.75),
    ("Find restaurants nearby", "web_search.search", 0.75),
    ("Calculate 4 * 9", "calculator.evaluate", 0.8),
    ("Compute 2+2", "calculator.evaluate", 0.7),
    ("What is 6*7", "calculator.evaluate", 0.7),
    ("Volume up", "system_control.volume_up", 0.85),
    ("Volume down", "system_control.volume_down", 0.85),
    ("Mute", "system_control.mute", 0.85),
    ("Unmute", "system_control.mute", 0.7),
    ("Take screenshot", "desktop.screenshot", 0.8),
    ("Capture screen", "desktop.screenshot", 0.7),
    ("Read README.md", "filesystem.read", 0.75),
    ("Read notes.txt", "filesystem.read", 0.75),
    ("Write file notes.txt", "filesystem.write", 0.7),
    ("Create file todo.txt", "filesystem.write", 0.7),
    ("List files in Downloads", "filesystem.list", 0.75),
    ("List folder Desktop", "filesystem.list", 0.75),
    ("Show files in Documents", "filesystem.list", 0.7),
    ("Delete Downloads folder", "filesystem.delete", 0.7, True),
    ("Delete file temp.txt", "filesystem.delete", 0.7, True),
    ("Shutdown", "system_control.shutdown", 0.8, True),
    ("Shut down now", "system_control.shutdown", 0.8, True),
    ("Restart computer", "system_control.restart", 0.8, True),
    ("Organize my day", "planning.organize_day", 0.55),
    ("Plan my day", "planning.organize_day", 0.55),
    ("Tell me a joke", "llm.chat", 0.0),
    ("What is the meaning of life", "llm.chat", 0.0),
    ("Summarize today's notes", "llm.chat", 0.0),
    ("What's the weather like tomorrow", "llm.chat", 0.0),
    ("Click at 100,200", "desktop.click", 0.7),
    ("Click at 100/200", "desktop.click", 0.7),
    ("Type Hello world", "desktop.type", 0.7),
    ("Press Enter", "desktop.press", 0.7),
    ("Run python script", "code_execution.run", 0.7),
    ("Run code", "llm.chat", 0.0),
    ("Lock screen", "system_control.shutdown", 0.7, True),
    ("Sleep", "system_control.restart", 0.7, True),
    ("Close window", "desktop.click", 0.7),
    ("Full screen", "desktop.click", 0.7),
    ("Minimize window", "desktop.click", 0.7),
    ("Switch to Chrome", "desktop.open_application", 0.7),
    ("Open file README.md", "filesystem.read", 0.75),
    ("Create file output.txt", "filesystem.write", 0.7),
    ("Calculate 4×9", "calculator.evaluate", 0.7),
    ("Calculate 4x9", "calculator.evaluate", 0.7),
    ("Search for Python tutorials", "web_search.search", 0.75),
    ("Look up latest news", "web_search.search", 0.75),
]

class TestIntentResult(unittest.TestCase):
    def test_strategy_immediate(self):
        from modules.intent.result import ExecutionPolicy, IntentResult
        p = ExecutionPolicy()
        r = IntentResult(intent="system_control.mute", confidence=0.999)
        self.assertEqual(p.decide(r).value, "execute_immediately")

    def test_strategy_destructive_confirm(self):
        from modules.intent.result import ExecutionPolicy, IntentResult
        p = ExecutionPolicy()
        r = IntentResult(intent="filesystem.delete", confidence=0.999)
        self.assertEqual(p.decide(r).value, "require_confirmation")

    def test_strategy_clarification(self):
        from modules.intent.result import ExecutionPolicy, IntentResult
        p = ExecutionPolicy()
        r = IntentResult(intent="unknown", confidence=0.75)
        self.assertEqual(p.decide(r).value, "ask_clarification")

    def test_strategy_llm(self):
        from modules.intent.result import ExecutionPolicy, IntentResult
        p = ExecutionPolicy()
        r = IntentResult(intent="unknown", confidence=0.4)
        self.assertEqual(p.decide(r).value, "llm_reasoning")


class TestEntityExtractor(unittest.TestCase):
    def test_open_app(self):
        from modules.intent.entities import EntityExtractor
        ex = EntityExtractor()
        self.assertEqual(ex.extract("desktop.open_application", "Open Chrome"), {"application": "chrome", "application_known": True})
        self.assertEqual(ex.extract("desktop.open_application", "Launch VS Code"), {"application": "vs code", "application_known": True})

    def test_search(self):
        from modules.intent.entities import EntityExtractor
        ex = EntityExtractor()
        self.assertEqual(ex.extract("web_search.search", "Search Python tutorials"), {"query": "python tutorials"})
        self.assertEqual(ex.extract("web_search.search", "Look up weather"), {"query": "weather"})

    def test_math(self):
        from modules.intent.entities import EntityExtractor
        ex = EntityExtractor()
        self.assertEqual(ex.extract("calculator.evaluate", "Calculate 4 * 9"), {"expression": "4 * 9"})
        self.assertEqual(ex.extract("calculator.evaluate", "What is 2+2"), {"expression": "2+2"})

    def test_delete(self):
        from modules.intent.entities import EntityExtractor
        ex = EntityExtractor()
        self.assertEqual(ex.extract("filesystem.delete", "Delete Downloads folder"), {"source": "downloads folder", "target": ""})

    def test_system(self):
        from modules.intent.entities import EntityExtractor
        ex = EntityExtractor()
        self.assertEqual(ex.extract("system_control.mute", "Mute"), {"action": "mute"})
        self.assertEqual(ex.extract("system_control.shutdown", "Shutdown"), {"action": "shutdown"})

    def test_click(self):
        from modules.intent.entities import EntityExtractor
        ex = EntityExtractor()
        self.assertEqual(ex.extract("desktop.click", "Click at 100,200"), {"action": "click", "x": 100, "y": 200})
        self.assertEqual(ex.extract("desktop.click", "Click at 100/200"), {"action": "click", "x": 100, "y": 200})
        self.assertEqual(ex.extract("desktop.click", "Click at 100 200"), {"action": "click", "x": 100, "y": 200})


class TestConfidenceScorer(unittest.TestCase):
    def test_score_formula(self):
        from modules.intent.scorer import ConfidenceScorer
        s = ConfidenceScorer()
        score = s.score(keyword_score=1.0, regex_score=1.0, entity_score=1.0, app_lookup_score=1.0, memory_score=1.0)
        self.assertGreaterEqual(score, 0.98)
        self.assertLessEqual(score, 1.0)

    def test_penalty(self):
        from modules.intent.scorer import ConfidenceScorer
        s = ConfidenceScorer()
        score = s.score(keyword_score=1.0, regex_score=1.0, ambiguity_penalty=1.0)
        self.assertLess(score, 0.9)

    def test_historical_boost(self):
        from modules.intent.scorer import ConfidenceScorer
        s = ConfidenceScorer()
        base = s.score(keyword_score=0.8, regex_score=0.8)
        boosted = s.score(keyword_score=0.8, regex_score=0.8, historical_success=0.95)
        self.assertGreaterEqual(boosted, base)

    def test_record_and_history(self):
        from modules.intent.scorer import ConfidenceScorer
        mem = DummyMemory()
        s = ConfidenceScorer(memory=mem)
        s.record("test.intent", success=True)
        self.assertEqual(s.historical("test.intent"), 1.0)
        s.record("test.intent", success=False)
        self.assertAlmostEqual(s.historical("test.intent"), 0.5)


class TestIntentAnalyzer(unittest.TestCase):
    def setUp(self):
        from modules.intent import IntentAnalyzer
        self.analyzer = IntentAnalyzer(router=DummyRouter(), memory=DummyMemory())

    def test_logging(self):
        log_path = REPO / "logs" / "intent.log"
        if log_path.exists():
            log_path.unlink()
        self.analyzer.analyze("Open Notepad")
        self.assertTrue(log_path.exists(), "intent log missing")
        content = log_path.read_text(encoding="utf-8")
        self.assertIn("desktop.open_application", content)
        self.assertNotIn("Open Notepad", content, "raw prompt must not be logged")

    def test_learn(self):
        self.analyzer.learn("Open Chrome", success=True)
        self.analyzer.learn("Open Chrome", success=True)
        self.analyzer.learn("Open Chrome", success=False)
        hist = self.analyzer.scorer.historical("desktop.open_application")
        self.assertAlmostEqual(hist, 2/3)

    def test_benchmark_average(self):
        prompts = ["Open Chrome", "Search Python", "Calculate 1+1", "Mute", "Tell me a joke", "Read file.txt", "Delete folder", "Shutdown", "Restart", "Organize my day"]
        stats = self.analyzer.benchmark(prompts)
        self.assertEqual(stats["count"], len(prompts))
        self.assertLess(stats["avg_ms"], 5.0, msg=f"avg latency too high: {stats['avg_ms']}ms")

    def test_result_serialization(self):
        from modules.intent.result import IntentResult, ExecutionStrategy
        r = IntentResult(intent="x", confidence=0.5, strategy=ExecutionStrategy.llm_reasoning)
        d = r.to_dict()
        self.assertEqual(d["strategy"], "llm_reasoning")
        self.assertIn("latency_ms", d)


class TestRouterIntegration(unittest.TestCase):
    def test_fast_router_used_as_signal(self):
        from modules.fast_intent import FastIntentRouter
        from modules.tools import ToolRegistry
        from modules.config import JarvisConfig
        from modules.intent import IntentAnalyzer
        config = JarvisConfig(project_root=REPO)
        reg = ToolRegistry(config)
        router = FastIntentRouter(reg)
        analyzer = IntentAnalyzer(router=router, memory=DummyMemory())
        result = analyzer.analyze("Open Notepad")
        self.assertEqual(result.intent, "desktop.open_application")
        self.assertGreaterEqual(result.confidence, 0.8)


for idx, (prompt, expected_intent, min_conf, *rest) in enumerate(cases, start=1):
    expect_destructive = rest[0] if rest else False
    setattr(TestIntentAnalyzer, f"test_route_{idx:02d}_{expected_intent.replace('.', '_')}", _make_test(prompt, expected_intent, min_conf, expect_destructive))


if __name__ == "__main__":
    unittest.main()
