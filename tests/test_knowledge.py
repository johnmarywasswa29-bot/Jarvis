"""Tests for the knowledge package."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
os.chdir(REPO)

from knowledge.document_loader import DocumentLoadError, load_document
from knowledge.document_parser import DocumentParser
from knowledge.chunker import Chunker, Chunk
from knowledge.knowledge_storage import KnowledgeStorage
from knowledge.indexer import KnowledgeIndexer
from knowledge.knowledge_engine import KnowledgeEngine, KnowledgeSearchTool


class FakeMemory:
    def __init__(self):
        self.messages = []
    def add_message(self, role, content, metadata=None):
        self.messages.append({"role": role, "content": content, "metadata": metadata or {}})


class TestDocumentLoader(unittest.TestCase):
    def test_text_document(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt", encoding="utf-8") as f:
            f.write("Hello Jarvis.")
            path = f.name
        try:
            text, meta = load_document(path)
            self.assertEqual(text, "Hello Jarvis.")
            self.assertEqual(meta["filename"], Path(path).name)
            self.assertEqual(meta["extension"], ".txt")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_unsupported_reads_as_text(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".bin", encoding="utf-8") as f:
            f.write("binary-ish")
            path = f.name
        try:
            text, meta = load_document(path)
            self.assertIn("binary-ish", text)
            self.assertEqual(meta["mime"], "text/plain")
        finally:
            Path(path).unlink(missing_ok=True)


class TestDocumentParser(unittest.TestCase):
    def test_guess_kind(self):
        parser = DocumentParser()
        result = parser.parse("```python\nprint('hi')\n```", kind_hint="code")
        self.assertEqual(result["kind"], "code")


class TestChunker(unittest.TestCase):
    def test_empty(self):
        chunker = Chunker(chunk_size=100, overlap=10)
        self.assertEqual(chunker.chunk(""), [])

    def test_split(self):
        chunker = Chunker(chunk_size=40, overlap=5, min_chars=5)
        text = "Alpha beta gamma delta epsilon zeta eta theta iota kappa"
        chunks = chunker.chunk(text, metadata={"source": "x"})
        self.assertGreaterEqual(len(chunks), 1)
        self.assertEqual(chunks[0].metadata["source"], "x")
        self.assertTrue(chunks[0].text)

    def test_chunk_ids(self):
        chunker = Chunker(chunk_size=20, overlap=2)
        text = "One two three four five six."
        chunks = chunker.chunk(text)
        ids = [c.id for c in chunks]
        self.assertEqual(len(ids), len(set(ids)))


class TestKnowledgeStorage(unittest.TestCase):
    def test_sqlite_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = KnowledgeStorage(tmp)
            try:
                doc_id = "doc:1"
                meta = {"source": "notes", "filename": "notes.txt", "extension": ".txt", "checksum": "abc", "indexed_at": time.time()}
                store.upsert_document(doc_id, meta)
                store.add_chunks(doc_id, [Chunk(text="hello", metadata={"index": 0, "source": "notes.txt"})])
                doc = store.load_document(doc_id)
                self.assertIsNotNone(doc)
                self.assertEqual(doc["filename"], "notes.txt")
                results = store.query("hello", k=1)
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0]["metadata"]["doc_id"], doc_id)
                store.delete_document(doc_id)
                self.assertIsNone(store.load_document(doc_id))
            finally:
                store.close()

    def test_reopen_and_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc_id = "doc:2"
            store = KnowledgeStorage(tmp)
            store.upsert_document(doc_id, {"source": "p", "filename": "p.txt", "extension": ".txt", "checksum": "k2", "indexed_at": time.time()})
            store.add_chunks(doc_id, [Chunk(text="persistent query", metadata={"index": 0, "source": "p.txt"})])
            store.close()
            reopened = KnowledgeStorage(tmp)
            try:
                results = reopened.query("persistent", k=1)
                self.assertTrue(len(results) >= 1)
            finally:
                reopened.close()


class TestIndexer(unittest.TestCase):
    def test_index_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "notes.txt"
            p.write_text("knowledge is power", encoding="utf-8")
            store = KnowledgeStorage(Path(tmp) / "store")
            indexer = KnowledgeIndexer(store)
            doc_id = indexer.index_file(p)
            self.assertTrue(doc_id)
            try:
                results = store.query("knowledge", k=1)
                self.assertTrue(len(results) >= 1)
            finally:
                store.close()

    def test_duplicate_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "repeat.md"
            p.write_text("# hello world", encoding="utf-8")
            store = KnowledgeStorage(Path(tmp) / "store")
            indexer = KnowledgeIndexer(store)
            doc_id = indexer.index_file(p)
            self.assertTrue(doc_id)
            try:
                count_before = len(store.query("hello", k=10))
                indexer.index_file(p)
                count_after = len(store.query("hello", k=10))
                self.assertEqual(count_before, count_after)
            finally:
                store.close()

    def test_unsupported_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = KnowledgeStorage(Path(tmp) / "store")
            indexer = KnowledgeIndexer(store)
            unknown = Path(tmp) / "file.unknown"
            unknown.write_text("??", encoding="utf-8")
            self.assertIsNone(indexer.index_file(unknown))
            store.close()


class TestKnowledgeEngine(unittest.TestCase):
    def test_search_without_chroma(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = KnowledgeEngine(root_dir=Path(tmp) / "engine")
            self.assertEqual(engine.search("nothing"), [])
            engine.close()

    def test_enhance_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = KnowledgeEngine(root_dir=tmp)
            memory = FakeMemory()
            engine.enhance_memory(memory, "test query")
            self.assertEqual(len(memory.messages), 0)
            engine.storage.add_chunks("doc", [Chunk(text="Solar energy.", metadata={"index": 0, "source": "s.txt"})])
            engine.enhance_memory(memory, "energy")
            self.assertEqual(len(memory.messages), 1)
            self.assertEqual(memory.messages[0]["role"], "knowledge")
            engine.close()

    def test_enhance_planner(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = KnowledgeEngine(root_dir=tmp)
            state = {"context": [], "selected_tools": []}
            result = engine.enhance_planner(state, "plan science project")
            self.assertTrue(any("knowledge_search" in t for t in result["selected_tools"]))
            engine.close()

    def test_enhance_reflection(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = KnowledgeEngine(root_dir=tmp)
            engine.storage.add_chunks("doc", [Chunk(text="Protocol details.", metadata={"index": 0, "source": "s.txt"})])
            state = {"reflection": "fallback", "transcript": "protocol details", "answer": ""}
            engine.enhance_reflection(state)
            self.assertEqual(state.get("reflection"), "refine")
            engine.close()

    def test_enhance_tool_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = KnowledgeEngine(root_dir=tmp)
            registry = SimpleNamespace(tools=[], logger=SimpleNamespace(info=lambda *_: None))
            engine.enhance_tool_registry(registry)
            self.assertEqual(len(registry.tools), 1)
            self.assertEqual(registry.tools[0].name, "knowledge_search")
            engine.close()

    def test_register_and_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "docs"
            folder.mkdir()
            (folder / "a.txt").write_text("alpha", encoding="utf-8")
            engine = KnowledgeEngine(root_dir=tmp)
            engine.register_watch_folder(folder)
            # simulate stale snapshot
            engine._watch_folders[str(folder.resolve())] = 0.0
            refs = engine.refresh_if_changed()
            self.assertTrue(len(refs) >= 1)
            engine.close()

    def test_sqlite_reuse_after_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "notes.txt"
            p.write_text("hello hello hello", encoding="utf-8")
            store = KnowledgeStorage(Path(tmp) / "store")
            indexer = KnowledgeIndexer(store)
            indexer.index_file(p)
            store.close()
            reopened = KnowledgeStorage(Path(tmp) / "store")
            try:
                results = reopened.query("hello", k=5)
                self.assertTrue(len(results) >= 1)
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
