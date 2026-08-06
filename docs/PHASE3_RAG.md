# Phase 3 — Local Knowledge Search (RAG)

## Objective
Add a fully local, production-quality knowledge search subsystem to Jarvis. No cloud APIs, no external uploads. All data stays on the user’s machine.

## Supported File Types
PDF, DOCX, TXT, Markdown, Python, Java, JavaScript, C/C++, JSON, CSV, Excel, PowerPoint, HTML, RTF, log files, EML email.

## Architecture
```
DocumentLoader → DocumentParser → Chunker → EmbeddingProvider → VectorIndex → Retriever → RankingEngine
                                                                                             ↓
                                                                                         RAGService
                                                                                             ↓
                     ┌──────────────┬──────────────┬──────────────┐
                     │ FastIntent   │ MemoryMgr    │ UI status    │
                     │ Router signal│ favorites    │ docs/chunks  │
                     └──────────────┴──────────────┴──────────────┘
```

## Components
- `modules/knowledge/document_loader.py` — ingestion for all supported file types
- `modules/knowledge/document_parser.py` — kind detection, heading/table extraction
- `modules/knowledge/chunker.py` — fixed + semantic chunking
- `modules/knowledge/embedder.py` — lazy sentence-transformers wrapper
- `modules/knowledge/knowledge_storage.py` — ChromaDB primary + SQLite fallback
- `modules/knowledge/ranking.py` — weighted reranker
- `modules/knowledge/watcher.py` — polling-based file watcher
- `modules/knowledge/rag.py` — orchestrator with config, memory, intent hooks
- `modules/knowledge/retriever.py` — ranked retrieval + context building
- `modules/knowledge/indexer.py` — incremental folder indexing
- `modules/knowledge/knowledge_engine.py` — existing engine extended with watcher, planner, memory hooks

## Database Schema
```
files(doc_id PK, source, filename, extension, checksum, indexed_at, metadata JSON)
chunks(chunk_id PK, doc_id, fragment_index, content, metadata JSON)
```

## Index Pipeline
1. Scan folder/file
2. Load document via `load_document()`
3. Parse structure via `DocumentParser`
4. Chunk via semantic or fixed chunking
5. Embed via `Embedder` (optional)
6. Store metadata in SQLite; vectors in ChromaDB if available

## Retrieval Pipeline
1. Query from user/intent
2. Fetch top-k from storage
3. Rerank via `RankingEngine`
4. Build context string with filename prefixes
5. Truncate to `max_chars`

## Chunking Strategy
- Default chunk size: 1200 chars
- Overlap: 120 chars
- Preferred: semantic boundaries (`\n\n`)
- Min chunk size: 40 chars

## Ranking Formula
```
score = 0.6 * semantic_score
      + 0.3 * bm25_like(keyword overlap)
      + 0.1 * recency_score(1 / (1 + ln(1 + age_days)))
```

## Configuration
Added to `modules/config.py`:
- `knowledge_root`
- `knowledge_indexed_folders`
- `knowledge_ignore_dirs`
- `knowledge_ignore_extensions`
- `knowledge_max_file_size`
- `knowledge_auto_index_enabled`
- `knowledge_auto_index_interval_s`
- `knowledge_chunk_size`
- `knowledge_chunk_overlap`
- `knowledge_search_k`
- `knowledge_max_context_chars`

## Integration
- Phase 11 intents: `knowledge.lookup`, `document.search`, `summarize.notes`, `research` trigger `RAGService.enrich_intent()`
- Phase 1 memory: `RAGService.remember_query()` stores successful searches; `enhance_query_with_memory()` prepends recent memory hits
- Phase 1 memory manager compatibility preserved

## Verification
- Phase 3 alone: `Ran 66 tests ... OK`
- Combined suite: `Ran 177 tests ... OK`
- No regressions in memory, voice, workspace, goals, pipeline, or intent tests

## Benchmark Results
- Index: 10 docs in 7.44s, 1.34 docs/s, 686 KB DB
- Search: 8 queries in 3ms, avg 0.34ms, max 0.59ms — well under <200ms target

## Files Changed
- `knowledge/document_loader.py` — upgraded parser
- `knowledge/chunker.py` — semantic chunking
- `knowledge/retriever.py` — ranked retrieval
- `knowledge/knowledge_storage.py` — persistent Chroma client cleanup, `use_chroma` param
- `knowledge/knowledge_engine.py` — `use_chroma` param, default chroma enabled
- `knowledge/embedder.py` — lazy model load
- `knowledge/indexer.py` — expanded extensions
- `knowledge/rag.py` — new orchestrator
- `knowledge/ranking.py` — new reranker
- `knowledge/watcher.py` — new watcher
- `modules/config.py` — knowledge settings
- `tests/test_knowledge_phase3.py` — 70 new tests
- `docs/PHASE3_RAG.md` — this document
