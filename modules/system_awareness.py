"""System awareness: CPU, RAM, battery, network/internet, disk, Ollama connectivity."""
from __future__ import annotations

import shutil
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ResourceSnapshot:
    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    battery_percent: Optional[float] = None
    battery_plugged: Optional[bool] = None
    internet_available: bool = False
    disk_percent: Optional[float] = None
    gpu_available: bool = False
    ollama_available: bool = False
    updated_at: float = 0.0


class SystemAwareness:
    def __init__(self, ollama_base_url: str = "http://localhost:11434") -> None:
        self.ollama_base_url = ollama_base_url.rstrip("/")
        self._last: ResourceSnapshot = ResourceSnapshot(updated_at=time.time() - 10.0)

    def snapshot(self) -> ResourceSnapshot:
        s = ResourceSnapshot()
        try:
            import psutil
            s.cpu_percent = float(psutil.cpu_percent(interval=0.0))
            s.ram_percent = float(psutil.virtual_memory().percent)
            try:
                usage = shutil.disk_usage(str(Path.home()))
                s.disk_percent = round((1.0 - usage.free / usage.total) * 100, 1)
            except Exception:
                pass
            batt = psutil.sensors_battery()
            if batt is not None:
                s.battery_percent = float(batt.percent)
                s.battery_plugged = bool(batt.power_plugged)
        except Exception:
            pass
        s.internet_available = self._check_internet()
        s.ollama_available = self._check_ollama()
        s.gpu_available = self._check_gpu()
        s.updated_at = time.time()
        self._last = s
        return s

    def last_known(self) -> ResourceSnapshot:
        return self._last

    def _check_internet(self, host: str = "8.8.8.8", port: int = 53, timeout: float = 0.6) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except Exception:
            return False

    def _check_ollama(self, timeout: float = 0.6) -> bool:
        try:
            from urllib.request import Request, urlopen
            req = Request(f"{self.ollama_base_url}/api/tags")
            with urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _check_gpu(self) -> bool:
        try:
            import GPUtil
            return len(GPUtil.getGPUs()) > 0
        except Exception:
            return False
