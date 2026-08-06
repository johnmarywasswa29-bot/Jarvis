"""Phase 3 benchmarks: indexing speed, search speed, memory usage, database size."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from knowledge.rag import RAGService


def bench_index(samples: list[Path]) -> dict[str, Any]:
    svc = RAGService(REPO / "tests" / "tmp_knowledge_bench")
    t0 = time.perf_counter()
    for p in samples:
        svc.index_file(p)
    total_s = time.perf_counter() - t0
    stats = svc.index_stats()
    store_path = svc.engine.storage.directory
    db_size = sum(f.stat().st_size for f in store_path.rglob("*") if f.is_file())
    svc.close()
    return {
        "documents_indexed": len(samples),
        "index_time_s": round(total_s, 3),
        "docs_per_s": round(len(samples) / max(total_s, 1e-6), 2),
        "db_size_bytes": db_size,
        "total_chunks": stats.get("chunks", 0),
    }


def bench_search(queries: list[str], k: int = 5) -> dict[str, Any]:
    svc = RAGService(REPO / "tests" / "tmp_knowledge_bench")
    t0 = time.perf_counter()
    latencies = []
    for q in queries:
        s = time.perf_counter()
        svc.search(q, k=k)
        latencies.append((time.perf_counter() - s) * 1000)
    total_s = time.perf_counter() - t0
    svc.close()
    return {
        "queries": len(queries),
        "total_s": round(total_s, 3),
        "avg_latency_ms": round(sum(latencies) / max(len(latencies), 1), 2),
        "max_latency_ms": round(max(latencies), 2),
        "min_latency_ms": round(min(latencies), 2),
    }


def main() -> int:
    samples = []
    texts = [
        "Jarvis is a local-first desktop assistant with memory, intent confidence, and knowledge search.",
        "Transformer models revolutionized natural language processing with attention mechanisms.",
        "Python source code includes modules for brain, intent, tools, memory, and knowledge.",
        "CSV files can be parsed into plain text tables with pipe separators.",
        "Excel workbooks are converted to sheet titles and cell rows.",
        "PowerPoint presentations extract text from each slide shape.",
        "RTF documents are stripped to plain text when rtf2txt is unavailable.",
        "HTML pages are parsed with BeautifulSoup into clean text.",
        "Log files contain timestamped events and error messages.",
        "Email EML files include subject, sender, recipient, and body.",
    ]
    for idx, text in enumerate(texts, start=1):
        p = REPO / "tests" / "tmp_knowledge_bench" / f"doc_{idx}.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        samples.append(p)
    queries = ["assistant", "transformer", "python", "tables", "email", "presentation", "logs", "local"]

    print("=== Phase 3 Benchmark ===")
    idx = bench_index(samples)
    print("\nIndex:")
    for k, v in idx.items():
        print(f"  {k}: {v}")
    sr = bench_search(queries)
    print("\nSearch:")
    for k, v in sr.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
