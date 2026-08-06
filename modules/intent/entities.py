"""Entity extraction for intent confidence engine."""
from __future__ import annotations

import re
from typing import Any, Optional


class EntityExtractor:
    known_apps = [
        "chrome", "firefox", "edge", "notepad", "vscode", "vs code", "code", "spotify",
        "discord", "teams", "zoom", "slack", "explorer", "calculator", "terminal",
        "cmd", "powershell", "word", "excel", "powerpoint", "outlook",
    ]

    math_keywords = ("calculate", "compute", "what is", "math", "solve")

    def extract(self, intent: str, prompt: str) -> dict[str, Any]:
        low = prompt.strip().lower()
        if intent == "desktop.open_application":
            return self._extract_open(low)
        if intent == "filesystem.delete":
            return self._extract_delete(low)
        if intent == "filesystem.list":
            return self._extract_list(low)
        if intent == "filesystem.read":
            return self._extract_read(low)
        if intent == "filesystem.write":
            return self._extract_write(low)
        if intent == "web_search.search":
            return self._extract_search(low)
        if intent == "system_control.volume_up":
            return {"action": "volume_up"}
        if intent == "system_control.volume_down":
            return {"action": "volume_down"}
        if intent == "system_control.mute":
            return {"action": "mute"}
        if intent == "system_control.shutdown":
            return {"action": "shutdown"}
        if intent == "system_control.restart":
            return {"action": "restart"}
        if intent == "calculator.evaluate":
            return self._extract_math(low)
        if intent == "code_execution.run":
            return {"code": prompt}
        if intent == "planning.organize_day":
            return {"query": prompt}
        if intent == "desktop.screenshot":
            return {"action": "screenshot"}
        if intent == "desktop.click":
            return self._extract_click(low)
        if intent == "desktop.type":
            text = prompt.split(" ", 1)[1] if " " in prompt else ""
            return {"action": "type", "text": text}
        if intent == "desktop.press":
            key = prompt.split(" ", 1)[1].strip() if " " in prompt else "enter"
            return {"action": "press", "key": key}
        return {"query": prompt}

    def _extract_click(self, low: str) -> dict[str, Any]:
        m = re.search(r"click\s+at\s+(\d+)\s*[,/ ]\s*(\d+)", low, re.IGNORECASE)
        if m:
            return {"action": "click", "x": int(m.group(1)), "y": int(m.group(2))}
        return {"action": "click"}

    def _extract_open(self, low: str) -> dict[str, Any]:
        for prefix in ("open ", "launch "):
            if low.startswith(prefix):
                app = low[len(prefix):].strip().lower()
                app_known = any(app == a or app.startswith(a) for a in self.known_apps)
                return {"application": app, "application_known": app_known}
        return {"application": "", "application_known": False}

    def _extract_delete(self, low: str) -> dict[str, Any]:
        m = re.search(r"delete\s+(?:file\s+)?(.+?)(?:\s+from\s+(.+))?$", low, re.IGNORECASE)
        if m:
            return {"source": m.group(1).strip().lower(), "target": (m.group(2) or "").strip().lower()}
        return {"source": "", "target": ""}

    def _extract_list(self, low: str) -> dict[str, Any]:
        m = re.search(r"(?:list|show)\s+(?:files\s+)?(?:in|of|from)\s+(.+)", low, re.IGNORECASE)
        if m:
            return {"source": m.group(1).strip().lower()}
        return {"source": "."}

    def _extract_read(self, low: str) -> dict[str, Any]:
        m = re.search(r"read\s+(?:file\s+)?(.+)", low, re.IGNORECASE)
        if m:
            return {"source": m.group(1).strip().lower()}
        return {"source": ""}

    def _extract_write(self, low: str) -> dict[str, Any]:
        m = re.search(r"(?:write|create)\s+file\s+(?:at\s+|to\s+)?([^\s]+)", low, re.IGNORECASE)
        source = m.group(1) if m else "output.txt"
        return {"source": source.lower(), "content": ""}

    def _extract_search(self, low: str) -> dict[str, Any]:
        for prefix in ("search ", "search for ", "look up ", "lookup ", "find "):
            if low.lower().startswith(prefix):
                return {"query": low[len(prefix):].strip()}
        return {"query": low}

    def _extract_math(self, low: str) -> dict[str, Any]:
        expr = low
        for prefix in ("what is", "calculate", "compute", "solve"):
            if expr.lower().startswith(prefix):
                expr = expr[len(prefix):].strip()
                break
        return {"expression": expr}
