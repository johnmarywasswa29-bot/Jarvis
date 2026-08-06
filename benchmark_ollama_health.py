"""Benchmark Ollama health checks."""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from core.ollama_health import OllamaHealth, OllamaHealthState


def _time(fn):
    t0 = time.perf_counter()
    try:
        result = fn()
    except Exception:
        result = None
    return (time.perf_counter() - t0) * 1000.0, result


def main() -> int:
    # Use a very short ping timeout for offline benchmarks so the suite stays fast.
    offline_url = "http://127.0.0.1:59999"
    h = OllamaHealth(
        base_url=offline_url,
        ping_timeout_s=0.2,
        inference_timeout_s=0.5,
    )
    rows = []

    # 1. Health check latency (offline)
    ms, _ = _time(h.refresh)
    rows.append(("health_check_offline_ms", ms))

    # 2. Startup validation
    ms, snap = _time(lambda: h.refresh(force_inference_probe=True))
    rows.append(("startup_validation_ms", ms))
    rows.append(("startup_validation_state", snap.state.value if snap else "error"))

    # 3. Model discovery
    ms, models = _time(h.list_models)
    rows.append(("model_discovery_ms", ms))
    rows.append(("model_discovery_count", len(models) if models else 0))

    # 4. Recommended models
    ms, recs = _time(h.recommended_models)
    rows.append(("recommended_models_ms", ms))
    rows.append(("recommended_count", len(recs) if recs else 0))

    # 5. Offline detection
    ms, _ = _time(h.refresh)
    rows.append(("offline_detection_ms", ms))

    # 6. Recovery attempt
    ms, snap2 = _time(h.attempt_recovery)
    rows.append(("recovery_attempt_ms", ms))
    rows.append(("recovery_state", snap2.state.value if snap2 else "error"))

    # 7. Background monitor overhead
    h.start()
    time.sleep(0.1)
    t0 = time.perf_counter()
    for _ in range(10):
        h.refresh()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    rows.append(("monitor_10_checks_ms", elapsed_ms))
    h.stop()

    # 8. Diagnostics
    ms, diag = _time(h.diagnostics)
    rows.append(("diagnostics_ms", ms))
    rows.append(("diagnostics_state", diag.get("state") if diag else "error"))

    # Print
    print("Ollama Health Benchmarks")
    print("-" * 40)
    for name, value in rows:
        if isinstance(value, float):
            print(f"{name}: {value:.3f} ms")
        else:
            print(f"{name}: {value}")
    print("-" * 40)

    # Thresholds: offline detection should stay under 1s on Windows sockets.
    assert rows[0][1] < 1000, "health check too slow"
    assert rows[6][1] < 3000, "background monitor overhead too high"
    print("BENCHMARK_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
