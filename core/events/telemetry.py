"""Telemetry recording for event bus metrics."""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class ModuleLatency:
    module: str
    avg_ms: float = 0.0
    count: int = 0
    samples: deque = field(default_factory=lambda: deque(maxlen=200))

    def record(self, value: float) -> None:
        self.samples.append(value)
        self.count += 1
        if self.samples:
            self.avg_ms = sum(self.samples) / len(self.samples)


@dataclass
class TelemetryRecord:
    event_count: int = 0
    failure_count: int = 0
    retry_count: int = 0
    module_latencies: Dict[str, ModuleLatency] = field(default_factory=dict)

    def record_event(self, source: str, duration_ms: Optional[float] = None, success: bool = True) -> None:
        self.event_count += 1
        if not success:
            self.failure_count += 1
        if source not in self.module_latencies:
            self.module_latencies[source] = ModuleLatency(module=source)
        if duration_ms is not None:
            self.module_latencies[source].record(duration_ms)

    def record_retry(self) -> None:
        self.retry_count += 1

    def failure_rate(self) -> float:
        if self.event_count == 0:
            return 0.0
        return self.failure_count / self.event_count


class TelemetryManager:
    def __init__(self) -> None:
        self.records: Dict[str, TelemetryRecord] = {}
        self._lock = threading.RLock()

    def record(self, source: str, duration_ms: Optional[float] = None, success: bool = True) -> None:
        with self._lock:
            if source not in self.records:
                self.records[source] = TelemetryRecord()
            self.records[source].record_event(source, duration_ms=duration_ms, success=success)

    def record_retry(self, source: str) -> None:
        with self._lock:
            if source not in self.records:
                self.records[source] = TelemetryRecord()
            self.records[source].record_retry()

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            out: Dict[str, Dict[str, Any]] = {}
            for source, rec in self.records.items():
                out[source] = {
                    "event_count": rec.event_count,
                    "failure_count": rec.failure_count,
                    "failure_rate": rec.failure_rate(),
                    "retry_count": rec.retry_count,
                    "avg_latency_ms": rec.module_latencies[source].avg_ms if source in rec.module_latencies else 0.0,
                }
            return out

    def reset(self) -> None:
        with self._lock:
            self.records = {}
