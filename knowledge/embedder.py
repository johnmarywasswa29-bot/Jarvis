"""Embedder abstraction with lazy local sentence-transformers fallback."""
from __future__ import annotations

from typing import Iterable, Optional


class Embedder:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self.model_name = model_name
        self._model = None
        self._load_attempted = False

    def _ensure_model(self) -> None:
        if self._model is not None or self._load_attempted:
            return
        self._load_attempted = True
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            self._model = SentenceTransformer(self.model_name)
        except Exception:
            self._model = None

    def is_available(self) -> bool:
        return self._model is not None

    def embed(self, texts: list[str]) -> Optional[list[list[float]]]:
        self._ensure_model()
        if self._model is None:
            return None
        try:
            vectors = self._model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
            return vectors.tolist()
        except Exception:
            return None

    def embed_one(self, text: str) -> Optional[list[float]]:
        vectors = self.embed([text])
        return vectors[0] if vectors else None
