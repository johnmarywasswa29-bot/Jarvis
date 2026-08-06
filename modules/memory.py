from __future__ import annotations

import time
import asyncio
import json
from pathlib import Path
from typing import Any, Optional
import logging

from modules.config import JarvisConfig
from modules.logger import get_logger

logger = get_logger("memory")


class JarvisMemory:
    def __init__(self, config: JarvisConfig) -> None:
        self.config = config
        self._sessions: list[dict[str, Any]] = []
        self.logger = get_logger("memory")
    
    def add_message(self, role: str, content: str, metadata: Optional[dict[str, Any]] = None) -> None:
        entry = {
            "role": role,
            "content": content,
            "ts": time.time(),
            "metadata": metadata or {},
        }
        self._sessions.append(entry)
        self.logger.debug("Memory added: %s -> %s", role, content[:100])
    
    def get_recent_context(self, max_messages: int = 20, max_chars: int = 4000) -> str:
        recent = self._sessions[-max_messages:]
        lines = [f"{e['role'].upper()}: {e['content']}" for e in recent]
        joined = "\n".join(lines)
        if len(joined) > max_chars:
            joined = joined[-max_chars:]
        return joined
    
    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        """Simple keyword search over memory."""
        q = query.lower()
        scored = []
        for entry in self._sessions:
            txt = entry["content"].lower()
            score = txt.count(q)
            if score > 0:
                scored.append((score, entry))
        scored.sort(reverse=True)
        return [e for _, e in scored[:k]] if scored else []
    
    def shutdown(self) -> None:
        try:
            saved = self.config.memory_path() / "latest_session.json"
            with open(saved, "w", encoding="utf-8") as f:
                json.dump(self._sessions, f, ensure_ascii=False, indent=2)
            self.logger.info("Memory persisted to %s", saved)
        except Exception as exc:
            self.logger.error("Memory shutdown error: %s", exc)
