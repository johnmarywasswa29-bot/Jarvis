"""Focused security tests for calendar secrets storage."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from plugins.calendar_plugin.secrets import (
    _DPAPIStore,
    _PlaintextFallbackStore,
    create_store,
)
from plugins.calendar_plugin.provider_google import GoogleProvider
from plugins.calendar_plugin.provider_outlook import OutlookProvider


class TestPlaintextFallbackStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="calendar_secrets_")
        self.store = _PlaintextFallbackStore(self.tmp)

    def tearDown(self):
        for root, dirs, files in os.walk(self.tmp, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(self.tmp)

    def test_set_get_round_trip(self):
        self.store.set_secret("key", "value")
        assert self.store.get_secret("key") == "value"

    def test_overwrite(self):
        self.store.set_secret("key", "v1")
        self.store.set_secret("key", "v2")
        assert self.store.get_secret("key") == "v2"

    def test_delete(self):
        self.store.set_secret("key", "value")
        self.store.delete_secret("key")
        assert self.store.get_secret("key") is None
        assert not self.store.has_secret("key")

    def test_missing_secret(self):
        assert self.store.get_secret("missing") is None
        assert not self.store.has_secret("missing")

    def test_malformed_corrupt_data(self):
        key = "corrupt"
        safe = __import__("base64").urlsafe_b64encode(key.encode("utf-8")).decode("ascii").rstrip("=")
        with open(os.path.join(self.tmp, f"{safe}.json"), "w", encoding="utf-8") as fh:
            fh.write("not-json")
        assert self.store.get_secret(key) is None


class TestDPAPIStore(unittest.TestCase):
    def setUp(self):
        if os.name != "nt":
            self.skipTest("DPAPI is Windows-only")
        self.tmp = tempfile.mkdtemp(prefix="calendar_dpapi_")
        self.store = _DPAPIStore(self.tmp)

    def tearDown(self):
        for root, dirs, files in os.walk(self.tmp, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(self.tmp)

    def test_set_get_round_trip(self):
        self.store.set_secret("key", "value")
        assert self.store.get_secret("key") == "value"

    def test_overwrite(self):
        self.store.set_secret("key", "v1")
        self.store.set_secret("key", "v2")
        assert self.store.get_secret("key") == "v2"

    def test_delete(self):
        self.store.set_secret("key", "value")
        self.store.delete_secret("key")
        assert self.store.get_secret("key") is None
        assert not self.store.has_secret("key")

    def test_missing_secret(self):
        assert self.store.get_secret("missing") is None
        assert not self.store.has_secret("missing")

    def test_plaintext_not_written(self):
        self.store.set_secret("secret-key", "secret-value")
        safe = __import__("base64").urlsafe_b64encode("secret-key".encode("utf-8")).decode("ascii").rstrip("=")
        path = os.path.join(self.tmp, f"{safe}.bin")
        blob = Path(path).read_bytes()
        assert b"secret-value" not in blob

    def test_corrupt_blob(self):
        safe = __import__("base64").urlsafe_b64encode("key".encode("utf-8")).decode("ascii").rstrip("=")
        path = os.path.join(self.tmp, f"{safe}.bin")
        Path(path).write_bytes(b"not-a-dpapi-blob")
        assert self.store.get_secret("key") is None


class TestCreateStore(unittest.TestCase):
    def test_windows_returns_dpapi(self):
        with patch("os.name", "nt"), patch("plugins.calendar_plugin.secrets._DPAPIStore") as mock:
            create_store("tmp")
            mock.assert_called_once_with("tmp")

    def test_non_windows_returns_fallback(self):
        with patch("os.name", "posix"):
            store = create_store("tmp")
            assert isinstance(store, _PlaintextFallbackStore)


class TestLegacyMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="calendar_mig_")
        self.store = _PlaintextFallbackStore(self.tmp)

    def tearDown(self):
        for root, dirs, files in os.walk(self.tmp, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(self.tmp)

    def test_google_migration(self):
        token_path = os.path.join(self.tmp, "google_token.json")
        Path(token_path).write_text('{"token": "x"}', encoding="utf-8")
        provider = GoogleProvider()
        provider._token_path = token_path
        provider._store = self.store
        provider._migrate_legacy_token(token_path)
        assert not Path(token_path).exists()
        assert self.store.get_secret(token_path) == '{"token": "x"}'

    def test_outlook_migration(self):
        token_path = os.path.join(self.tmp, "outlook_token.txt")
        Path(token_path).write_text("legacy-outlook-token", encoding="utf-8")
        provider = OutlookProvider()
        provider._token_path = token_path
        provider._store = self.store
        provider._migrate_legacy_token(token_path)
        assert not Path(token_path).exists()
        assert self.store.get_secret(token_path) == "legacy-outlook-token"

    def test_google_migration_missing_file(self):
        token_path = os.path.join(self.tmp, "google_token.json")
        provider = GoogleProvider()
        provider._token_path = token_path
        provider._store = self.store
        provider._migrate_legacy_token(token_path)
        assert not Path(token_path).exists()
        assert self.store.get_secret(token_path) is None

    def test_migration_safety_on_store_failure(self):
        token_path = os.path.join(self.tmp, "token.json")
        Path(token_path).write_text("value", encoding="utf-8")
        provider = OutlookProvider()
        provider._token_path = token_path
        failing_store = _PlaintextFallbackStore(self.tmp)

        def failing_set(key, value):
            raise OSError("disk full")

        failing_store.set_secret = failing_set
        provider._store = failing_store
        provider._migrate_legacy_token(token_path)
        assert Path(token_path).exists()

    def test_google_load_token_migrates(self):
        token_path = os.path.join(self.tmp, "google_token.json")
        Path(token_path).write_text('{"token": "x"}', encoding="utf-8")
        provider = GoogleProvider()
        provider._token_path = token_path
        provider._store = self.store
        result = provider._load_token(token_path)
        assert result is None
        assert not Path(token_path).exists()
        assert self.store.get_secret(token_path) == '{"token": "x"}'

    def test_google_load_token_parses_secure_json(self):
        try:
            import google.oauth2.credentials  # noqa: F401
        except ImportError:
            self.skipTest("google-auth not installed")
        token_path = os.path.join(self.tmp, "google_token.json")
        creds_json = json.dumps({
            "token": "ya29.fake",
            "refresh_token": "rt",
            "client_id": "cid",
            "client_secret": "csecret",
            "scopes": ["https://www.googleapis.com/auth/calendar.readonly"],
        })
        self.store.set_secret(token_path, creds_json)
        provider = GoogleProvider()
        provider._token_path = token_path
        provider._store = self.store

        captured = {}
        orig_factory = None
        try:
            import google.oauth2.credentials as _gc

            orig_factory = _gc.Credentials.from_authorized_user_info

            def fake_factory(info, scopes=None):
                captured["info"] = info
                captured["scopes"] = scopes
                return None

            _gc.Credentials.from_authorized_user_info = fake_factory
            result = provider._load_token(token_path)
        finally:
            if orig_factory is not None:
                _gc.Credentials.from_authorized_user_info = orig_factory
        assert result is None
        assert captured["info"].get("token") == "ya29.fake"
        assert captured["info"].get("refresh_token") == "rt"

    def test_outlook_load_token_migrates(self):
        token_path = os.path.join(self.tmp, "outlook_token.txt")
        Path(token_path).write_text("legacy-token", encoding="utf-8")
        provider = OutlookProvider()
        provider._token_path = token_path
        provider._store = self.store
        token = provider._load_token()
        assert token is None
        assert not Path(token_path).exists()
        assert self.store.get_secret(token_path) == "legacy-token"

    def test_outlook_migration_idempotent(self):
        token_path = os.path.join(self.tmp, "outlook_token.txt")
        Path(token_path).write_text("legacy-token", encoding="utf-8")
        provider = OutlookProvider()
        provider._token_path = token_path
        provider._store = self.store
        provider._migrate_legacy_token(token_path)
        assert not Path(token_path).exists()
        assert self.store.get_secret(token_path) == "legacy-token"
        provider._migrate_legacy_token(token_path)
        assert not Path(token_path).exists()
        assert self.store.get_secret(token_path) == "legacy-token"


class TestProviderPlaintextNotWritten(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="calendar_provider_")
        self.store = _PlaintextFallbackStore(self.tmp)

    def tearDown(self):
        for root, dirs, files in os.walk(self.tmp, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(self.tmp)

    def test_google_no_plaintext_without_legacy(self):
        provider = GoogleProvider()
        provider._token_path = ""
        provider._client_secrets_path = ""
        provider._store = self.store
        provider._ensure_authed()
        assert not any(Path(self.tmp).iterdir())

    def test_outlook_no_plaintext_without_token_path(self):
        provider = OutlookProvider()
        provider._token_path = ""
        provider.client_id = ""
        provider.client_secret = ""
        provider._store = self.store
        provider._ensure_token()
        assert not any(Path(self.tmp).iterdir())


if __name__ == "__main__":
    unittest.main()
