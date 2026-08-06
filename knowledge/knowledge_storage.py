"""Knowledge storage using ChromaDB with metadata fallback storage."""
from __future__ import annotations

import atexit
import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional


class KnowledgeStorage:
    def __init__(self, directory: str | Path, *, use_chroma: bool = True) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self._lock = threading.RLock()
        self._chroma = None
        self._chroma_client = None
        if use_chroma:
            try:
                import chromadb  # type: ignore
                client = chromadb.PersistentClient(path=str(directory / "chroma"))
                self._chroma_client = client
                self._chroma = client.get_or_create_collection("knowledge")
            except Exception:
                self._chroma = None

        self._sqlite = directory / "knowledge.sqlite"
        self._conn = sqlite3.connect(self._sqlite)
        try:
            if hasattr(sqlite3, "SQLITE_DBCONFIG_ENABLE_FKEY") or True:
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    doc_id TEXT PRIMARY KEY,
                    source TEXT,
                    filename TEXT,
                    extension TEXT,
                    checksum TEXT,
                    indexed_at REAL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    fragment_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id)")
            self._conn.commit()
        except Exception:
            pass
        atexit.register(self.close)

    def close(self) -> None:
        try:
            client = getattr(self, "_chroma_client", None)
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
                self._chroma_client = None
                self._chroma = None
        except Exception:
            pass
        try:
            conn = getattr(self, "_conn", None)
            if conn is not None:
                conn.close()
                self._conn = None
        except Exception:
            pass

    def enabled(self) -> bool:
        return self._chroma is not None

    def upsert_document(self, doc_id: str, metadata: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO files(doc_id, source, filename, extension, checksum, indexed_at, metadata) "
                "VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(doc_id) DO UPDATE SET source=excluded.source, filename=excluded.filename, extension=excluded.extension, checksum=excluded.checksum, indexed_at=excluded.indexed_at, metadata=excluded.metadata",
                (
                    doc_id,
                    metadata.get("source"),
                    metadata.get("filename"),
                    metadata.get("extension"),
                    metadata.get("checksum"),
                    metadata.get("indexed_at"),
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            self._conn.commit()

    def delete_document(self, doc_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
            self._conn.execute("DELETE FROM files WHERE doc_id=?", (doc_id,))
            self._conn.commit()
            if self._chroma is not None:
                try:
                    self._chroma.delete(where={"doc_id": doc_id})
                except Exception:
                    pass

    def add_chunks(self, doc_id: str, chunks: list[Any]) -> None:
        if not chunks:
            return
        texts = [c.text for c in chunks]
        metadatas = [{**c.metadata, "doc_id": doc_id} for c in chunks]
        ids = [c.id for c in chunks]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO chunks(chunk_id, doc_id, fragment_index, content, metadata) VALUES(?,?,?,?,?)",
                [
                    (
                        cid,
                        doc_id,
                        idx,
                        text,
                        json.dumps(meta, ensure_ascii=False),
                    )
                    for cid, idx, text, meta in zip(
                        ids,
                        [m.get("index", 0) for m in metadatas],
                        texts,
                        metadatas,
                    )
                ],
            )
            self._conn.commit()
        if self._chroma is not None:
            try:
                self._chroma.add(documents=texts, metadatas=metadatas, ids=ids)
            except Exception:
                pass

    def query(self, query_text: str, *, k: int = 5, where: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if self._chroma is not None:
            try:
                data = self._chroma.query(query_texts=[query_text], n_results=k, where=where or {})
                docs = data.get("documents", [[]])[0]
                metas = data.get("metadatas", [[]])[0]
                for text, meta in zip(docs, metas):
                    results.append({"content": text, "metadata": meta or {}})
                return results
            except Exception:
                pass
        with self._lock:
            rows = self._conn.execute(
                "SELECT chunk_id, doc_id, content, metadata FROM chunks ORDER BY rowid DESC LIMIT ?",
                (k,),
            ).fetchall()
            for chunk_id, doc_id, content, metadata in rows:
                meta = json.loads(metadata) if metadata else {}
                results.append(
                    {
                        "chunk_id": chunk_id,
                        "doc_id": doc_id,
                        "content": content,
                        "metadata": meta,
                    }
                )
        return results

    def load_document(self, doc_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT doc_id, source, filename, metadata FROM files WHERE doc_id=?", (doc_id,)).fetchone()
            if not row:
                return None
            return {
                "doc_id": row[0],
                "source": row[1],
                "filename": row[2],
                "metadata": json.loads(row[3]) if row[3] else {},
            }

    @staticmethod
    def checksum(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()
