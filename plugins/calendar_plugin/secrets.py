"""Calendar plugin secrets storage abstraction.

Provides a small backend-agnostic interface for storing and retrieving
OAuth tokens and client secrets. The default backend uses Windows DPAPI
(via pywin32) so secrets are encrypted at rest and tied to the current
user/machine. On unsupported platforms or when pywin32 is unavailable,
a no-op plaintext fallback is used so the plugin remains importable.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class SecretsStore:
    """Minimal secrets backend interface."""

    def set_secret(self, key: str, value: str) -> None:
        raise NotImplementedError

    def get_secret(self, key: str) -> Optional[str]:
        raise NotImplementedError

    def delete_secret(self, key: str) -> None:
        raise NotImplementedError

    def has_secret(self, key: str) -> bool:
        raise NotImplementedError


class _PlaintextFallbackStore(SecretsStore):
    """Fallback for unsupported environments.

    This keeps plaintext behavior for importability, but callers should
    prefer a protected backend when available.
    """

    def __init__(self, base_path: str) -> None:
        self._base_path = base_path
        os.makedirs(self._base_path, exist_ok=True)

    def _path(self, key: str) -> str:
        safe = base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii").rstrip("=")
        return os.path.join(self._base_path, f"{safe}.json")

    def set_secret(self, key: str, value: str) -> None:
        path = self._path(key)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"value": value}, fh)

    def get_secret(self, key: str) -> Optional[str]:
        path = self._path(key)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data.get("value")
        except Exception:
            return None

    def delete_secret(self, key: str) -> None:
        path = self._path(key)
        try:
            os.remove(path)
        except Exception:
            pass

    def has_secret(self, key: str) -> bool:
        return os.path.exists(self._path(key))


class _DPAPIStore(SecretsStore):
    """Windows DPAPI-backed secrets store.

    Stores each secret as a single encrypted blob on disk. Encryption is
    tied to the current Windows user account, so other users or machines
    cannot decrypt the data.
    """

    def __init__(self, base_path: str) -> None:
        self._base_path = base_path
        os.makedirs(self._base_path, exist_ok=True)
        try:
            import win32crypt  # type: ignore

            self._win32crypt = win32crypt
        except Exception as exc:
            raise RuntimeError("pywin32/win32crypt is required for DPAPI store") from exc

    def _encrypt(self, plaintext: str) -> bytes:
        blob = self._win32crypt.CryptProtectData(
            plaintext.encode("utf-8"),
            None,
            None,
            None,
            None,
            0,
        )
        return base64.b64encode(blob)

    def _decrypt(self, data: bytes) -> Optional[str]:
        try:
            blob = base64.b64decode(data)
            decrypted = self._win32crypt.CryptUnprotectData(
                blob,
                None,
                None,
                None,
                0,
            )
            if isinstance(decrypted, tuple):
                decrypted = decrypted[1]
            if isinstance(decrypted, bytes):
                return decrypted.decode("utf-8")
            if isinstance(decrypted, str):
                return decrypted
            return None
        except Exception as exc:
            logger.debug("DPAPI decrypt failed: %s", exc)
            return None

    def _path(self, key: str) -> str:
        safe = base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii").rstrip("=")
        return os.path.join(self._base_path, f"{safe}.bin")

    def set_secret(self, key: str, value: str) -> None:
        path = self._path(key)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        encrypted = self._encrypt(value)
        with open(path, "wb") as fh:
            fh.write(encrypted)

    def get_secret(self, key: str) -> Optional[str]:
        path = self._path(key)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            if not data:
                return None
            return self._decrypt(data)
        except Exception:
            return None

    def delete_secret(self, key: str) -> None:
        path = self._path(key)
        try:
            os.remove(path)
        except Exception:
            pass

    def has_secret(self, key: str) -> bool:
        return os.path.exists(self._path(key))


def create_store(base_path: str) -> SecretsStore:
    """Factory: return the most secure supported store for the host."""
    if os.name == "nt":
        try:
            return _DPAPIStore(base_path)
        except Exception:
            logger.debug("DPAPI store unavailable; falling back to plaintext store")
    return _PlaintextFallbackStore(base_path)
