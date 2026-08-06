"""Chunker splits documents into retrievable chunks with optional semantic chunking."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Chunk:
    text: str
    metadata: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        source = self.metadata.get("filename") or self.metadata.get("source") or ""
        return f"{source}:{self.metadata.get('index', 0)}"


class Chunker:
    def __init__(
        self,
        *,
        chunk_size: int = 1200,
        overlap: int = 120,
        min_chars: int = 40,
        prefer_semantic: bool = True,
    ) -> None:
        if overlap >= chunk_size:
            overlap = max(0, chunk_size // 5)
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.min_chars = min_chars
        self.prefer_semantic = prefer_semantic

    def chunk(self, text: str, metadata: Optional[dict] = None) -> list[Chunk]:
        metadata = metadata or {}
        text = text.strip()
        if not text:
            return []
        if len(text) <= self.chunk_size:
            return [Chunk(text=text, metadata={**metadata, "index": 0, "start": 0, "end": len(text)})]

        if self.prefer_semantic:
            chunks = self._semantic_chunks(text, metadata)
        else:
            chunks = self._fixed_chunks(text, metadata)
        if not chunks and text.strip():
            chunks = [Chunk(text=text.strip(), metadata={**metadata, "index": 0, "start": 0, "end": len(text)})]
        return chunks

    def _fixed_chunks(self, text: str, metadata: dict) -> list[Chunk]:
        chunks: list[Chunk] = []
        index = 0
        start = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            fragment = text[start:end].strip()
            if len(fragment) >= self.min_chars:
                chunks.append(Chunk(text=fragment, metadata={**metadata, "index": index, "start": start, "end": end}))
                index += 1
            start = max(end, start + self.min_chars)
        return chunks

    def _semantic_chunks(self, text: str, metadata: dict) -> list[Chunk]:
        boundaries = self._find_boundaries(text)
        chunks: list[Chunk] = []
        start = 0
        idx = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            if end < len(text) and boundaries:
                best = max((b for b in boundaries if start + self.min_chars <= b <= end), default=None)
                if best is not None:
                    end = best
            fragment = text[start:end].strip()
            if len(fragment) >= self.min_chars:
                chunks.append(Chunk(text=fragment, metadata={**metadata, "index": idx, "start": start, "end": end}))
                idx += 1
            start = max(end, start + self.min_chars)
        return chunks

    def _find_boundaries(self, text: str) -> list[int]:
        b: list[int] = []
        for m in re.finditer(r"\n\s*\n", text):
            b.append(m.start())
        return b
