"""
Fast Intent Router - deterministic routing without LLM calls.

Routes:
- desktop_control: obvious control/app/file commands
- filesystem: file/folder operations
- calculator: math expressions
- web_search: search/lookup/news queries
- system_control: volume, brightness, lock, etc.
- code_execution: run/calculate/script/code
- complex: everything else -> LangGraph or full LLM
"""
from __future__ import annotations

import math
import re
import time
from typing import Any, Optional


class FastIntentRouter:
    def __init__(self, tool_registry: Any) -> None:
        self.tools = {t.name: t for t in getattr(tool_registry, "tools", [])}
        self.tool_registry = tool_registry

    def route(self, prompt: str) -> Optional[dict[str, Any]]:
        t0 = time.perf_counter()
        p = prompt.strip()
        low = p.lower()

        if self.tools.get("desktop_control") and self.tools["desktop_control"].enabled:
            if self._matches_desktop(low):
                return self._plan_desktop(low, p)
            if self._matches_open(low):
                result = {"tool": "desktop_control", "args": {"action": "open_app", "target": self._extract_open_target(low, p)}, "confidence": 0.95}
                self._record(t0)
                return result

        if self.tools.get("filesystem") and self.tools["filesystem"].enabled:
            if self._matches_filesystem(low):
                result = self._plan_filesystem(low, p)
                self._record(t0)
                return result

        if self.tools.get("web_search") and self.tools["web_search"].enabled:
            if self._matches_web(low):
                result = self._plan_web(low, p)
                self._record(t0)
                return result

        if self.tools.get("system_control") and self.tools["system_control"].enabled:
            if self._matches_system(low):
                result = self._plan_system(low, p)
                self._record(t0)
                return result

        if self.tools.get("code_execution") and self.tools["code_execution"].enabled:
            if self._matches_code(low):
                result = {"tool": "code_execution", "args": {"code": p}, "confidence": 0.85}
                self._record(t0)
                return result

        if self._looks_like_math(low):
            result = {"tool": "calculator", "args": {"expression": p}, "confidence": 0.95}
            self._record(t0)
            return result

        self._record(t0)
        return None

    def _record(self, t0: float) -> None:
        try:
            from modules.perf import record as perf_record
            perf_record("fast_intent.route", start=t0, end=time.perf_counter(), stage="router")
        except Exception:
            pass

    def _matches_desktop(self, low: str) -> bool:
        return any(x in low for x in ["screenshot", "click at", "type ", "press ", "full screen", "minimize window", "close window", "switch to"])

    def _plan_desktop(self, low: str, prompt: str) -> dict[str, Any]:
        if "screenshot" in low:
            return {"tool": "desktop_control", "args": {"action": "screenshot"}, "confidence": 0.99}
        if low.startswith("click at "):
            m = re.search(r"click\s+at\s+(\d+)\s*[,/]\s*(\d+)", low)
            if m:
                return {"tool": "desktop_control", "args": {"action": "click", "x": int(m.group(1)), "y": int(m.group(2))}, "confidence": 0.99}
        if low.startswith("type "):
            text = prompt.split(" ", 1)[1] if " " in prompt else prompt
            return {"tool": "desktop_control", "args": {"action": "type", "text": text}, "confidence": 0.95}
        if low.startswith("press "):
            key = prompt.split(" ", 1)[1] if " " in prompt else "enter"
            return {"tool": "desktop_control", "args": {"action": "press", "key": key.strip()}, "confidence": 0.95}
        return {"tool": "desktop_control", "args": {"action": "", "target": prompt}, "confidence": 0.6}

    def _matches_filesystem(self, low: str) -> bool:
        return any(x in low for x in ["list files", "list folder", "create file", "write file", "read file", "delete file", "move file", "search file", "find file"])

    def _plan_filesystem(self, low: str, prompt: str) -> dict[str, Any]:
        if low.startswith("list files in ") or low.startswith("list folder "):
            source = prompt.split(" ", 3)[-1] if " " in prompt else "."
            return {"tool": "filesystem", "args": {"action": "list", "source": source}, "confidence": 0.95}
        if low.startswith("create file ") or low.startswith("write file "):
            m = re.search(r"(?:create|write)\s+file\s+(?:at\s+|to\s+)?([^\s]+)", low)
            source = m.group(1) if m else "output.txt"
            return {"tool": "filesystem", "args": {"action": "write", "source": source, "content": ""}, "confidence": 0.9}
        if low.startswith("read file "):
            source = prompt.split(" ", 3)[-1] if " " in prompt else "."
            return {"tool": "filesystem", "args": {"action": "read", "source": source}, "confidence": 0.95}
        return {"tool": "filesystem", "args": {"action": "list", "source": "."}, "confidence": 0.7}

    def _matches_web(self, low: str) -> bool:
        return any(low.startswith(x) or f" {x} " in f" {low} " for x in ["search", "search for", "look up", "find", "lookup", "who is", "what is", "when is", "where is", "how to", "why is", "news about"])

    def _plan_web(self, low: str, prompt: str) -> dict[str, Any]:
        query = prompt
        for prefix in ["search ", "search for ", "look up ", "lookup ", "find "]:
            if low.startswith(prefix):
                query = prompt[len(prefix):].strip()
                break
        return {"tool": "web_search", "args": {"query": query or prompt}, "confidence": 0.95}

    def _matches_system(self, low: str) -> bool:
        return any(x in low for x in ["volume up", "volume down", "mute", "unmute", "lock screen", "shutdown", "restart", "sleep", "wifi on", "wifi off", "bluetooth on", "bluetooth off"])

    def _plan_system(self, low: str, prompt: str) -> dict[str, Any]:
        if "volume up" in low:
            return {"tool": "system_control", "args": {"action": "volume_up"}, "confidence": 0.99}
        if "volume down" in low:
            return {"tool": "system_control", "args": {"action": "volume_down"}, "confidence": 0.99}
        if "mute" in low:
            return {"tool": "system_control", "args": {"action": "mute"}, "confidence": 0.99}
        return {"tool": "system_control", "args": {"action": "unknown", "target": prompt}, "confidence": 0.7}

    def _matches_code(self, low: str) -> bool:
        return any(x in low for x in ["run code", "execute python", "python script", "run script", "run python"])

    def _matches_open(self, low: str) -> bool:
        return low.startswith("open ") or low.startswith("launch ")

    def _looks_like_math(self, low: str) -> bool:
        stripped = low.replace("what is", "").replace("calculate", "").replace("compute", "").strip()
        if not stripped:
            return False
        return bool(re.fullmatch(r"[0-9\s\+\-\*/\.\(\)\%\^]+", stripped))

    def _extract_open_target(self, low: str, original: str) -> str:
        for prefix in ["open ", "launch "]:
            if low.startswith(prefix):
                return original[len(prefix):].strip()
        return original
