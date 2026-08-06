"""Knowledge Engine V1: local document indexing + semantic retrieval + integrations."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from knowledge.knowledge_storage import KnowledgeStorage
from knowledge.indexer import KnowledgeIndexer, SUPPORTED_EXTENSIONS
from knowledge.chunker import Chunker
from knowledge.embedder import Embedder
from knowledge.retriever import KnowledgeRetriever


def _json_dumps(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)


class KnowledgeEngine:
    def __init__(
        self,
        root_dir: str | Path = "knowledge_data",
        *,
        embedder: Optional[Embedder] = None,
        chunker: Optional[Chunker] = None,
        use_chroma: bool = True,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.storage = KnowledgeStorage(self.root_dir / "store", use_chroma=use_chroma)
        self.indexer = KnowledgeIndexer(self.storage, chunker or Chunker())
        self.embedder = embedder or Embedder()
        self.retriever = KnowledgeRetriever(self.storage, self.embedder)
        self._watch_folders: dict[str, float] = {}
        self._lock = threading.RLock()

    def close(self) -> None:
        try:
            self.storage.close()
        except Exception:
            pass

    # Core API

    def enabled(self) -> bool:
        return self.storage.enabled() or self.embedder.is_available()

    def index_file(self, path: str | Path) -> Optional[str]:
        with self._lock:
            doc_id = self.indexer.index_file(path)
            return doc_id

    def index_folder(self, folder: str | Path) -> list[str]:
        with self._lock:
            return self.indexer.index_folder(folder)

    def forget(self, doc_id: str) -> None:
        with self._lock:
            self.storage.delete_document(doc_id)

    def search(self, query: str, *, k: int = 5, filters: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        return self.retriever.search(query, k=k, filters=filters)

    def context(self, query: str, *, max_chars: int = 2500, k: int = 5) -> str:
        return self.retriever.get_context(query, max_chars=max_chars, k=k)

    # Auto reindex management

    def register_watch_folder(self, folder: str | Path) -> None:
        folder = str(Path(folder).resolve())
        latest = self._latest_mtime(folder)
        with self._lock:
            self._watch_folders[folder] = latest

    def refresh_if_changed(self) -> list[str]:
        updated: list[str] = []
        with self._lock:
            folders = list(self._watch_folders.keys())
        for folder in folders:
            latest = self._latest_mtime(folder)
            with self._lock:
                previous = self._watch_folders.get(folder)
            if previous is not None and latest > previous:
                updated.extend([p for p in Path(folder).rglob("*") if p.suffix.lower() in SUPPORTED_EXTENSIONS])
                with self._lock:
                    self._watch_folders[folder] = latest
        doc_ids: list[str] = []
        for path in updated:
            doc_id = self.index_file(path)
            if doc_id:
                doc_ids.append(doc_id)
        return doc_ids

    # Integrations

    def enhance_memory(self, memory_obj: Any, query: str, *, k: int = 5) -> dict[str, Any]:
        try:
            if hasattr(memory_obj, "add_message"):
                context = self.context(query, k=k)
                if context:
                    memory_obj.add_message(
                        "knowledge",
                        _json_dumps({"query": query, "context": context}),
                        metadata={"source": "knowledge_engine"},
                    )
        except Exception:
            pass
        return {}

    def enhance_planner(self, planner_state: dict[str, Any], query: str, *, k: int = 3) -> dict[str, Any]:
        try:
            snippets = self.search(query, k=k)
            knowledge_context = "\n".join(item.get("content", "") for item in snippets)
            planner_state.setdefault("context", [])
            if knowledge_context:
                planner_state["context"].append(f"Knowledge matches:\n{knowledge_context}")
                planner_state.setdefault("knowledge_context", knowledge_context)
            planner_state.setdefault("selected_tools", [])
            planner_state["selected_tools"].append("knowledge_search")
        except Exception:
            pass
        return planner_state

    def enhance_reflection(self, state: dict[str, Any]) -> dict[str, Any]:
        try:
            if state.get("reflection") == "fallback":
                query = state.get("transcript") or state.get("answer") or ""
                context = self.context(query, k=3)
                if context:
                    state["answer"] = context.split("\n\n")[0]
                    state["reflection"] = "refine"
        except Exception:
            pass
        return state

    def enhance_goal_manager(self, goal_manager: Any, query: str) -> None:
        try:
            if goal_manager is None:
                return
            if hasattr(goal_manager, "create"):
                goal_manager.create(
                    title=f"Knowledge query: {query[:140]}",
                    description="Auto-created from knowledge engine query.",
                    category="knowledge",
                    tags=["knowledge"],
                )
        except Exception:
            pass

    def enhance_task_queue(self, task_queue: Any, query: str) -> None:
        try:
            if task_queue is None:
                return
            results = self.search(query, k=3)
            for idx, item in enumerate(results, start=1):
                task = type("Task", (), {"id": f"knowledge-{idx}", "status": "ready", "title": item.get("content", "")[:120]})
                try:
                    task_queue.enqueue(task)
                except Exception:
                    pass
        except Exception:
            pass

    def enhance_tool_registry(self, tool_registry: Any) -> None:
        try:
            if tool_registry is None or not hasattr(tool_registry, "tools"):
                return
            names = {getattr(t, "name", "") for t in tool_registry.tools}
            if "knowledge_search" in names:
                return
            tool_registry.tools.append(KnowledgeSearchTool(self))
            tool_registry.logger.info("Tool enriched with knowledge_search")
        except Exception:
            pass

    def to_context(self, query: str, *, limit: int = 5) -> str:
        return self.context(query, k=limit)

    def start_background_refresh(self, interval: int = 60) -> None:
        def worker() -> None:
            while True:
                try:
                    self.refresh_if_changed()
                except Exception:
                    pass
                time.sleep(interval)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    # Internal helper

    @staticmethod
    def _latest_mtime(folder: str) -> float:
        try:
            paths = [p for p in Path(folder).rglob("*") if p.is_file()]
            return max((p.stat().st_mtime for p in paths), default=0.0)
        except Exception:
            return 0.0


def json_dumps(obj: Any) -> str:
    try:
        import json
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)


class KnowledgeSearchTool:
    name = "knowledge_search"
    description = "Semantic search across indexed local documents."

    def __init__(self, engine: KnowledgeEngine) -> None:
        self.engine = engine

    def can_handle(self, prompt: str) -> bool:
        return any(p in (prompt or "").lower() for p in ["knowledge", "document", "file", "notes", "index"])

    def execute(self, query: str = "", **kwargs: Any) -> Any:
        query = query or kwargs.get("query", "")
        if not query:
            return type("ToolResult", (), {"success": False, "error": "Missing query"})()
        results = self.engine.search(query, k=5)
        return type("ToolResult", (), {"success": True, "output": "\n\n".join([r.get("content", "") for r in results])})
