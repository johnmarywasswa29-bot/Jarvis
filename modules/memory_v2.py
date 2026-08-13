"""Memory v2: SQLite structured memory + ChromaDB vector memory.

Compatibility layer:
- JarvisMemoryV2: add_message(), get_recent_context(), search(), shutdown()
- MemoryManager: typed memories, scoring, retrieval, decay, consolidation, migration
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from modules.config import JarvisConfig
from modules.logger import get_logger

logger = get_logger("memory")

try:
    import chromadb  # type: ignore
    _HAS_CHROMA = True
except Exception:
    _HAS_CHROMA = False


class JarvisMemoryV2:
    def __init__(self, config: JarvisConfig, *, use_chroma: bool = True) -> None:
        self.config = config
        memory_dir = config.memory_path()
        self.db_path = memory_dir / "jarvis.sqlite"
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_sqlite(self._conn)
        self._chroma = None
        if use_chroma and _HAS_CHROMA:
            try:
                client = chromadb.PersistentClient(path=str(memory_dir / "chroma"))
                self._chroma = client.get_or_create_collection("memory")
                logger.info("ChromaDB memory collection ready")
            except Exception as exc:
                logger.warning("ChromaDB disabled: %s", exc)
                self._chroma = None
        self._lock = threading.Lock()
        self._msg_buffer: list[tuple[str, str, float, str]] = []
        self._max_buffer = 20

    def _init_sqlite(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                ts REAL NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_role_ts ON messages(role, ts)"
        )

    def add_message(self, role: str, content: str, metadata: Optional[dict[str, Any]] = None) -> None:
        ts = time.time()
        payload = json.dumps(metadata or {}, ensure_ascii=False)
        with self._lock:
            self._msg_buffer.append((role, content, ts, payload))
            if len(self._msg_buffer) >= self._max_buffer:
                self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._msg_buffer:
            return
        batch = self._msg_buffer[:]
        self._msg_buffer.clear()
        self._conn.executemany(
            "INSERT INTO messages(role, content, ts, metadata) VALUES(?,?,?,?)",
            [(r, c, t, m) for r, c, t, m in batch],
        )
        self._conn.commit()
        if self._chroma is not None and batch:
            try:
                docs = [c for _, c, _, _ in batch]
                metas = [{"role": r, "ts": t} for r, _, t, _ in batch]
                ids = [hashlib.sha1(f"{t}{r}{c}".encode("utf-8")).hexdigest() for r, c, t, _ in batch]
                self._chroma.add(documents=docs, metadatas=metas, ids=ids)
            except Exception as exc:
                logger.debug("Chroma batch insert skipped: %s", exc)

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def get_recent_context(self, max_messages: int = 20, max_chars: int = 4000) -> str:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content FROM messages ORDER BY ts DESC LIMIT ?",
                (max_messages,),
            ).fetchall()
        lines = [f"{r[0].upper()}: {r[1]}" for r in reversed(rows)]
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[-max_chars:]
        return text

    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if self._chroma is not None:
            try:
                results = self._chroma.query(query_texts=[query], n_results=k)
                docs = results.get("documents", [[]])[0]
                metas = results.get("metadatas", [[]])[0]
                if docs:
                    for text, meta in zip(docs, metas):
                        out.append({"content": text, "metadata": meta or {}})
                    return out
            except Exception as exc:
                logger.debug("Chroma query failed: %s", exc)
        q = query.lower()
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, ts, metadata FROM messages WHERE content LIKE ? ORDER BY ts DESC LIMIT ?",
                (f"%{q}%", k),
            ).fetchall()
        for role, content, ts, metadata in rows:
            out.append(
                {
                    "role": role,
                    "content": content,
                    "ts": ts,
                    "metadata": json.loads(metadata) if metadata else {},
                }
            )
        return out

    def shutdown(self) -> None:
        try:
            self.flush()
            with self._lock:
                self._conn.execute("VACUUM")
                self._conn.close()
            logger.info("Memory persisted to %s", self.db_path)
        except Exception as exc:
            logger.error("Memory shutdown error: %s", exc)


@dataclass
class MemoryRecord:
    memory_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    memory_type: str = "episodic"
    content: str = ""
    importance: float = 0.5
    confidence: float = 0.5
    access_count: int = 0
    last_accessed: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    source: str = "user"
    tags: list[str] = field(default_factory=list)
    related_memories: list[str] = field(default_factory=list)
    decay_score: float = 1.0

    def to_row(self) -> tuple[str, str, str, Optional[bytes], float, float, int, float, float, str, str, str, float]:
        return (
            self.memory_id,
            self.memory_type,
            self.content,
            None,
            self.importance,
            self.confidence,
            self.access_count,
            self.last_accessed,
            self.created_at,
            self.source,
            json.dumps(self.tags, ensure_ascii=False),
            json.dumps(self.related_memories, ensure_ascii=False),
            self.decay_score,
        )

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> "MemoryRecord":
        if len(row) < 11:
            raise ValueError("row too short")
        created_idx = 8 if len(row) >= 13 else 7
        source_idx = 9 if len(row) >= 13 else 8
        tags_idx = 10 if len(row) >= 13 else 9
        related_idx = 11 if len(row) >= 13 else 10
        decay_idx = 12 if len(row) >= 13 else 11
        return cls(
            memory_id=row[0],
            memory_type=row[1],
            content=row[2],
            importance=float(row[3]) if len(row) < 13 else float(row[4]),
            confidence=float(row[4]) if len(row) < 13 else float(row[5]),
            access_count=int(row[5]) if len(row) < 13 else int(row[6]),
            last_accessed=float(row[6]) if len(row) < 13 else float(row[7]),
            created_at=float(row[created_idx]),
            source=row[source_idx],
            tags=json.loads(row[tags_idx]) if row[tags_idx] else [],
            related_memories=json.loads(row[related_idx]) if row[related_idx] else [],
            decay_score=float(row[decay_idx]),
        )


class MemoryManager:
    def __init__(self, config: JarvisConfig) -> None:
        self.config = config
        memory_dir = config.memory_path()
        self.db_path = memory_dir / "memory_v3.sqlite"
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema(self._conn)
        self._lock = threading.Lock()
        # Embedder is loaded lazily on first semantic need to keep startup fast.
        self._embed = None

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                memory_type TEXT NOT NULL CHECK(memory_type IN ('episodic','semantic','procedural')),
                content TEXT NOT NULL,
                embedding BLOB,
                importance REAL NOT NULL DEFAULT 0.5,
                confidence REAL NOT NULL DEFAULT 0.5,
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed REAL NOT NULL,
                created_at REAL NOT NULL,
                source TEXT NOT NULL DEFAULT 'user',
                tags TEXT NOT NULL DEFAULT '[]',
                related_memories TEXT NOT NULL DEFAULT '[]',
                decay_score REAL NOT NULL DEFAULT 1.0
            )
        """,
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON memories(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_last_accessed ON memories(last_accessed)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_importance ON memories(importance)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decay ON memories(decay_score)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_data TEXT NOT NULL DEFAULT '{}',
                ts REAL NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_events_mid ON memory_events(memory_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_consolidation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_ts REAL NOT NULL,
                promoted INTEGER NOT NULL DEFAULT 0,
                decayed INTEGER NOT NULL DEFAULT 0,
                merged INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.commit()

    @classmethod
    def _init_embedder(cls):
        if getattr(cls, "_shared_embed", "sentinel") is not None and getattr(cls, "_shared_embed", None) is not None:
            return cls._shared_embed
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            model = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("SentenceTransformer memory embedder loaded")
            cls._shared_embed = model
            return model
        except Exception as exc:
            logger.warning("Embeddings disabled: %s", exc)
            cls._shared_embed = None
            return None

    def _ensure_embedder(self):
        """Lazily initialize the shared embedder on first semantic need."""
        if self._embed is not None:
            return self._embed
        try:
            from core.embedder_cache import get_embedder
            self._embed = get_embedder("all-MiniLM-L6-v2")
        except Exception as exc:
            logger.debug("Embedder lazy init skipped: %s", exc)
            self._embed = None
        return self._embed

    def _event(self, memory_id: str, event_type: str, event_data: dict[str, Any]) -> None:
        try:
            self._conn.execute(
                "INSERT INTO memory_events(memory_id, event_type, event_data, ts) VALUES(?,?,?,?)",
                (memory_id, event_type, json.dumps(event_data, ensure_ascii=False), time.time()),
            )
        except Exception:
            pass

    def add_memory(
        self,
        content: str,
        memory_type: str = "episodic",
        *,
        importance: float = 0.5,
        confidence: float = 0.5,
        source: str = "user",
        tags: Optional[list[str]] = None,
        related_memories: Optional[list[str]] = None,
        deduplicate: bool = True,
    ) -> MemoryRecord:
        if memory_type not in {"episodic", "semantic", "procedural"}:
            memory_type = "episodic"
        tags = tags or []
        related_memories = related_memories or []
        with self._lock:
            if deduplicate:
                row = self._conn.execute(
                    "SELECT id, access_count, created_at FROM memories WHERE memory_type=? AND content=? ORDER BY created_at DESC LIMIT 1",
                    (memory_type, content),
                ).fetchone()
                if row:
                    mem_id, access_count, created_at = row
                    importance_row = self._conn.execute("SELECT importance, confidence FROM memories WHERE id=?", (mem_id,)).fetchone()
                    existing_importance = float(importance_row[0]) if importance_row else 0.5
                    existing_confidence = float(importance_row[1]) if importance_row else 0.5
                    self._conn.execute(
                        "UPDATE memories SET access_count=?, last_accessed=?, confidence=? WHERE id=?",
                        (int(access_count) + 1, time.time(), min(1.0, existing_confidence + 0.1), mem_id),
                    )
                    self._event(mem_id, "access", {"access_count": int(access_count) + 1})
                    self._conn.commit()
                    return MemoryRecord(
                        memory_id=mem_id,
                        memory_type=memory_type,
                        content=content,
                        importance=existing_importance,
                        confidence=min(1.0, existing_confidence + 0.1),
                        access_count=int(access_count) + 1,
                        last_accessed=time.time(),
                        source=source,
                        tags=tags,
                        related_memories=related_memories,
                        decay_score=1.0,
                    )
            record = MemoryRecord(
                memory_type=memory_type,
                content=content,
                importance=importance,
                confidence=confidence,
                source=source,
                tags=tags,
                related_memories=related_memories,
            )
            self._conn.execute(
                "INSERT INTO memories(id, memory_type, content, embedding, importance, confidence, access_count, last_accessed, created_at, source, tags, related_memories, decay_score) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                record.to_row(),
            )
            self._event(record.memory_id, "create", {"type": memory_type, "source": source})
            self._conn.commit()
            embedder = self._ensure_embedder()
            if embedder is not None:
                try:
                    vec = embedder.encode(content)
                    self._conn.execute(
                        "UPDATE memories SET embedding=? WHERE id=?",
                        (vec.tobytes(), record.memory_id),
                    )
                    self._conn.commit()
                except Exception as exc:
                    logger.debug("Embedding skipped: %s", exc)
        return record

    def get_memory(self, memory_id: str) -> Optional[MemoryRecord]:
        row = self._conn.execute(
            "SELECT id, memory_type, content, embedding, importance, confidence, access_count, last_accessed, created_at, source, tags, related_memories, decay_score FROM memories WHERE id=?",
            (memory_id,),
        ).fetchone()
        if row is None:
            return None
        with self._lock:
            self._conn.execute(
                "UPDATE memories SET access_count=access_count+1, last_accessed=? WHERE id=?",
                (time.time(), memory_id),
            )
        self._event(memory_id, "access", {})
        return MemoryRecord.from_row(row)

    def update_memory(self, memory_id: str, **kwargs: Any) -> bool:
        allowed = {"importance", "confidence", "memory_type", "content", "tags", "related_memories", "decay_score", "source"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        sets = ", ".join(f"{k}=?" for k in updates)
        values = []
        for k in updates:
            v = updates[k]
            if isinstance(v, (list, dict)):
                v = json.dumps(v, ensure_ascii=False)
            values.append(v)
        values += [time.time(), memory_id]
        with self._lock:
            cur = self._conn.execute(f"UPDATE memories SET {sets}, last_accessed=? WHERE id=?", values)
            if cur.rowcount:
                self._event(memory_id, "update", updates)
                return True
        return False

    def delete_memory(self, memory_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            if cur.rowcount:
                self._conn.execute("DELETE FROM memory_events WHERE memory_id=?", (memory_id,))
                return True
        return False

    def _semantic_scores(self, query: str, ids: list[str]) -> dict[str, float]:
        if self._embed is None or not ids:
            return {}
        try:
            q_vec = self._embed.encode(query)
            scores: dict[str, float] = {}
            rows = self._conn.execute(
                f"SELECT id, embedding FROM memories WHERE id IN ({','.join('?' for _ in ids)})",
                ids,
            ).fetchall()
            import numpy as np
            q = np.asarray(q_vec)
            q = q / (np.linalg.norm(q) + 1e-9)
            for mem_id, blob in rows:
                if blob is None:
                    continue
                v = np.frombuffer(blob, dtype=np.float32)
                if v.size == 0:
                    continue
                v = v / (np.linalg.norm(v) + 1e-9)
                scores[mem_id] = float((q * v).sum())
            return scores
        except Exception as exc:
            logger.debug("Semantic scoring failed: %s", exc)
            return {}

    def search(
        self,
        query: str,
        *,
        types: Optional[list[str]] = None,
        limit: int = 10,
        weights: Optional[dict[str, float]] = None,
    ) -> list[dict[str, Any]]:
        weights = weights or {"recency": 0.25, "importance": 0.45, "confidence": 0.3}
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, memory_type, content, embedding, importance, confidence, access_count, last_accessed, created_at, source, tags, related_memories, decay_score FROM memories",
            ).fetchall()
        candidates: list[tuple[float, dict[str, Any]]] = []
        now = time.time()
        ids: list[str] = []
        for row in rows:
            rec = MemoryRecord.from_row(row)
            if types and rec.memory_type not in types:
                continue
            recency = max(0.0, min(1.0, (now - rec.last_accessed) / (30 * 24 * 3600)))
            ids.append(rec.memory_id)
            candidates.append((0.0, {
                "memory": rec,
                "recency_score": 1.0 - recency,
            }))
        sem = self._semantic_scores(query, ids)
        scored: list[tuple[float, dict[str, Any]]] = []
        for base, item in candidates:
            rec = item["memory"]
            sem_s = sem.get(rec.memory_id, 0.0)
            rec_s = item["recency_score"]
            imp_s = rec.importance
            conf_s = rec.confidence
            score = (
                weights.get("recency", 0.25) * rec_s
                + weights.get("importance", 0.45) * imp_s
                + weights.get("confidence", 0.3) * conf_s
                + (0.15 if sem_s > 0 else 0.0) * max(0.0, sem_s)
            )
            scored.append((score, {
                "memory": rec,
                "score": score,
                "recency_score": rec_s,
                "importance_score": imp_s,
                "confidence_score": conf_s,
                "semantic_score": sem_s,
            }))
        scored.sort(key=lambda x: x[0], reverse=True)
        out: list[dict[str, Any]] = []
        for _, item in scored[: max(1, limit)]:
            m = item["memory"]
            out.append({
                "id": m.memory_id,
                "memory_type": m.memory_type,
                "content": m.content,
                "importance": m.importance,
                "confidence": m.confidence,
                "access_count": m.access_count,
                "last_accessed": m.last_accessed,
                "created_at": m.created_at,
                "source": m.source,
                "tags": m.tags,
                "related_memories": m.related_memories,
                "decay_score": m.decay_score,
                "score": item["score"],
                "recency_score": item["recency_score"],
                "importance_score": item["importance_score"],
                "confidence_score": item["confidence_score"],
                "semantic_score": item["semantic_score"],
            })
        return out

    def get_recent(self, memory_type: Optional[str] = None, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            if memory_type:
                rows = self._conn.execute(
                    "SELECT id, memory_type, content, embedding, importance, confidence, access_count, last_accessed, created_at, source, tags, related_memories, decay_score FROM memories WHERE memory_type=? ORDER BY created_at DESC LIMIT ?",
                    (memory_type, max(1, limit)),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, memory_type, content, embedding, importance, confidence, access_count, last_accessed, created_at, source, tags, related_memories, decay_score FROM memories ORDER BY created_at DESC LIMIT ?",
                    (max(1, limit),),
                ).fetchall()
        out = []
        for row in rows:
            m = MemoryRecord.from_row(row)
            out.append({
                "id": m.memory_id,
                "memory_type": m.memory_type,
                "content": m.content,
                "importance": m.importance,
                "confidence": m.confidence,
                "access_count": m.access_count,
                "last_accessed": m.last_accessed,
                "created_at": m.created_at,
                "source": m.source,
                "tags": m.tags,
                "related_memories": m.related_memories,
                "decay_score": m.decay_score,
            })
        return out

    def get_important(self, memory_type: Optional[str] = None, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            if memory_type:
                rows = self._conn.execute(
                    "SELECT id, memory_type, content, embedding, importance, confidence, access_count, last_accessed, created_at, source, tags, related_memories, decay_score FROM memories WHERE memory_type=? ORDER BY importance DESC, decay_score DESC LIMIT ?",
                    (memory_type, max(1, limit)),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, memory_type, content, embedding, importance, confidence, access_count, last_accessed, created_at, source, tags, related_memories, decay_score FROM memories ORDER BY importance DESC, decay_score DESC LIMIT ?",
                    (max(1, limit),),
                ).fetchall()
        out = []
        for row in rows:
            m = MemoryRecord.from_row(row)
            out.append({
                "id": m.memory_id,
                "memory_type": m.memory_type,
                "content": m.content,
                "importance": m.importance,
                "confidence": m.confidence,
                "access_count": m.access_count,
                "last_accessed": m.last_accessed,
                "created_at": m.created_at,
                "source": m.source,
                "tags": m.tags,
                "related_memories": m.related_memories,
                "decay_score": m.decay_score,
            })
        return out

    def decay_pass(self, now: Optional[float] = None) -> dict[str, int]:
        now = now or time.time()
        promoted = 0
        decayed = 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, importance, confidence, access_count, decay_score, created_at FROM memories"
            ).fetchall()
            updates = []
            deletes = []
            for row in rows:
                mem_id, importance, confidence, access_count, decay_score, created_at = row
                age_days = max(0.0, (now - float(created_at)) / (24 * 3600))
                new_decay = max(0.0, 1.0 - (int(access_count) * 0.01) - (age_days * 0.001))
                if new_decay < 0.1 and float(importance) < 0.3 and float(confidence) < 0.5:
                    deletes.append((mem_id,))
                    decayed += 1
                    continue
                if new_decay < 0.2 and float(importance) >= 0.7:
                    new_importance = min(1.0, float(importance) + 0.1)
                    updates.append((new_decay, new_importance, time.time(), mem_id))
                    promoted += 1
                else:
                    updates.append((new_decay, float(importance), time.time(), mem_id))
            for row in deletes:
                self._conn.execute("DELETE FROM memories WHERE id=?", row)
            for upd in updates:
                self._conn.execute("UPDATE memories SET decay_score=?, importance=?, last_accessed=? WHERE id=?", upd)
            self._conn.execute(
                "INSERT INTO memory_consolidation_log(run_ts, promoted, decayed, merged) VALUES(?,?,?,?)",
                (now, promoted, decayed, 0),
            )
        return {"promoted": promoted, "decayed": decayed, "merged": 0}

    def consolidate(self) -> dict[str, int]:
        merged = 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, memory_type, content, tags FROM memories ORDER BY created_at ASC"
            ).fetchall()
            by_key: dict[str, list[tuple[str, str, list[str]]]] = {}
            for row in rows:
                mem_id, memory_type, content, tags_json = row
                tags = json.loads(tags_json) if tags_json else []
                key = (memory_type, content.strip().lower(), tuple(sorted(tags)))
                by_key.setdefault(key, []).append((mem_id, content, tags))
            delete_ids = []
            for key, items in by_key.items():
                if len(items) <= 1:
                    continue
                for mem_id, _, _ in items[1:]:
                    delete_ids.append((mem_id,))
                merged += len(items) - 1
            for mem_id in delete_ids:
                self._conn.execute("DELETE FROM memories WHERE id=?", (mem_id,))
                self._conn.execute("DELETE FROM memory_events WHERE memory_id=?", (mem_id,))
            if merged:
                self._conn.execute(
                    "INSERT INTO memory_consolidation_log(run_ts, promoted, decayed, merged) VALUES(?,?,?,?)",
                    (time.time(), 0, 0, merged),
                )
        return {"promoted": 0, "decayed": 0, "merged": merged}

    def migrate_from_v2(self, v2_db_path: Path) -> int:
        if not v2_db_path.exists():
            return 0
        conn = sqlite3.connect(v2_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT role, content, ts, metadata FROM messages").fetchall()
        count = 0
        for row in rows:
            role = row["role"]
            content = row["content"]
            ts = row["ts"]
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
            memory_type = "episodic"
            if role == "assistant":
                memory_type = "semantic"
            tags = metadata.get("tags", [])
            if not isinstance(tags, list):
                tags = [str(tags)]
            self.add_memory(
                content,
                memory_type=memory_type,
                importance=0.5,
                confidence=0.5,
                source="migration",
                tags=tags,
                deduplicate=True,
            )
            count += 1
        conn.close()
        logger.info("Migrated %d memories from v2", count)
        return count

    def shutdown(self) -> None:
        try:
            with self._lock:
                try:
                    self._conn.execute("COMMIT")
                except Exception:
                    pass
                self._conn.execute("VACUUM")
                self._conn.close()
            logger.info("MemoryManager shutdown complete")
        except Exception as exc:
            logger.error("MemoryManager shutdown error: %s", exc)
