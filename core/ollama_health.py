"""Ollama health monitoring, model discovery, latency tracking, degraded mode."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class OllamaHealthState(str, Enum):
    READY = "ready"
    LOADING = "loading"
    BUSY = "busy"
    SLOW = "slow"
    VERY_SLOW = "very_slow"
    OFFLINE = "offline"
    UNREACHABLE = "unreachable"
    MODEL_MISSING = "model_missing"
    ERROR = "error"
    DEGRADED = "degraded"


@dataclass
class OllamaHealthSnapshot:
    state: OllamaHealthState = OllamaHealthState.UNREACHABLE
    model: str = ""
    installed_models: list[str] = field(default_factory=list)
    latency_ping_ms: float = 0.0
    latency_first_token_ms: float = 0.0
    latency_total_ms: float = 0.0
    tokens_per_sec: float = 0.0
    rolling_avg_ms: float = 0.0
    error: Optional[str] = None
    updated_at: float = field(default_factory=time.time)
    recovery_attempts: int = 0


class OllamaHealth:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3",
        *,
        warning_latency_s: float = 8.0,
        critical_latency_s: float = 20.0,
        ping_timeout_s: float = 1.5,
        inference_timeout_s: float = 12.0,
        auto_reconnect: bool = True,
        check_interval_s: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.warning_latency_s = warning_latency_s
        self.critical_latency_s = critical_latency_s
        self.ping_timeout_s = float(ping_timeout_s)
        self.inference_timeout_s = inference_timeout_s
        self.auto_reconnect = auto_reconnect
        self.check_interval_s = check_interval_s

        self._snapshot = OllamaHealthSnapshot()
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._recent: list[float] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False

    def snapshot(self) -> OllamaHealthSnapshot:
        with self._lock:
            return self._snapshot

    def current_state(self) -> OllamaHealthState:
        return self.snapshot().state

    def is_available(self) -> bool:
        return self.current_state() not in (
            OllamaHealthState.OFFLINE,
            OllamaHealthState.UNREACHABLE,
            OllamaHealthState.MODEL_MISSING,
            OllamaHealthState.ERROR,
        )

    def is_degraded(self) -> bool:
        return self.current_state() == OllamaHealthState.DEGRADED

    def refresh(self, *, force_inference_probe: bool = False) -> OllamaHealthSnapshot:
        """Run a full or lightweight health check synchronously."""
        snapshot = self._run_checks(force_inference_probe=force_inference_probe)
        with self._lock:
            self._snapshot = snapshot
        return snapshot

    def wait_until_ready(self, timeout: float = 60.0, poll: float = 1.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            s = self.refresh()
            if self.is_available():
                return True
            time.sleep(poll)
        return False

    def block_if_degraded(self) -> None:
        if self.is_degraded():
            raise RuntimeError("Ollama is in degraded mode")

    # ------------------------------------------------------------------
    # Model discovery
    # ------------------------------------------------------------------
    def list_models(self) -> list[str]:
        data = self._ollama_get("/api/tags")
        if not data:
            return []
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]

    def current_model(self) -> str:
        return self.model

    def model_exists(self, name: str) -> bool:
        return name in self.list_models()

    def model_size(self, name: str) -> Optional[str]:
        data = self._ollama_get("/api/tags")
        if not data:
            return None
        for m in data.get("models", []):
            if m.get("name") == name:
                return m.get("size")
        return None

    def recommended_models(self) -> list[str]:
        models = self.list_models()
        preferred = ["llama3", "llama3.1", "llama3.2", "llama2", "mistral", "gemma"]
        hits = [m for m in models if any(m.startswith(p) for p in preferred)]
        return hits or models

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    def diagnostics(self) -> dict[str, Any]:
        s = self.snapshot()
        return {
            "state": s.state.value,
            "configured_model": s.model,
            "installed_models": s.installed_models,
            "installed_model_count": len(s.installed_models),
            "configured_model_exists": s.model in s.installed_models,
            "current_loaded_model": s.installed_models[0] if s.installed_models else "",
            "latency_ping_ms": round(s.latency_ping_ms, 2),
            "latency_first_token_ms": round(s.latency_first_token_ms, 2),
            "latency_total_ms": round(s.latency_total_ms, 2),
            "tokens_per_sec": round(s.tokens_per_sec, 2),
            "rolling_avg_ms": round(s.rolling_avg_ms, 2),
            "error": s.error,
            "updated_at": s.updated_at,
            "recovery_attempts": s.recovery_attempts,
            "is_available": self.is_available(),
            "is_degraded": self.is_degraded(),
        }

    # ------------------------------------------------------------------
    # Internal monitor
    # ------------------------------------------------------------------
    def _monitor_loop(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    return
            try:
                self.refresh()
            except Exception as exc:
                logger.debug("Ollama health monitor error: %s", exc)
            time.sleep(self.check_interval_s)

    def _run_checks(self, *, force_inference_probe: bool = False) -> OllamaHealthSnapshot:
        snap = OllamaHealthSnapshot(model=self.model)
        error_parts: list[str] = []

        # 1. Server reachable?
        if not self._server_reachable(snap):
            snap.state = OllamaHealthState.OFFLINE
            snap.error = "Cannot reach Ollama server"
            return snap

        # 2. API responding?
        tags = self._ollama_get("/api/tags")
        if tags is None:
            snap.state = OllamaHealthState.UNREACHABLE
            snap.error = "Ollama /api/tags not responding"
            snap.installed_models = []
            return snap
        snap.installed_models = [m.get("name", "") for m in tags.get("models", []) if m.get("name")]

        # 3. Model exists?
        if self.model not in snap.installed_models:
            snap.state = OllamaHealthState.MODEL_MISSING
            snap.error = f"Model '{self.model}' not installed"
            return snap

        # 4. Ping latency
        snap.latency_ping_ms = self._ping_latency()

        # 5. Optional inference probe
        if force_inference_probe:
            probe = self._inference_probe()
            snap.latency_first_token_ms = probe.get("first_token_ms", 0.0)
            snap.latency_total_ms = probe.get("total_ms", 0.0)
            snap.tokens_per_sec = probe.get("tokens_per_sec", 0.0)
            if probe.get("error"):
                error_parts.append(probe["error"])

        # 6. Classify latency
        latency = snap.latency_total_ms or snap.latency_ping_ms
        with self._lock:
            self._recent.append(latency)
            if len(self._recent) > 20:
                self._recent = self._recent[-20:]
            snap.rolling_avg_ms = sum(self._recent) / len(self._recent)

        if snap.latency_ping_ms == 0.0 and not force_inference_probe:
            snap.state = OllamaHealthState.READY
            return snap

        total_s = snap.latency_total_ms / 1000.0
        if total_s >= self.critical_latency_s:
            snap.state = OllamaHealthState.VERY_SLOW
        elif total_s >= self.warning_latency_s:
            snap.state = OllamaHealthState.SLOW
        else:
            snap.state = OllamaHealthState.READY

        # 7. Busy detection: if model is currently processing a request.
        snap.state = self._maybe_busy(snap.state, snap.installed_models)

        if error_parts:
            snap.error = "; ".join(error_parts)

        return snap

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _server_reachable(self, snap: OllamaHealthSnapshot) -> bool:
        try:
            from urllib.request import Request, urlopen
            req = Request(f"{self.base_url}/")
            with urlopen(req, timeout=self.ping_timeout_s) as resp:
                snap.latency_ping_ms = self._measure(lambda: urlopen(req, timeout=self.ping_timeout_s).read())
                return True
        except Exception:
            snap.latency_ping_ms = 0.0
            return False

    def _ollama_get(self, path: str) -> Optional[dict[str, Any]]:
        try:
            from urllib.request import Request, urlopen
            req = Request(f"{self.base_url}{path}")
            with urlopen(req, timeout=self.ping_timeout_s) as resp:
                import json
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    def _ollama_post(self, path: str, payload: dict[str, Any], timeout: float) -> Optional[dict[str, Any]]:
        try:
            from urllib.request import Request, urlopen
            data = __import__("json").dumps(payload).encode("utf-8")
            req = Request(f"{self.base_url}{path}", data=data, headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=timeout) as resp:
                import json
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.debug("Ollama POST %s failed: %s", path, exc)
            return None

    # ------------------------------------------------------------------
    # Latency helpers
    # ------------------------------------------------------------------
    def _measure(self, fn):
        t0 = time.perf_counter()
        try:
            fn()
        except Exception:
            pass
        return (time.perf_counter() - t0) * 1000.0

    def _ping_latency(self) -> float:
        def _do():
            from urllib.request import Request, urlopen
            req = Request(f"{self.base_url}/api/tags")
            urlopen(req, timeout=self.ping_timeout_s).read()

        return self._measure(_do)

    def _inference_probe(self) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "prompt": "ping",
            "stream": False,
            "options": {"num_predict": 1, "temperature": 0.0},
        }
        result: dict[str, Any] = {"first_token_ms": 0.0, "total_ms": 0.0, "tokens_per_sec": 0.0}
        t0 = time.perf_counter()
        try:
            resp = self._ollama_post("/api/generate", payload, timeout=self.inference_timeout_s)
        except Exception as exc:
            result["error"] = str(exc)
            return result
        if resp is None:
            result["error"] = "inference probe returned None"
            return result
        total_ms = (time.perf_counter() - t0) * 1000.0
        result["total_ms"] = total_ms
        result["first_token_ms"] = total_ms  # non-streaming probe has no per-token timing
        tokens = resp.get("eval_count") or resp.get("prompt_eval_count") or 1
        if total_ms > 0:
            result["tokens_per_sec"] = (tokens / (total_ms / 1000.0))
        return result

    def _maybe_busy(self, state: OllamaHealthState, installed: list[str]) -> OllamaHealthState:
        # If we have visibility into running processes, downgrade to BUSY.
        # We keep this lightweight to avoid parsing /api/ps on every tick.
        # For now, rely on latency bands for the state machine.
        return state

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------
    def attempt_recovery(self) -> OllamaHealthSnapshot:
        with self._lock:
            snap = self._snapshot
            snap.recovery_attempts += 1
        # A recovery attempt is just a fresh probe after a short delay.
        time.sleep(0.5)
        refreshed = self.refresh(force_inference_probe=True)
        if refreshed.state == OllamaHealthState.MODEL_MISSING:
            refreshed.state = OllamaHealthState.DEGRADED
            refreshed.error = f"Model missing; installed: {', '.join(refreshed.installed_models[:5]) or 'none'}"
        # Preserve the recovery counter in the returned snapshot.
        refreshed.recovery_attempts = snap.recovery_attempts
        return refreshed
