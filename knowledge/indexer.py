"""Indexer: scan folders, load documents, chunk, and store in ChromaDB."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Iterable, Optional

from knowledge.document_loader import DocumentLoadError, load_document
from knowledge.chunker import Chunker, Chunk
from knowledge.knowledge_storage import KnowledgeStorage


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".txt",
    ".md",
    ".docx",
    ".py",
    ".json",
    ".html",
    ".csv",
    ".java",
    ".js",
    ".c",
    ".cpp",
    ".h",
    ".log",
    ".rtf",
    ".pptx",
    ".ppt",
    ".xlsx",
    ".xls",
    ".eml",
}


class KnowledgeIndexer:
    def __init__(self, storage: KnowledgeStorage, chunker: Optional[Chunker] = None) -> None:
        self.storage = storage
        self.chunker = chunker or Chunker()

    def index_file(self, path: str | Path) -> Optional[str]:
        p = Path(path)
        if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return None
        try:
            text, metadata = load_document(p)
        except DocumentLoadError:
            return None
        content_hash = KnowledgeStorage.checksum(text)
        doc_id = f"{p.resolve()}:{content_hash}"
        existing = self.storage.load_document(doc_id)
        if existing and existing.get("metadata", {}).get("checksum") == content_hash:
            return doc_id

        if existing:
            self.storage.delete_document(doc_id)
        metadata.update(
            {
                "indexed_at": time.time(),
                "checksum": content_hash,
                "extension": p.suffix.lower(),
            }
        )
        self.storage.upsert_document(doc_id, metadata)
        chunks = self.chunker.chunk(text, metadata={k: v for k, v in metadata.items() if k in {"filename", "extension", "source"}})
        self.storage.add_chunks(doc_id, chunks)
        return doc_id

    def index_folder(self, folder: str | Path) -> list[str]:
        folder = Path(folder)
        ids: list[str] = []
        if not folder.exists():
            return ids
        for root, _, files in os.walk(folder):
            for name in files:
                path = Path(root) / name
                if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                    doc_id = self.index_file(path)
                    if doc_id:
                        ids.append(doc_id)
        return ids
