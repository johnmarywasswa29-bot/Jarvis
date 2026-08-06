"""Ranking engine for knowledge retrieval."""
from __future__ import annotations

import math
from typing import Any, Optional


class RankingEngine:
    def __init__(self, *, semantic_weight: float = 0.6, keyword_weight: float = 0.3, recency_weight: float = 0.1) -> None:
        self.semantic_weight = semantic_weight
        self.keyword_weight = keyword_weight
        self.recency_weight = recency_weight

    def rank(self, query: str, results: list[dict[str, Any]], *, now: Optional[float] = None) -> list[dict[str, Any]]:
        if not results:
            return []
        q_terms = [t.lower() for t in query.split() if t.strip()]
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in results:
            text = (item.get("content") or "").lower()
            meta = item.get("metadata") or {}

            semantic = float(meta.get("score", 0.0) or 0.0)
            keyword = self._bm25_like(text, q_terms) if q_terms else 0.0
            indexed_at = meta.get("indexed_at") or meta.get("modified") or 0.0
            recency = self._recency_score(indexed_at, now)

            score = self.semantic_weight * semantic + self.keyword_weight * keyword + self.recency_weight * recency
            item["score"] = score
            scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored]

    def _bm25_like(self, text: str, terms: list[str], k1: float = 1.5, b: float = 0.75) -> float:
        if not terms or not text:
            return 0.0
        score = 0.0
        avgdl = max(1.0, len(text))
        for t in terms:
            tf = text.count(t)
            if tf == 0:
                continue
            score += tf / (tf + k1 * (1.0 - b + b * avgdl))
        return score

    def _recency_score(self, indexed_at: float, now: Optional[float]) -> float:
        if not indexed_at or not now:
            return 0.0
        age_days = max(0.0, (now - float(indexed_at)) / 86400.0)
        return 1.0 / (1.0 + math.log1p(age_days))
