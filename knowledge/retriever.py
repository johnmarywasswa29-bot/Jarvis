"""Knowledge retriever: search across indexed documents with optional re-ranking."""
from __future__ import annotations

import time
from typing import Any, Optional


class KnowledgeRetriever:
    def __init__(self, storage: Any, embedder: Any, ranking_engine: Any = None) -> None:
        self.storage = storage
        self.embedder = embedder
        self.ranking_engine = ranking_engine or None

    def search(self, query: str, *, k: int = 5, filters: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        results = self.storage.query(query, k=k, where=filters or {})
        if self.ranking_engine:
            results = self.ranking_engine.rank(query, results, now=time.time())
        return results[:k]

    def get_context(self, query: str, *, max_chars: int = 2500, k: int = 5) -> str:
        results = self.search(query, k=k)
        if not results:
            return ""
        parts: list[str] = []
        total = 0
        for item in results:
            text = (item.get("content") or "").strip()
            if not text:
                continue
            prefix = ""
            if item.get("metadata", {}).get("filename"):
                prefix = f"[{item['metadata']['filename']}] "
            if total + len(prefix) + len(text) > max_chars:
                break
            parts.append(prefix + text)
            total += len(prefix) + len(text)
        return "\n\n".join(parts)
