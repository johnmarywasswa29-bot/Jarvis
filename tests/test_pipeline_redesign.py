"""Tests for redesigned pipeline: FastIntentRouter, timeout config, calculator, system control, app launch."""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


class DummyConfig:
    llm_model = "llama3"
    llm_base_url = "http://localhost:11434"
    llm_fallback_model = "llama2"
    llm_timeout_s = 12.0
    project_root = REPO
    memory_persist_directory = str(REPO / "memory")
    memory_collection = "jarvis_memory"
    downloads = str(Path.home() / "Downloads")
    desktop = str(Path.home() / "Desktop")
    documents = str(Path.home() / "Documents")

    def memory_path(self):
        return Path(self.memory_persist_directory)

    def stt_models_dir(self):
        return REPO / "assets" / "stt_models"

    def wake_word_path(self):
        return REPO / "assets" / "jarvis.ppn"


class TestFastIntentRouter(unittest.TestCase):
    def test_desktop_command_routes_without_llm(self):
        from modules.fast_intent import FastIntentRouter
        reg = MagicMock()
        tool = MagicMock()
        tool.enabled = True
        tool.name = "desktop_control"
        reg.tools = [tool]
        router = FastIntentRouter(reg)
        intent = router.route("open notepad")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["tool"], "desktop_control")
        self.assertEqual(intent["args"]["action"], "open_app")

    def test_web_search_routes_without_llm(self):
        from modules.fast_intent import FastIntentRouter
        reg = MagicMock()
        tool = MagicMock()
        tool.enabled = True
        tool.name = "web_search"
        reg.tools = [tool]
        router = FastIntentRouter(reg)
        intent = router.route("search python tutorials")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["tool"], "web_search")
        self.assertIn("python tutorials", intent["args"]["query"])

    def test_filesystem_list_routes_without_llm(self):
        from modules.fast_intent import FastIntentRouter
        reg = MagicMock()
        tool = MagicMock()
        tool.enabled = True
        tool.name = "filesystem"
        reg.tools = [tool]
        router = FastIntentRouter(reg)
        intent = router.route("list files in Downloads")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["tool"], "filesystem")
        self.assertEqual(intent["args"]["action"], "list")

    def test_math_routes_to_calculator_without_llm(self):
        from modules.fast_intent import FastIntentRouter
        reg = MagicMock()
        reg.tools = []
        router = FastIntentRouter(reg)
        intent = router.route("calculate 2 + 2")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["tool"], "calculator")
        self.assertEqual(intent["args"]["expression"], "calculate 2 + 2")

    def test_chitchat_returns_none_for_llm(self):
        from modules.fast_intent import FastIntentRouter
        reg = MagicMock()
        reg.tools = []
        router = FastIntentRouter(reg)
        intent = router.route("Hello, how are you?")
        self.assertIsNone(intent)


class TestToolExecutionLatency(unittest.TestCase):
    def test_calculator_under_200ms(self):
        from modules.tools import CalculatorTool
        tool = CalculatorTool()
        t0 = time.perf_counter()
        result = tool.execute(expression="2 + 2")
        dt = time.perf_counter() - t0
        self.assertTrue(result.success)
        self.assertLess(dt, 0.2, f"calculator too slow: {dt:.3f}s")
        self.assertEqual(result.output.strip(), "4")

    def test_web_search_tool_registry_creates(self):
        from modules.config import JarvisConfig
        from modules.tools import ToolRegistry
        config = JarvisConfig(project_root=REPO)
        reg = ToolRegistry(config)
        names = [t.name for t in reg.tools]
        self.assertIn("web_search", names)
        self.assertIn("desktop_control", names)
        self.assertIn("calculator", names)
        self.assertIn("system_control", names)


class TestLlmTimeoutConfig(unittest.TestCase):
    def test_default_timeout(self):
        from modules.config import JarvisConfig
        c = JarvisConfig(project_root=REPO)
        self.assertEqual(c.llm_timeout_s, 12.0)

    def test_custom_timeout_from_yaml(self):
        yaml_path = REPO / "config.yaml"
        if not yaml_path.exists():
            self.skipTest("missing config.yaml")
        from modules.config import JarvisConfig
        c = JarvisConfig.from_yaml(yaml_path)
        self.assertGreaterEqual(c.llm_timeout_s, 1.0)


class TestBrainGraphPreserved(unittest.TestCase):
    def test_graph_available(self):
        try:
            from langgraph.graph import StateGraph  # type: ignore
            imported = True
        except Exception:
            imported = False
        from modules.brain_graph import _HAS_LANGGRAPH
        self.assertEqual(imported, _HAS_LANGGRAPH)


if __name__ == "__main__":
    unittest.main()
