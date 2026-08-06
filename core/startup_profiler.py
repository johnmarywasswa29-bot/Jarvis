"""Startup profiler: measures per-subsystem init time, memory, and CPU."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("runtime.profiler")


@dataclass
class StartupStep:
    name: str
    start_s: float = 0.0
    end_s: float = 0.0
    duration_ms: float = 0.0
    memory_kb: int = 0
    cpu_percent: float = 0.0
    error: Optional[str] = None

    @property
    def is_heavy(self) -> bool:
        return self.duration_ms > 500 or self.memory_kb > 50_000


class StartupProfiler:
    def __init__(self) -> None:
        self.steps: list[StartupStep] = []
        self._lock = threading.Lock()
        self._current: Optional[StartupStep] = None

    def begin(self, name: str) -> None:
        with self._lock:
            step = StartupStep(name=name, start_s=time.perf_counter())
            self.steps.append(step)
            self._current = step
        logger.debug("[profiler] START %s", name)

    def end(self, error: Optional[str] = None) -> None:
        with self._lock:
            step = self._current
            if step is None:
                return
            step.end_s = time.perf_counter()
            step.duration_ms = (step.end_s - step.start_s) * 1000
            try:
                import resource
                r = resource.getrusage(resource.RUSAGE_SELF)
                step.memory_kb = int(r.ru_maxrss)
            except Exception:
                pass
            step.error = error
            self._current = None
        logger.debug("[profiler] END %s (%.1f ms)", step.name, step.duration_ms)

    def heavy(self) -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "duration_ms": round(s.duration_ms, 1),
                "memory_kb": s.memory_kb,
                "error": s.error,
            }
            for s in self.steps
            if s.is_heavy
        ]

    def timeline(self) -> str:
        lines = ["Step                              ms       kb  note"]
        lines.append("-" * 60)
        total = 0.0
        for s in self.steps:
            note = s.error or ("HEAVY" if s.is_heavy else "")
            lines.append(f"{s.name:<32} {s.duration_ms:>6.1f}  {s.memory_kb:>6}  {note}")
            total += s.duration_ms
        lines.append("-" * 60)
        lines.append(f"{'TOTAL':<32} {total:>6.1f}")
        return "\n".join(lines)
