"""Confidence scoring for intent confidence engine."""
from __future__ import annotations

import json
import time
from typing import Any, Optional


def _json_dumps(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return "{}"


def _json_loads(text: Any) -> dict[str, Any]:
    if isinstance(text, dict):
        return text
    try:
        return json.loads(text)
    except Exception:
        return {}


class ConfidenceScorer:
    def __init__(self, memory: Any = None) -> None:
        self.memory = memory
        self._local: dict[str, dict[str, Any]] = {}

    def score(
        self,
        *,
        keyword_score: float = 0.0,
        regex_score: float = 0.0,
        entity_score: float = 0.0,
        app_lookup_score: float = 0.0,
        memory_score: float = 0.0,
        ambiguity_penalty: float = 0.0,
        historical_success: Optional[float] = None,
    ) -> float:
        base = 0.0
        base += max(0.0, min(1.0, keyword_score)) * 0.40
        base += max(0.0, min(1.0, regex_score)) * 0.40
        base += max(0.0, min(1.0, entity_score)) * 0.15
        base += max(0.0, min(1.0, app_lookup_score)) * 0.10
        base += max(0.0, min(1.0, memory_score)) * 0.05

        if historical_success is not None:
            base *= max(0.5, min(1.5, 0.5 + historical_success * 1.0))

        base -= max(0.0, min(1.0, ambiguity_penalty)) * 0.15

        return max(0.0, min(1.0, base))

    def record(self, intent: str, success: bool) -> None:
        entry = self._local.setdefault(intent, {"success": 0, "total": 0})
        entry["success"] += 1 if success else 0
        entry["total"] += 1
        if self.memory is None:
            return
        try:
            stats = self._stats_from_memory()
            key = f"intent:{intent}"
            mem_entry = stats.get(key, {"success": 0, "total": 0, "avg_confidence": 0.5})
            mem_entry["success"] = int(mem_entry.get("success", 0)) + (1 if success else 0)
            mem_entry["total"] = int(mem_entry.get("total", 0)) + 1
            mem_entry["avg_confidence"] = mem_entry["success"] / mem_entry["total"] if mem_entry["total"] else 0.5
            stats[key] = mem_entry
            self.memory.add_memory(
                _json_dumps(stats),
                memory_type="semantic",
                importance=0.3,
                confidence=0.6,
                source="system",
                tags=["intent_stats"],
                deduplicate=True,
            )
        except Exception:
            pass

    def historical(self, intent: str) -> float:
        local = self._local.get(intent)
        if local and local["total"] > 0:
            return local["success"] / local["total"]
        if self.memory is None:
            return 0.5
        try:
            stats = self._stats_from_memory()
            entry = stats.get(f"intent:{intent}")
            if not entry:
                return 0.5
            return float(entry.get("avg_confidence", 0.5))
        except Exception:
            return 0.5

    def _stats_from_memory(self) -> dict[str, Any]:
        if self.memory is None:
            return {}
        try:
            hits = self.memory.search("intent_stats", types=["semantic"], limit=10)
            for hit in hits:
                try:
                    return _json_loads(hit["content"])
                except Exception:
                    continue
        except Exception:
            pass
        return {}

    def benchmark(self, n: int = 200) -> float:
        t0 = time.perf_counter()
        for _ in range(n):
            self.score(keyword_score=0.8, regex_score=0.9, entity_score=0.7, app_lookup_score=0.9, memory_score=0.5, ambiguity_penalty=0.1)
        return (time.perf_counter() - t0) / n
