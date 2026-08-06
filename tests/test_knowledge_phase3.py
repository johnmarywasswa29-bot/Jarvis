"""Phase 3 tests: local knowledge search / RAG."""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[1]
os.chdir(REPO)

TMP_DIR = REPO / "tests" / "tmp_knowledge"
TMP_DIR.mkdir(parents=True, exist_ok=True)


def _clean_dir(path: Path) -> None:
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        return
    for child in path.glob("*"):
        if child.is_file():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            import shutil
            shutil.rmtree(child, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)


def _tmp() -> Path:
    return TMP_DIR / f"test_{id(unittest.TestCase())}"


from knowledge.document_loader import DocumentLoadError, load_document
from knowledge.document_parser import DocumentParser
from knowledge.chunker import Chunker, Chunk
from knowledge.embedder import Embedder
from knowledge.knowledge_storage import KnowledgeStorage
from knowledge.indexer import KnowledgeIndexer, SUPPORTED_EXTENSIONS
from knowledge.knowledge_engine import KnowledgeEngine
from knowledge.retriever import KnowledgeRetriever
from knowledge.ranking import RankingEngine
from knowledge.watcher import KnowledgeWatcher
from knowledge.rag import RAGService


class TestDocumentLoader(unittest.TestCase):
    def test_text_document(self):
        path = TMP_DIR / "hello.txt"
        path.write_text("Hello Jarvis.", encoding="utf-8")
        text, meta = load_document(path)
        self.assertEqual(text, "Hello Jarvis.")
        self.assertEqual(meta["filename"], "hello.txt")
        self.assertEqual(meta["extension"], ".txt")
        self.assertIn("created", meta)
        self.assertIn("modified", meta)

    def test_markdown_document(self):
        path = TMP_DIR / "doc.md"
        path.write_text("# Title\n\nHello **world**.", encoding="utf-8")
        text, meta = load_document(path)
        self.assertEqual(meta["extension"], ".md")
        self.assertIn("Hello", text)

    def test_html_document(self):
        path = TMP_DIR / "page.html"
        path.write_text("<html><body><p>Hello</p></body></html>", encoding="utf-8")
        text, meta = load_document(path)
        self.assertIn("Hello", text)
        self.assertEqual(meta["mime"], "text/html")

    def test_json_document(self):
        path = TMP_DIR / "data.json"
        path.write_text('{"a":1}', encoding="utf-8")
        _, meta = load_document(path)
        self.assertEqual(meta["mime"], "application/json")

    def test_csv_document(self):
        path = TMP_DIR / "data.csv"
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["a", "b"])
            writer.writerow([1, 2])
        text, meta = load_document(path)
        self.assertIn("a", text)
        self.assertIn("1", text)
        self.assertEqual(meta["mime"], "text/csv")

    def test_py_document(self):
        path = TMP_DIR / "main.py"
        path.write_text("print('hello')", encoding="utf-8")
        text, meta = load_document(path)
        self.assertIn("print", text)
        self.assertEqual(meta["mime"], "text/x-python")

    def test_source_code_documents(self):
        for ext, mime in [(".js", "text/javascript"), (".java", "text/x-java"), (".c", "text/x-c"), (".cpp", "text/x-c++"), (".h", "text/x-c"), (".log", "text/plain")]:
            path = TMP_DIR / f"file{ext}"
            path.write_text("code here", encoding="utf-8")
            _, meta = load_document(path)
            self.assertEqual(meta["mime"], mime)
            self.assertEqual(meta["extension"], ext)

    def test_oversized_document(self):
        path = TMP_DIR / "big.txt"
        path.write_text("A" * 200, encoding="utf-8")
        with self.assertRaises(DocumentLoadError):
            load_document(path, max_size=100)

    def test_missing_document(self):
        with self.assertRaises(DocumentLoadError):
            load_document("missing.txt")

    def test_unsupported_reads_as_text(self):
        path = TMP_DIR / "file.bin"
        path.write_text("binary-ish", encoding="utf-8")
        text, meta = load_document(path)
        self.assertIn("binary-ish", text)
        self.assertEqual(meta["mime"], "text/plain")

    def test_metadata_has_title_and_language(self):
        path = TMP_DIR / "note.txt"
        path.write_text("Hello world", encoding="utf-8")
        _, meta = load_document(path)
        self.assertIn("title", meta)
        self.assertIn("language", meta)


class TestDocumentParser(unittest.TestCase):
    def test_guess_kind_json(self):
        parser = DocumentParser()
        self.assertEqual(parser.parse('{"a":1}', kind_hint=None)["kind"], "json")

    def test_guess_kind_code(self):
        parser = DocumentParser()
        result = parser.parse("import os\nprint('hi')", kind_hint=None)
        self.assertEqual(result["kind"], "code")

    def test_guess_kind_markup(self):
        parser = DocumentParser()
        result = parser.parse("# Heading\n\nSome text", kind_hint=None)
        self.assertEqual(result["kind"], "markup")

    def test_guess_kind_plain(self):
        parser = DocumentParser()
        result = parser.parse("Just plain text here", kind_hint=None)
        self.assertEqual(result["kind"], "plain")

    def test_headings_extracted(self):
        parser = DocumentParser()
        result = parser.parse("# Title\n\n## Section\n\nBody", kind_hint="markup")
        self.assertEqual(len(result["heading_blocks"]), 2)

    def test_paragraphs_split(self):
        parser = DocumentParser()
        result = parser.parse("Para1\n\nPara2\n\nPara3", kind_hint="plain")
        self.assertEqual(len(result["paragraphs"]), 3)


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

    def test_semantic_preferred(self):
        chunker = Chunker(chunk_size=40, overlap=5, prefer_semantic=True)
        text = "Para1\n\nPara2\n\nPara3\n\n" + "x" * 50 + "\n\nPara4"
        chunks = chunker.chunk(text)
        self.assertGreaterEqual(len(chunks), 1)

    def test_fixed_when_no_boundaries(self):
        chunker = Chunker(chunk_size=20, overlap=2, prefer_semantic=True)
        text = "A B C D E F G H I J"
        chunks = chunker.chunk(text)
        self.assertGreaterEqual(len(chunks), 1)

    def test_small_text(self):
        chunker = Chunker(chunk_size=1000)
        text = "Small text"
        chunks = chunker.chunk(text)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, "Small text")

    def test_min_chars_filter(self):
        chunker = Chunker(chunk_size=5, overlap=1, min_chars=4)
        text = "AB CD EF GH"
        chunks = chunker.chunk(text)
        self.assertTrue(len(chunks) >= 1)
        for c in chunks:
            self.assertGreaterEqual(len(c.text), 4)

    def test_overlap_reset(self):
        chunker = Chunker(chunk_size=50, overlap=60)
        self.assertEqual(chunker.overlap, 10)

    def test_semantic_boundaries(self):
        chunker = Chunker(chunk_size=30, overlap=2, prefer_semantic=True)
        text = "First paragraph here.\n\nSecond paragraph here.\n\nThird paragraph here."
        chunks = chunker.chunk(text)
        self.assertGreaterEqual(len(chunks), 1)
        for c in chunks:
            self.assertIn("paragraph", c.text)


class TestEmbedder(unittest.TestCase):
    def test_is_available(self):
        emb = Embedder()
        self.assertIn(emb.is_available(), {True, False})

    def test_embed_unavailable(self):
        emb = Embedder()
        result = emb.embed(["hello"])
        self.assertTrue(result is None or isinstance(result, list))

    def test_embed_one_returns_list_or_none(self):
        emb = Embedder()
        result = emb.embed_one("hello")
        self.assertTrue(result is None or isinstance(result, list))


class TestKnowledgeStorage(unittest.TestCase):
    def tearDown(self):
        _clean_dir(TMP_DIR)

    def test_sqlite_fallback(self):
        path = TMP_DIR / "sqlite"
        _clean_dir(path)
        store = KnowledgeStorage(path, use_chroma=False)
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
        path = TMP_DIR / "reopen"
        _clean_dir(path)
        doc_id = "doc:2"
        store = KnowledgeStorage(path, use_chroma=False)
        store.upsert_document(doc_id, {"source": "p", "filename": "p.txt", "extension": ".txt", "checksum": "k2", "indexed_at": time.time()})
        store.add_chunks(doc_id, [Chunk(text="persistent query", metadata={"index": 0, "source": "p.txt"})])
        store.close()
        reopened = KnowledgeStorage(path, use_chroma=False)
        try:
            results = reopened.query("persistent", k=1)
            self.assertTrue(len(results) >= 1)
        finally:
            reopened.close()

    def test_upsert_overwrite(self):
        path = TMP_DIR / "upsert"
        _clean_dir(path)
        store = KnowledgeStorage(path, use_chroma=False)
        store.upsert_document("doc", {"source": "a", "filename": "a.txt", "extension": ".txt", "checksum": "c1", "indexed_at": 1.0})
        store.upsert_document("doc", {"source": "b", "filename": "b.txt", "extension": ".txt", "checksum": "c2", "indexed_at": 2.0})
        doc = store.load_document("doc")
        self.assertEqual(doc["filename"], "b.txt")
        store.close()

    def test_query_empty(self):
        path = TMP_DIR / "empty"
        _clean_dir(path)
        store = KnowledgeStorage(path, use_chroma=False)
        self.assertEqual(store.query("", k=1), [])
        store.close()

    def test_query_returns_chunk_id(self):
        path = TMP_DIR / "chunk_id"
        _clean_dir(path)
        store = KnowledgeStorage(path, use_chroma=False)
        store.add_chunks("doc", [Chunk(text="alpha", metadata={"index": 0, "source": "s.txt"})])
        results = store.query("alpha", k=1)
        self.assertIn("chunk_id", results[0])
        store.close()


class TestIndexer(unittest.TestCase):
    def test_index_file(self):
        path = TMP_DIR / "idx"
        _clean_dir(path)
        p = path / "notes.txt"
        p.write_text("knowledge is power", encoding="utf-8")
        store = KnowledgeStorage(path / "store", use_chroma=False)
        indexer = KnowledgeIndexer(store)
        doc_id = indexer.index_file(p)
        self.assertTrue(doc_id)
        try:
            results = store.query("knowledge", k=1)
            self.assertTrue(len(results) >= 1)
        finally:
            store.close()

    def test_index_folder(self):
        path = TMP_DIR / "folder"
        _clean_dir(path)
        folder = path / "docs"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "a.txt").write_text("alpha", encoding="utf-8")
        (folder / "b.txt").write_text("beta", encoding="utf-8")
        store = KnowledgeStorage(path / "store", use_chroma=False)
        indexer = KnowledgeIndexer(store)
        ids = indexer.index_folder(folder)
        self.assertEqual(len(ids), 2)
        store.close()

    def test_duplicate_detection(self):
        path = TMP_DIR / "dup"
        _clean_dir(path)
        p = path / "repeat.md"
        p.write_text("# hello world", encoding="utf-8")
        store = KnowledgeStorage(path / "store", use_chroma=False)
        indexer = KnowledgeIndexer(store)
        indexer.index_file(p)
        try:
            count_before = len(store.query("hello", k=10))
            indexer.index_file(p)
            count_after = len(store.query("hello", k=10))
            self.assertEqual(count_before, count_after)
        finally:
            store.close()

    def test_unsupported_skipped(self):
        path = TMP_DIR / "unsupported"
        _clean_dir(path)
        store = KnowledgeStorage(path / "store", use_chroma=False)
        indexer = KnowledgeIndexer(store)
        unknown = path / "file.unknown"
        unknown.write_text("??", encoding="utf-8")
        self.assertIsNone(indexer.index_file(unknown))
        store.close()

    def test_supported_extensions_include_required(self):
        required = {".pdf", ".docx", ".md", ".py", ".json", ".csv", ".java", ".js", ".c", ".cpp", ".h", ".log", ".rtf", ".pptx", ".xlsx", ".eml"}
        self.assertTrue(required.issubset(SUPPORTED_EXTENSIONS))

    def test_delete_updates_storage(self):
        path = TMP_DIR / "del"
        _clean_dir(path)
        p = path / "del.txt"
        p.write_text("delete me", encoding="utf-8")
        store = KnowledgeStorage(path / "store", use_chroma=False)
        indexer = KnowledgeIndexer(store)
        doc_id = indexer.index_file(p)
        self.assertTrue(doc_id)
        indexer.storage.delete_document(doc_id)
        self.assertIsNone(store.load_document(doc_id))
        store.close()


class TestKnowledgeEngine(unittest.TestCase):
    def tearDown(self):
        _clean_dir(TMP_DIR)

    def test_search_without_chroma(self):
        engine = KnowledgeEngine(root_dir=TMP_DIR / "engine", use_chroma=False)
        self.assertEqual(engine.search("nothing"), [])
        engine.close()

    def test_enhance_memory(self):
        engine = KnowledgeEngine(root_dir=TMP_DIR / "engine", use_chroma=False)
        memory = SimpleNamespace(messages=[])
        def add_message(role, content, metadata=None):
            memory.messages.append({"role": role, "content": content, "metadata": metadata or {}})
        memory.add_message = add_message
        engine.enhance_memory(memory, "test query")
        self.assertEqual(len(memory.messages), 0)
        engine.storage.add_chunks("doc", [Chunk(text="Solar energy.", metadata={"index": 0, "source": "s.txt"})])
        engine.enhance_memory(memory, "energy")
        self.assertEqual(len(memory.messages), 1)
        self.assertEqual(memory.messages[0]["role"], "knowledge")
        engine.close()

    def test_enhance_planner(self):
        engine = KnowledgeEngine(root_dir=TMP_DIR / "engine", use_chroma=False)
        state = {"context": [], "selected_tools": []}
        result = engine.enhance_planner(state, "plan science project")
        self.assertTrue(any("knowledge_search" in t for t in result["selected_tools"]))
        engine.close()

    def test_enhance_reflection(self):
        engine = KnowledgeEngine(root_dir=TMP_DIR / "engine", use_chroma=False)
        engine.storage.add_chunks("doc", [Chunk(text="Protocol details.", metadata={"index": 0, "source": "s.txt"})])
        state = {"reflection": "fallback", "transcript": "protocol details", "answer": ""}
        engine.enhance_reflection(state)
        self.assertEqual(state.get("reflection"), "refine")
        engine.close()

    def test_enhance_tool_registry(self):
        engine = KnowledgeEngine(root_dir=TMP_DIR / "engine", use_chroma=False)
        registry = SimpleNamespace(tools=[], logger=SimpleNamespace(info=lambda *_: None))
        engine.enhance_tool_registry(registry)
        self.assertEqual(len(registry.tools), 1)
        self.assertEqual(registry.tools[0].name, "knowledge_search")
        engine.close()

    def test_register_and_refresh(self):
        folder = TMP_DIR / "docs"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "a.txt").write_text("alpha", encoding="utf-8")
        engine = KnowledgeEngine(root_dir=TMP_DIR / "engine", use_chroma=False)
        engine.register_watch_folder(folder)
        engine._watch_folders[str(folder.resolve())] = 0.0
        refs = engine.refresh_if_changed()
        self.assertTrue(len(refs) >= 1)
        engine.close()

    def test_sqlite_reuse_after_close(self):
        p = TMP_DIR / "notes.txt"
        p.write_text("hello hello hello", encoding="utf-8")
        path = TMP_DIR / "reuse"
        _clean_dir(path)
        store = KnowledgeStorage(path, use_chroma=False)
        indexer = KnowledgeIndexer(store)
        indexer.index_file(p)
        store.close()
        reopened = KnowledgeStorage(path, use_chroma=False)
        try:
            results = reopened.query("hello", k=5)
            self.assertTrue(len(results) >= 1)
        finally:
            reopened.close()


class TestRetriever(unittest.TestCase):
    def test_search_returns_results(self):
        engine = KnowledgeEngine(root_dir=TMP_DIR / "ret", use_chroma=False)
        engine.storage.add_chunks("doc", [Chunk(text="hello world", metadata={"index": 0, "source": "s.txt"})])
        results = engine.retriever.search("hello", k=1)
        self.assertTrue(len(results) >= 1)
        engine.close()

    def test_context_truncation(self):
        engine = KnowledgeEngine(root_dir=TMP_DIR / "ret", use_chroma=False)
        engine.storage.add_chunks("doc", [Chunk(text="A" * 10000, metadata={"index": 0, "source": "big.txt"})])
        ctx = engine.retriever.get_context("a", max_chars=100, k=1)
        self.assertLessEqual(len(ctx), 150)
        engine.close()

    def test_ranking_engine(self):
        ranking = RankingEngine()
        results = [
            {"content": "alpha", "metadata": {"score": 0.9, "indexed_at": 1.0}},
            {"content": "beta", "metadata": {"score": 0.1, "indexed_at": 1.0}},
        ]
        ranked = ranking.rank("alpha", results)
        self.assertEqual(ranked[0]["content"], "alpha")

    def test_retriever_with_ranking(self):
        engine = KnowledgeEngine(root_dir=TMP_DIR / "ret", use_chroma=False)
        engine.ranking_engine = RankingEngine()
        engine.retriever.ranking_engine = engine.ranking_engine
        engine.storage.add_chunks("doc", [Chunk(text="alpha", metadata={"index": 0, "source": "s.txt"})])
        results = engine.retriever.search("alpha", k=1)
        self.assertTrue(len(results) >= 1)
        engine.close()


class TestWatcher(unittest.TestCase):
    def test_watcher_add_and_scan(self):
        folder = TMP_DIR / "scan"
        _clean_dir(folder)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "a.txt").write_text("alpha", encoding="utf-8")
        (folder / "b.txt").write_text("beta", encoding="utf-8")
        calls = []

        watcher = KnowledgeWatcher(poll_interval=0.1)
        watcher.add_watch(folder, lambda path: calls.append(path))
        time.sleep(0.3)
        watcher.stop()
        self.assertTrue(len(calls) >= 1)
        for expected in [(folder / "a.txt").resolve(), (folder / "b.txt").resolve()]:
            self.assertTrue(any(Path(p).resolve() == expected for p in calls))

    def test_watcher_ignores_dirs(self):
        folder = TMP_DIR / "ignore"
        _clean_dir(folder)
        folder.mkdir(parents=True, exist_ok=True)
        hidden = folder / ".git"
        hidden.mkdir()
        (hidden / "ignore.txt").write_text("secret", encoding="utf-8")
        calls = []
        watcher = KnowledgeWatcher(poll_interval=0.1)
        watcher.add_watch(folder, lambda path: calls.append(path))
        time.sleep(0.3)
        watcher.stop()
        for path in calls:
            self.assertNotIn(".git", path)

    def test_watcher_extension_filter(self):
        folder = TMP_DIR / "ext"
        _clean_dir(folder)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "a.txt").write_text("alpha", encoding="utf-8")
        (folder / "b.bin").write_text("binary", encoding="utf-8")
        calls = []
        watcher = KnowledgeWatcher(poll_interval=0.1)
        watcher.add_watch(folder, lambda path: calls.append(path), extensions={".txt"})
        time.sleep(0.3)
        watcher.stop()
        for path in calls:
            self.assertEqual(Path(path).suffix.lower(), ".txt")

    def test_watcher_max_size(self):
        folder = TMP_DIR / "size"
        _clean_dir(folder)
        folder.mkdir(parents=True, exist_ok=True)
        big = folder / "big.txt"
        big.write_text("A" * 200, encoding="utf-8")
        calls = []
        watcher = KnowledgeWatcher(poll_interval=0.1)
        watcher.add_watch(folder, lambda path: calls.append(path), max_size=100)
        time.sleep(0.3)
        watcher.stop()
        for path in calls:
            self.assertNotEqual(Path(path).name, "big.txt")


class TestRAGService(unittest.TestCase):
    def _test_dir(self):
        return TMP_DIR / self._testMethodName

    def setUp(self):
        _clean_dir(self._test_dir())

    def tearDown(self):
        _clean_dir(self._test_dir())

    def test_index_file_and_search(self):
        svc = RAGService(TMP_DIR / "rag")
        p = TMP_DIR / "rag" / "notes.txt"
        p.write_text("transformer notes", encoding="utf-8")
        doc_id = svc.index_file(p)
        self.assertTrue(doc_id)
        results = svc.search("transformer", k=1)
        self.assertTrue(len(results) >= 1)
        self.assertTrue(any("transformer" in r.get("content", "") for r in results))
        svc.close()

    def test_stats(self):
        svc = RAGService(self._test_dir())
        stats = svc.index_stats()
        self.assertIn("documents", stats)
        self.assertIn("chunks", stats)
        self.assertIn("watcher_running", stats)
        svc.close()

    def test_forget(self):
        svc = RAGService(self._test_dir())
        p = self._test_dir() / "doc.txt"
        p.write_text("forget me", encoding="utf-8")
        doc_id = svc.index_file(p)
        self.assertTrue(doc_id)
        svc.forget(doc_id)
        self.assertIsNone(svc.engine.storage.load_document(doc_id))
        svc.close()

    def test_enhance_intent_knowledge(self):
        svc = RAGService(self._test_dir())
        p = self._test_dir() / "notes.txt"
        p.write_text("quantum physics notes", encoding="utf-8")
        svc.index_file(p)
        result = svc.enrich_intent({"intent": "knowledge.lookup", "query": "physics"})
        self.assertIn("knowledge_context", result)
        svc.close()

    def test_remember_query(self):
        svc = RAGService(self._test_dir())
        svc.memory = None
        svc.remember_query("test")
        svc.close()

    def test_open_document(self):
        svc = RAGService(self._test_dir())
        p = self._test_dir() / "open.txt"
        p.write_text("open", encoding="utf-8")
        doc_id = svc.index_file(p)
        self.assertTrue(doc_id)
        source = svc.open_document(doc_id)
        self.assertIsNotNone(source)
        self.assertTrue(Path(source).exists())
        svc.close()

    def test_watcher_start_stop(self):
        svc = RAGService(self._test_dir())
        svc.start_watcher([str(REPO / "knowledge")])
        self.assertTrue(svc._started)
        svc.stop_watcher()
        self.assertFalse(svc._started)
        svc.close()

    def test_apply_config_defaults(self):
        svc = RAGService(self._test_dir())
        from modules.config import JarvisConfig
        cfg = JarvisConfig(project_root=REPO)
        cfg.knowledge_root = str(TMP_DIR / "rag2")
        cfg.knowledge_indexed_folders = [str(REPO / "knowledge")]
        cfg.knowledge_auto_index_enabled = True
        cfg.knowledge_auto_index_interval_s = 0.1
        cfg.knowledge_chunk_size = 500
        cfg.knowledge_chunk_overlap = 50
        svc.apply_config(cfg)
        stats = svc.index_stats()
        self.assertIn("documents", stats)
        svc.close()


class TestPhase3Integration(unittest.TestCase):
    def test_intents_exist(self):
        from modules.intent.analyzer import IntentAnalyzer
        a = IntentAnalyzer()
        sample = "Where is my physics assignment?"
        r = a.analyze(sample)
        self.assertIn(r.intent, {"document.search", "knowledge.lookup", "llm.chat"})

    def test_enhance_memory_with_knowledge(self):
        engine = KnowledgeEngine(root_dir=TMP_DIR / "integration", use_chroma=False)
        memory = SimpleNamespace(messages=[])
        def add_message(role, content, metadata=None):
            memory.messages.append({"role": role, "content": content, "metadata": metadata or {}})
        memory.add_message = add_message
        engine.enhance_memory(memory, "test")
        engine.storage.add_chunks("doc", [Chunk(text="Solar energy.", metadata={"index": 0, "source": "s.txt"})])
        engine.enhance_memory(memory, "energy")
        self.assertEqual(len(memory.messages), 1)
        engine.close()

    def test_ragservice_memory_hook(self):
        svc = RAGService(TMP_DIR / "integration")
        svc.memory = SimpleNamespace(messages=[])
        def add_message(role, content, metadata=None):
            svc.memory.messages.append({"role": role, "content": content, "metadata": metadata or {}})
        svc.memory.add_message = add_message
        svc.remember_query("transformer", success=True)
        self.assertEqual(len(svc.memory.messages), 1)
        svc.close()


if __name__ == "__main__":
    unittest.main()
