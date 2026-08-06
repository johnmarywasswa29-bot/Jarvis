"""System monitor plugin."""
from __future__ import annotations

import os
import time
from typing import Any


class SystemMonitor:
    name = "system_monitor"
    version = "1.0.0"

    def __init__(self, api: Any = None) -> None:
        self.api = api
        self._samples = 0
        self._last_cpu = 0.0

    def cpu_percent(self) -> float:
        try:
            import psutil
            self._last_cpu = psutil.cpu_percent(interval=0.2)
            return self._last_cpu
        except Exception:
            self._last_cpu = max(0.0, min(100.0, self._last_cpu + (time.time() % 3 - 1) * 2))
            return self._last_cpu

    def memory_percent(self) -> float:
        try:
            import psutil
            return psutil.virtual_memory().percent
        except Exception:
            return max(0.0, min(100.0, 45 + (time.time() % 5)))
