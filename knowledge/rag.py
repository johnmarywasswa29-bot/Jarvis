"""RAG service: orchestrator for indexing, retrieval, memory/intent integration, settings."""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from knowledge.knowledge_engine import KnowledgeEngine
from knowledge.chunker import Chunker, Chunk
from knowledge.ranking import RankingEngine
from knowledge.watcher import KnowledgeWatcher
from modules.intent import IntentAnalyzer
from modules.memory_v2 import MemoryManager


logger = logging.getLogger(__name__)


class RAGService:
    def __init__(
        self,
        root_dir: str | Path,
        *,
        memory: Optional[MemoryManager] = None,
        intent_analyzer: Optional[IntentAnalyzer] = None,
        config: Optional[Any] = None,
        chunk_size: int = 1200,
        chunk_overlap: int = 120,
        max_file_size: int = 20 * 1024 * 1024,
        watch_interval: float = 3600.0,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.memory = memory
        self.intent_analyzer = intent_analyzer

        self.engine = KnowledgeEngine(root_dir=self.root_dir)
        self.engine.indexer.chunker = Chunker(chunk_size=chunk_size, overlap=chunk_overlap, prefer_semantic=True)
        self.engine.embedder = self.engine.embedder
        self.ranking_engine = RankingEngine()
        self.engine.retriever.ranking_engine = self.ranking_engine

        self._watch_lock = threading.RLock()
        self._watcher = KnowledgeWatcher(poll_interval=watch_interval)
        self._started = False

    # Configuration

    def apply_config(self, config: Any) -> None:
        cfg = config
        self.root_dir = Path(cfg.knowledge_root)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.engine.storage = self.engine.storage.__class__(self.root_dir / "store")
        self.engine.indexer = type(self.engine.indexer)(self.engine.storage, Chunker(chunk_size=cfg.knowledge_chunk_size, overlap=cfg.knowledge_chunk_overlap, prefer_semantic=True))
        if cfg.knowledge_auto_index_enabled:
            self.start_watcher(
                folders=cfg.knowledge_indexed_folders,
                ignore_dirs=set(cfg.knowledge_ignore_dirs),
                extensions=set() if cfg.knowledge_ignore_extensions else None,
                max_size=cfg.knowledge_max_file_size,
                interval=cfg.knowledge_auto_index_interval_s,
            )

    def start_watcher(
        self,
        folders: list[str],
        *,
        ignore_dirs: Optional[set[str]] = None,
        extensions: Optional[set[str]] = None,
        max_size: Optional[int] = None,
        interval: Optional[float] = None,
    ) -> None:
        with self._watch_lock:
            if self._started:
                return
            ignore_dirs = ignore_dirs or {".git", "__pycache__", "node_modules", "venv", ".venv", "dist", "build"}
            extensions = extensions or {".pdf", ".txt", ".md", ".docx", ".py", ".json", ".html", ".csv", ".java", ".js", ".c", ".cpp", ".h", ".log", ".rtf", ".pptx", ".ppt", ".xlsx", ".xls", ".eml"}
            max_size = max_size or (20 * 1024 * 1024)
            interval = interval or 3600.0
            self._watcher = KnowledgeWatcher(poll_interval=interval)
            for folder in folders:
                if Path(folder).exists():
                    self._watcher.add_watch(
                        folder,
                        index_fn=lambda path, svc=self: svc.index_file(path),
                        ignore_dirs=ignore_dirs,
                        extensions=extensions,
                        max_size=max_size,
                    )
            self._started = True

    def stop_watcher(self) -> None:
        with self._watch_lock:
            self._watcher.stop()
            self._started = False

    # Indexing API

    def index_file(self, path: str | Path) -> Optional[str]:
        return self.engine.index_file(path)

    def index_folder(self, folder: str | Path) -> list[str]:
        return self.engine.index_folder(folder)

    def forget(self, doc_id: str) -> None:
        self.engine.forget(doc_id)

    # Search API

    def search(self, query: str, *, k: Optional[int] = None, filters: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        k = k or (self.config.knowledge_search_k if self.config else 5)
        return self.engine.search(query, k=k, filters=filters)

    def context(self, query: str, *, max_chars: Optional[int] = None, k: Optional[int] = None) -> str:
        max_chars = max_chars or (self.config.knowledge_max_context_chars if self.config else 2500)
        k = k or (self.config.knowledge_search_k if self.config else 5)
        return self.engine.context(query, max_chars=max_chars, k=k)

    def open_document(self, doc_id: str) -> Optional[str]:
        doc = self.engine.storage.load_document(doc_id)
        if not doc:
            return None
        source = doc.get("source") or doc.get("metadata", {}).get("source")
        if source and Path(source).exists():
            os.startfile(str(Path(source).resolve()))
            return source
        return None

    # Memory/intent integration

    def enhance_query_with_memory(self, query: str) -> str:
        if self.memory is None:
            return query
        try:
            recent = getattr(self.memory, "get_recent_and_important", lambda q, **k: [])
            hits = recent(query, n=3)
            snippets = " ".join((h.get("content") or "") for h in hits if isinstance(h, dict))
            return f"{query}\nMemory: {snippets}" if snippets else query
        except Exception:
            return query

    def enrich_intent(self, intent_result: dict[str, Any]) -> dict[str, Any]:
        intent = intent_result.get("intent", "")
        if intent not in {"knowledge.lookup", "document.search", "summarize.notes", "research"}:
            return intent_result
        query = intent_result.get("query") or intent_result.get("prompt") or ""
        context = self.context(query)
        intent_result["knowledge_context"] = context
        return intent_result

    def remember_query(self, query: str, success: bool = True) -> None:
        if self.memory is None:
            return
        try:
            tag = "knowledge_search"
            if success:
                self.memory.add_message("knowledge", query, metadata={"source": "rag", "tag": tag})
        except Exception:
            pass

    # Stats / UI helpers

    def index_stats(self) -> dict[str, Any]:
        with self.engine.storage._lock:
            try:
                file_count = self.engine.storage._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
                chunk_count = self.engine.storage._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                last_index = self.engine.storage._conn.execute("SELECT MAX(indexed_at) FROM files").fetchone()[0]
            except Exception:
                file_count = 0
                chunk_count = 0
                last_index = None
        return {
            "documents": file_count,
            "chunks": chunk_count,
            "last_indexed": last_index,
            "watcher_running": self._started,
        }

    def close(self) -> None:
        try:
            self.stop_watcher()
            self.engine.close()
        except Exception:
            pass
