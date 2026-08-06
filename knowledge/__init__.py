"""Knowledge package: local document indexing, retrieval, and integration."""
from __future__ import annotations

from knowledge.knowledge_engine import KnowledgeEngine
from knowledge.document_loader import DocumentLoadError, load_document
from knowledge.document_parser import DocumentParser
from knowledge.chunker import Chunker, Chunk
from knowledge.embedder import Embedder
from knowledge.retriever import KnowledgeRetriever
from knowledge.indexer import KnowledgeIndexer
from knowledge.knowledge_storage import KnowledgeStorage
from knowledge.ranking import RankingEngine
from knowledge.watcher import KnowledgeWatcher

__all__ = [
    "KnowledgeEngine",
    "DocumentLoadError",
    "load_document",
    "DocumentParser",
    "Chunker",
    "Chunk",
    "Embedder",
    "KnowledgeRetriever",
    "KnowledgeIndexer",
    "KnowledgeStorage",
    "RankingEngine",
    "KnowledgeWatcher",
]
