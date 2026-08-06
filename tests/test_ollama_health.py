"""Comprehensive tests for core/ollama_health.py."""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from core.ollama_health import (
    OllamaHealth,
    OllamaHealthSnapshot,
    OllamaHealthState,
)


class TestOllamaHealthDefaults(unittest.TestCase):
    def test_defaults(self):
        h = OllamaHealth()
        self.assertEqual(h.base_url, "http://localhost:11434")
        self.assertEqual(h.model, "llama3")
        self.assertEqual(h.warning_latency_s, 8.0)
        self.assertEqual(h.critical_latency_s, 20.0)
        self.assertTrue(h.auto_reconnect)
        self.assertEqual(h.check_interval_s, 30.0)

    def test_snapshot_initial_state(self):
        h = OllamaHealth()
        snap = h.snapshot()
        self.assertEqual(snap.state, OllamaHealthState.UNREACHABLE)
        self.assertEqual(snap.model, "")
        self.assertEqual(snap.installed_models, [])
        self.assertEqual(snap.recovery_attempts, 0)

    def test_current_state(self):
        h = OllamaHealth()
        self.assertEqual(h.current_state(), OllamaHealthState.UNREACHABLE)

    def test_is_available(self):
        h = OllamaHealth()
        self.assertFalse(h.is_available())

    def test_is_degraded(self):
        h = OllamaHealth()
        self.assertFalse(h.is_degraded())


class TestOllamaHealthOffline(unittest.TestCase):
    def test_list_models_offline(self):
        h = OllamaHealth(base_url="http://127.0.0.1:59999")
        models = h.list_models()
        self.assertIsInstance(models, list)
        self.assertEqual(models, [])

    def test_model_exists_offline(self):
        h = OllamaHealth(base_url="http://127.0.0.1:59999")
        self.assertFalse(h.model_exists("llama3"))

    def test_model_size_offline(self):
        h = OllamaHealth(base_url="http://127.0.0.1:59999")
        self.assertIsNone(h.model_size("llama3"))

    def test_recommended_models_offline(self):
        h = OllamaHealth(base_url="http://127.0.0.1:59999")
        self.assertEqual(h.recommended_models(), [])

    def test_refresh_offline(self):
        h = OllamaHealth(base_url="http://127.0.0.1:59999")
        snap = h.refresh()
        self.assertEqual(snap.state, OllamaHealthState.OFFLINE)
        self.assertIn("Ollama", snap.error)

    def test_diagnostics_offline(self):
        h = OllamaHealth(base_url="http://127.0.0.1:59999")
        h.refresh()
        d = h.diagnostics()
        self.assertEqual(d["state"], "offline")
        self.assertIn("configured_model_exists", d)
        self.assertIn("recovery_attempts", d)

    def test_attempt_recovery_offline(self):
        h = OllamaHealth(base_url="http://127.0.0.1:59999")
        snap = h.attempt_recovery()
        self.assertIn(snap.state, {OllamaHealthState.OFFLINE, OllamaHealthState.DEGRADED})

    def test_wait_until_ready_times_out(self):
        h = OllamaHealth(base_url="http://127.0.0.1:59999")
        result = h.wait_until_ready(timeout=0.5, poll=0.1)
        self.assertFalse(result)

    def test_background_start_stop(self):
        h = OllamaHealth(base_url="http://127.0.0.1:59999")
        h.start()
        time.sleep(0.1)
        self.assertTrue(h._running)
        h.stop()
        time.sleep(0.1)
        self.assertFalse(h._running)


class TestOllamaHealthStateMachine(unittest.TestCase):
    def test_loading_state(self):
        h = OllamaHealth()
        snap = OllamaHealthSnapshot(state=OllamaHealthState.LOADING, model="llama3")
        h._snapshot = snap
        self.assertTrue(h.is_available())
        self.assertFalse(h.is_degraded())

    def test_busy_state(self):
        h = OllamaHealth()
        snap = OllamaHealthSnapshot(state=OllamaHealthState.BUSY, model="llama3")
        h._snapshot = snap
        self.assertTrue(h.is_available())
        self.assertFalse(h.is_degraded())

    def test_slow_state(self):
        h = OllamaHealth()
        snap = OllamaHealthSnapshot(state=OllamaHealthState.SLOW, model="llama3")
        h._snapshot = snap
        self.assertTrue(h.is_available())
        self.assertFalse(h.is_degraded())

    def test_model_missing_state(self):
        h = OllamaHealth()
        snap = OllamaHealthSnapshot(state=OllamaHealthState.MODEL_MISSING, model="llama3")
        h._snapshot = snap
        self.assertFalse(h.is_available())
        self.assertFalse(h.is_degraded())

    def test_degraded_state(self):
        h = OllamaHealth()
        snap = OllamaHealthSnapshot(state=OllamaHealthState.DEGRADED, model="llama3")
        h._snapshot = snap
        # Degraded mode keeps the application usable, so it is still
        # considered "available" by is_available(). The is_degraded() flag
        # provides the precise degraded classification.
        self.assertTrue(h.is_available())
        self.assertTrue(h.is_degraded())

    def test_error_state(self):
        h = OllamaHealth()
        snap = OllamaHealthSnapshot(state=OllamaHealthState.ERROR, model="llama3")
        h._snapshot = snap
        self.assertFalse(h.is_available())
        self.assertFalse(h.is_degraded())

    def test_ready_state(self):
        h = OllamaHealth()
        snap = OllamaHealthSnapshot(state=OllamaHealthState.READY, model="llama3")
        h._snapshot = snap
        self.assertTrue(h.is_available())
        self.assertFalse(h.is_degraded())


class TestOllamaHealthLatency(unittest.TestCase):
    def test_refresh_records_latency_metrics_offline(self):
        h = OllamaHealth(base_url="http://127.0.0.1:59999")
        snap = h.refresh(force_inference_probe=True)
        # Offline path records ping_ms and still returns valid snapshot
        self.assertGreaterEqual(snap.latency_ping_ms, 0.0)
        self.assertEqual(snap.state, OllamaHealthState.OFFLINE)

    def test_diagnostics_contains_latency_fields(self):
        h = OllamaHealth(base_url="http://127.0.0.1:59999")
        h.refresh(force_inference_probe=True)
        d = h.diagnostics()
        self.assertIn("latency_ping_ms", d)
        self.assertIn("rolling_avg_ms", d)


class TestOllamaHealthBackwardCompat(unittest.TestCase):
    def test_custom_params(self):
        h = OllamaHealth(
            base_url="http://localhost:8080",
            model="mistral",
            warning_latency_s=4.0,
            critical_latency_s=12.0,
            ping_timeout_s=0.5,
            inference_timeout_s=8.0,
            auto_reconnect=False,
            check_interval_s=60.0,
        )
        self.assertEqual(h.base_url, "http://localhost:8080")
        self.assertEqual(h.model, "mistral")
        self.assertEqual(h.warning_latency_s, 4.0)
        self.assertEqual(h.critical_latency_s, 12.0)
        self.assertEqual(h.ping_timeout_s, 0.5)
        self.assertEqual(h.inference_timeout_s, 8.0)
        self.assertFalse(h.auto_reconnect)
        self.assertEqual(h.check_interval_s, 60.0)


if __name__ == "__main__":
    unittest.main()
