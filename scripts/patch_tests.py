from pathlib import Path
p = Path('tests/test_knowledge_phase3.py')
text = p.read_text(encoding='utf-8')
old = '''class TestRAGService(unittest.TestCase):
    def tearDown(self):
        _clean_dir(TMP_DIR)

    def test_index_file_and_search(self):
        svc = RAGService(TMP_DIR / "rag")
        p = TMP_DIR / "notes.txt"
        p.write_text("transformer notes", encoding="utf-8")
        doc_id = svc.index_file(p)
        self.assertTrue(doc_id)
        results = svc.search("transformer", k=1)
        self.assertTrue(len(results) >= 1)
        self.assertTrue(any("transformer" in r.get("content", "") for r in results))
        svc.close()

    def test_stats(self):
        svc = RAGService(TMP_DIR / "rag")
        stats = svc.index_stats()
        self.assertIn("documents", stats)
        self.assertIn("chunks", stats)
        self.assertIn("watcher_running", stats)
        svc.close()

    def test_forget(self):
        svc = RAGService(TMP_DIR / "rag")
        p = TMP_DIR / "doc.txt"
        p.write_text("forget me", encoding="utf-8")
        doc_id = svc.index_file(p)
        self.assertTrue(doc_id)
        svc.forget(doc_id)
        self.assertIsNone(svc.engine.storage.load_document(doc_id))
        svc.close()

    def test_enhance_intent_knowledge(self):
        svc = RAGService(TMP_DIR / "rag")
        p = TMP_DIR / "notes.txt"
        p.write_text("quantum physics notes", encoding="utf-8")
        svc.index_file(p)
        result = svc.enrich_intent({"intent": "knowledge.lookup", "query": "physics"})
        self.assertIn("knowledge_context", result)
        svc.close()

    def test_remember_query(self):
        svc = RAGService(TMP_DIR / "rag")
        svc.memory = None
        svc.remember_query("test")
        svc.close()

    def test_open_document(self):
        svc = RAGService(TMP_DIR / "rag")
        p = TMP_DIR / "open.txt"
        p.write_text("open", encoding="utf-8")
        doc_id = svc.index_file(p)
        self.assertTrue(doc_id)
        source = svc.open_document(doc_id)
        self.assertIsNotNone(source)
        self.assertTrue(Path(source).exists())
        svc.close()

    def test_watcher_start_stop(self):
        svc = RAGService(TMP_DIR / "rag")
        svc.start_watcher([str(REPO / "knowledge")])
        self.assertTrue(svc._started)
        svc.stop_watcher()
        self.assertFalse(svc._started)
        svc.close()

    def test_apply_config_defaults(self):
        svc = RAGService(TMP_DIR / "rag")
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
        svc.close()'''
new = '''class TestRAGService(unittest.TestCase):
    def test_index_file_and_search(self):
        svc = RAGService(self._test_dir())
        p = self._test_dir() / "notes.txt"
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
        cfg.knowledge_root = str(self._test_dir() / "rag2")
        cfg.knowledge_indexed_folders = [str(REPO / "knowledge")]
        cfg.knowledge_auto_index_enabled = True
        cfg.knowledge_auto_index_interval_s = 0.1
        cfg.knowledge_chunk_size = 500
        cfg.knowledge_chunk_overlap = 50
        svc.apply_config(cfg)
        stats = svc.index_stats()
        self.assertIn("documents", stats)
        svc.close()'''
text = text.replace(old, new)
old = '''class TestPhase3Integration(unittest.TestCase):
    def test_intents_exist(self):
        from modules.intent.analyzer import IntentAnalyzer
        a = IntentAnalyzer()
        sample = "Where is my physics assignment?"
        r = a.analyze(sample)
        self.assertIn(r.intent, {"document.search", "knowledge.lookup", "llm.chat"})

    def test_enhance_memory_with_knowledge(self):
        engine = KnowledgeEngine(root_dir=TMP_DIR / "integration")
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
        svc.close()'''
new = '''class TestPhase3Integration(unittest.TestCase):
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
        svc.close()'''
text = text.replace(old, new)
old = '''class TestRAGService(unittest.TestCase):
    def tearDown(self):
        _clean_dir(TMP_DIR)

    def test_index_file_and_search(self):
        svc = RAGService(TMP_DIR / "rag")'''
new = '''class TestRAGService(unittest.TestCase):
    def _test_dir(self):
        return TMP_DIR / self._testMethodName

    def setUp(self):
        _clean_dir(self._test_dir())

    def tearDown(self):
        _clean_dir(self._test_dir())

    def test_index_file_and_search(self):
        svc = RAGService(self._test_dir())'''
text = text.replace(old, new)
old = '''    def test_stats(self):
        svc = RAGService(TMP_DIR / "rag")'''
new = '''    def test_stats(self):
        svc = RAGService(self._test_dir())'''
text = text.replace(old, new)
old = '''    def test_forget(self):
        svc = RAGService(TMP_DIR / "rag")
        p = TMP_DIR / "rag" / "doc.txt"'''
new = '''    def test_forget(self):
        svc = RAGService(self._test_dir())
        p = self._test_dir() / "doc.txt"'''
text = text.replace(old, new)
old = '''    def test_enhance_intent_knowledge(self):
        svc = RAGService(TMP_DIR / "rag")
        p = TMP_DIR / "rag" / "notes.txt"'''
new = '''    def test_enhance_intent_knowledge(self):
        svc = RAGService(self._test_dir())
        p = self._test_dir() / "notes.txt"'''
text = text.replace(old, new)
old = '''    def test_remember_query(self):
        svc = RAGService(TMP_DIR / "rag")'''
new = '''    def test_remember_query(self):
        svc = RAGService(self._test_dir())'''
text = text.replace(old, new)
old = '''    def test_open_document(self):
        svc = RAGService(TMP_DIR / "rag")
        p = TMP_DIR / "rag" / "open.txt"'''
new = '''    def test_open_document(self):
        svc = RAGService(self._test_dir())
        p = self._test_dir() / "open.txt"'''
text = text.replace(old, new)
old = '''    def test_watcher_start_stop(self):
        svc = RAGService(TMP_DIR / "rag")'''
new = '''    def test_watcher_start_stop(self):
        svc = RAGService(self._test_dir())'''
text = text.replace(old, new)
old = '''    def test_apply_config_defaults(self):
        svc = RAGService(TMP_DIR / "rag")'''
new = '''    def test_apply_config_defaults(self):
        svc = RAGService(self._test_dir())'''
text = text.replace(old, new)
p.write_text(text, encoding='utf-8')
print('patched')
