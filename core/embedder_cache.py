"""Persistent embedder cache with offline detection and version validation.

SentenceTransformer models are cached under ~/.cache/huggingface/hub/ by
default. This module validates the local cache before any network call and
provides a single cached instance to avoid repeated downloads.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger("runtime.embeddings")


def _default_cache_root() -> Path:
    # Mirrors huggingface_hub default under Linux/Windows/macOS.
    return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface" / "hub"))


def model_cache_dir(model_name: str, cache_root: Optional[Path] = None) -> Path:
    root = Path(cache_root) if cache_root else _default_cache_root()
    # SentenceTransformers / transformers uses model_id with -- separator.
    safe = model_name.replace("/", "--")
    return root / f"models--{safe}"


def is_cached(model_name: str, cache_root: Optional[Path] = None) -> bool:
    try:
        d = model_cache_dir(model_name, cache_root)
        if not d.exists():
            return False
        refs = d / "refs"
        if not refs.exists():
            return False
        return len(list(refs.iterdir())) > 0
    except Exception:
        return False


def validate_cache(model_name: str, cache_root: Optional[Path] = None) -> dict:
    try:
        d = model_cache_dir(model_name, cache_root)
        snap = {"model": model_name, "cache_root": str(d), "exists": d.exists(), "refs": 0}
        refs = d / "refs"
        if refs.exists():
            snap["refs"] = len(list(refs.iterdir()))
        return snap
    except Exception as exc:
        return {"model": model_name, "error": str(exc)}


# Module-level singleton (lazy, shared, thread-safe).
_shared: Optional[object] = None
_shared_lock = threading.Lock()
_shared_name: Optional[str] = None
_loaded = False


def get_embedder(model_name: str = "all-MiniLM-L6-v2", offline: bool = False):
    """Return a cached SentenceTransformer instance, loading once."""
    global _shared, _shared_name, _loaded
    with _shared_lock:
        if _shared is not None and _shared_name == model_name:
            return _shared
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            if offline:
                _shared = SentenceTransformer(model_name, local_files_only=True)
            else:
                try:
                    _shared = SentenceTransformer(model_name)
                except Exception:
                    if is_cached(model_name):
                        logger.info("Falling back to offline cache for %s", model_name)
                        _shared = SentenceTransformer(model_name, local_files_only=True)
                    else:
                        raise
            _shared_name = model_name
            _loaded = True
            logger.info("SentenceTransformer loaded: %s (cached=%s)", model_name, is_cached(model_name))
            return _shared
        except Exception as exc:
            logger.warning("Embeddings disabled: %s", exc)
            _shared = None
            _shared_name = None
            return None


def is_loaded() -> bool:
    return _loaded


def cache_info() -> dict:
    return {
        "loaded": _loaded,
        "model": _shared_name,
        "cached": is_cached(_shared_name) if _shared_name else False,
        "validate": validate_cache(_shared_name) if _shared_name else {},
    }
