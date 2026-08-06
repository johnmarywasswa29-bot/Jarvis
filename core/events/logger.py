"""Persisted structured event logging."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from core.events import Event


class EventLogger:
    def __init__(self, log_dir: str = "logs") -> None:
        self.log_dir = log_dir
        self._lock = threading.RLock()
        os.makedirs(log_dir, exist_ok=True)
        self._file_path = os.path.join(log_dir, "events.jsonl")

    def log(self, event: Event) -> None:
        record = event.to_dict()
        with self._lock:
            with open(self._file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def query(
        self,
        types: Optional[Iterable[str]] = None,
        sources: Optional[Iterable[str]] = None,
        search: str = "",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        types_set = set(types or [])
        sources_set = set(sources or [])
        search_lower = search.lower()
        results: List[Dict[str, Any]] = []
        try:
            with open(self._file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if types_set and obj.get("event_type") not in types_set:
                        continue
                    if sources_set and obj.get("source") not in sources_set:
                        continue
                    if search_lower and search_lower not in json.dumps(obj, ensure_ascii=False).lower():
                        continue
                    results.append(obj)
                    if len(results) >= limit:
                        break
        except FileNotFoundError:
            pass
        return results

    def tail(self, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            with open(self._file_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                if size == 0:
                    return []
                block = 4096
                data = b""
                while size > 0 and len(data.splitlines()) <= limit:
                    read_size = min(block, size)
                    size -= read_size
                    f.seek(size)
                    data = f.read(read_size) + data
                lines = data.splitlines()[-limit:]
                out: List[Dict[str, Any]] = []
                for raw in lines:
                    try:
                        out.append(json.loads(raw.decode("utf-8", errors="ignore")))
                    except json.JSONDecodeError:
                        continue
                return out
        except FileNotFoundError:
            return []
