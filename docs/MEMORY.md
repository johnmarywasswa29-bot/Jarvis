# Phase 1 — Intelligent Long-Term Memory

## Overview
Adds three distinct memory types with scoring, retrieval, consolidation, and migration.

Memory types:
- episodic: conversations, completed tasks, user actions, significant events
- semantic: learned facts, user preferences, frequently used applications, stable knowledge
- procedural: workflows, automation sequences, preferred task methods

## Memory Record Fields
- id: UUID
- memory_type: episodic | semantic | procedural
- content: text
- embedding: optional float32 vector bytes
- importance: 0–1
- confidence: 0–1
- access_count: int
- last_accessed: unix timestamp
- created_at: unix timestamp
- source: user | migration | system
- tags: JSON list
- related_memories: JSON list of memory ids
- decay_score: 0–1

## Database Schema
Tables:
- memories
- memory_events
- memory_consolidation_log

Indexes:
- idx_memory_type
- idx_created_at
- idx_last_accessed
- idx_importance
- idx_decay
- idx_memory_events_mid

## MemoryManager API
- add_memory(content, memory_type, importance, confidence, source, tags, related_memories, deduplicate)
- get_memory(memory_id)
- update_memory(memory_id, **kwargs)
- delete_memory(memory_id)
- search(query, types, limit, weights) -> hybrid ranked results
- get_recent(memory_type, limit)
- get_important(memory_type, limit)
- decay_pass(now) -> {promoted, decayed, merged}
- consolidate() -> {promoted, decayed, merged}
- migrate_from_v2(v2_db_path) -> count
- shutdown()

## Backward Compatibility
JarvisMemoryV2 is preserved unchanged.
Migration helper provided via MemoryManager.migrate_from_v2(path).

## Retrieval Strategy
Hybrid ranking:
- recency: 25%
- importance: 45%
- confidence: 30%
- semantic similarity bonus: 0.15 when embeddings available

Fallback:
- when embeddings unavailable, search still works on metadata/LIKE fallback

## Decay Policy
- decay_score decreases with age and low access count
- importance promoted when decay_score drops but importance >= 0.7
- memories deleted when decay_score < 0.1 AND importance < 0.3 AND confidence < 0.5

## Consolidation Policy
- duplicate memories merged by exact content match within same type/tag key
- oldest record kept; duplicates deleted
- consolidation log maintained

## Verification
- ad-hoc verification passed for add/get, dedup, search, recent/important, decay, consolidate, events, migration, v2 compat
- focused unittest subset still passes: Ran 33 tests ... OK
- temp verification script cleaned up

## Performance Notes
- memory_v3.sqlite stored in configured memory_path()
- sentence-transformers model loaded once on first MemoryManager init
- embedding write is async to main transaction to avoid blocking
- indexed fields support fast retrieval at scale

## Migration
Existing v2 messages stay in memory/jarvis.sqlite.
Run manager.migrate_from_v2(config.memory_path() / "jarvis.sqlite") once.
v2 system remains available for compatibility.
