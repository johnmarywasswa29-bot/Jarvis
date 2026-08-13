"""Research orchestrator around existing knowledge/search tools."""
from __future__ import annotations

from typing import Any, Optional

from knowledge.rag import RAGService


class ResearchFindings:
    def __init__(self, query: str, results: list[dict[str, Any]], structured: list[dict[str, Any]]) -> None:
        self.query = query
        self.results = results
        self.structured = structured

    def as_context(self) -> str:
        parts = [f"Query: {self.query}"]
        for idx, item in enumerate(self.structured[:5], 1):
            parts.append(f"{idx}. {item.get('title') or item.get('source')}: {(item.get('text') or item.get('content') or '')[:300]}")
        return "\n".join(parts)


class ResearchOrchestrator:
    def __init__(self, rag: Optional[RAGService] = None, web_search: Any = None) -> None:
        self.rag = rag
        self.web_search = web_search

    def research(self, query: str, *, persist: bool = False) -> ResearchFindings:
        if not query.strip():
            return ResearchFindings(query=query, results=[], structured=[])
        results = self._search(query)
        structured = self._normalize(query, results)
        if persist and self.rag:
            try:
                self.rag.remember_query(query, success=bool(structured))
            except Exception:
                pass
        return ResearchFindings(query=query, results=results, structured=structured)

    def _search(self, query: str) -> list[dict[str, Any]]:
        if self.web_search:
            try:
                res = self.web_search.search(query=query, limit=5)
                if isinstance(res, list):
                    return res
            except Exception:
                pass
        if self.rag:
            try:
                docs = self.rag.search(query, k=5)
                if isinstance(docs, list):
                    return [{"source": d.get("source") or d.get("metadata", {}).get("source"), "text": d.get("text") or d.get("content"), "score": d.get("score")} for d in docs if isinstance(d, dict)]
            except Exception:
                pass
        return []

    def _normalize(self, query: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for item in results:
            if not isinstance(item, dict):
                continue
            text = (item.get("text") or item.get("content") or "").strip()
            source = item.get("source") or item.get("url") or item.get("identifier") or ""
            if text:
                out.append({"source": source, "text": text, "score": item.get("score")})
        return out
