"""Lightweight performance instrumentation for Jarvis."""
from __future__ import annotations

import os
import time
from typing import Any

_enabled = os.environ.get("JARVIS_BENCH") == "1"
_events: list[dict[str, Any]] = []


def enable() -> None:
    global _enabled
    _enabled = True
    _events.clear()


def disable() -> None:
    global _enabled
    _enabled = False


def clear() -> None:
    _events.clear()


def record(name: str, *, start: float, end: float, **tags: Any) -> None:
    if not _enabled:
        return
    _events.append(
        {
            "name": name,
            "start": start,
            "end": end,
            "elapsed_ms": (end - start) * 1000.0,
            **tags,
        }
    )


class Tracker:
    def __init__(self, name: str, **tags: Any) -> None:
        self.name = name
        self.tags = tags
        self.t0 = time.perf_counter()

    def stop(self) -> float:
        elapsed = time.perf_counter() - self.t0
        record(self.name, start=self.t0, end=time.perf_counter(), **self.tags)
        return elapsed


def events() -> list[dict[str, Any]]:
    return list(_events)


def summary() -> str:
    lines = []
    for ev in _events:
        lines.append(
            f"{ev['name']}: {ev['elapsed_ms']:.2f} ms"
            + (f" [{ev.get('stage')}]" if ev.get("stage") else "")
        )
    return "\n".join(lines)
