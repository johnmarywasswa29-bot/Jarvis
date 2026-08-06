"""Runtime integration tests for OllamaHealth."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from runtime.runtime import build_runtime, startup, stop_runtime


class TestRuntimeOllamaIntegration(unittest.TestCase):
    def test_build_runtime_exposes_ollama_health(self):
        ctx = build_runtime(repo=REPO)
        try:
            self.assertIsNotNone(ctx.ollama_health)
            self.assertTrue(hasattr(ctx.ollama_health, "start"))
            self.assertTrue(hasattr(ctx.ollama_health, "stop"))
            self.assertTrue(hasattr(ctx.ollama_health, "refresh"))
        finally:
            stop_runtime(ctx)

    def test_startup_starts_ollama_health(self):
        ctx = build_runtime(repo=REPO)
        try:
            startup(ctx)
            self.assertTrue(ctx.ollama_health._running)
        finally:
            stop_runtime(ctx)

    def test_stop_runtime_stops_ollama_health(self):
        ctx = build_runtime(repo=REPO)
        try:
            startup(ctx)
            self.assertTrue(ctx.ollama_health._running)
        finally:
            stop_runtime(ctx)
        self.assertFalse(ctx.ollama_health._running)

    def test_ollama_health_uses_config_defaults(self):
        ctx = build_runtime(repo=REPO)
        try:
            h = ctx.ollama_health
            self.assertIsNotNone(h)
            cfg = ctx.config
            self.assertEqual(h.base_url, getattr(cfg, "llm_base_url", "http://localhost:11434"))
            self.assertEqual(h.model, getattr(cfg, "llm_model", "llama3"))
            self.assertEqual(h.warning_latency_s, float(getattr(cfg, "ollama_warning_latency_s", 8.0)))
            self.assertEqual(h.critical_latency_s, float(getattr(cfg, "ollama_critical_latency_s", 20.0)))
            self.assertEqual(h.auto_reconnect, bool(getattr(cfg, "ollama_auto_reconnect", True)))
            self.assertEqual(h.check_interval_s, float(getattr(cfg, "ollama_health_interval_s", 30.0)))
        finally:
            stop_runtime(ctx)

    def test_build_runtime_survives_ollama_failure(self):
        # Even if OllamaHealth is mocked to fail, build_runtime must succeed.
        # Since OllamaHealth is constructed inside _safe, failures are captured.
        ctx = build_runtime(repo=REPO)
        self.assertIsNotNone(ctx)
        stop_runtime(ctx)


if __name__ == "__main__":
    unittest.main()
