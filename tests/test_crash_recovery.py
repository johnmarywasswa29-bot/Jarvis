"""Focused tests for application crash recovery."""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
import traceback
import unittest
from io import StringIO
from pathlib import Path

REPO = Path(r"C:\Users\User NA\Desktop\jarvis")
sys.path.insert(0, str(REPO))

from runtime.crash_recovery import CrashRecovery


class TestCrashRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="crash_recovery_")
        self.log_dir = Path(self.tmp) / "logs"
        self.recovery = CrashRecovery(log_dir=self.log_dir)

    def tearDown(self):
        for root, dirs, files in os.walk(self.tmp, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(self.tmp)

    def test_unhandled_exception_captures_crash_log(self):
        self.recovery.install()
        try:
            raise ValueError("boom")
        except Exception:
            exc_type, exc_value, exc_tb = sys.exc_info()
            self.recovery._excepthook(exc_type, exc_value, exc_tb)
        log_path = self.log_dir / "crash.log"
        assert log_path.exists(), "crash.log was not created"
        content = log_path.read_text(encoding="utf-8")
        assert "ValueError" in content
        assert "boom" in content
        assert "timestamp:" in content

    def test_traceback_preserved(self):
        self.recovery.install()
        try:
            try:
                raise ValueError("inner")
            except ValueError:
                raise RuntimeError("outer")
        except Exception:
            exc_type, exc_value, exc_tb = sys.exc_info()
            self.recovery._excepthook(exc_type, exc_value, exc_tb)
        content = (self.log_dir / "crash.log").read_text(encoding="utf-8")
        assert "RuntimeError" in content
        assert "outer" in content
        assert "inner" in content

    def test_secret_safe_logging(self):
        self.recovery.install()
        secret = "ya29.super-secret-token-123"
        try:
            raise ValueError(f"auth failed for {secret}")
        except Exception:
            exc_type, exc_value, exc_tb = sys.exc_info()
            self.recovery._excepthook(exc_type, exc_value, exc_tb)
        content = (self.log_dir / "crash.log").read_text(encoding="utf-8")
        assert secret not in content, f"Secret leaked into crash log: {content}"

    def test_normal_shutdown_does_not_write_crash_log(self):
        self.recovery.install()
        self.recovery.mark_shutdown()
        exc_type, exc_value, exc_tb = sys.exc_info() or (None, None, None)
        if exc_type is None:
            try:
                raise ValueError("would-be crash")
            except Exception:
                exc_type, exc_value, exc_tb = sys.exc_info()
        self.recovery._excepthook(exc_type, exc_value, exc_tb)
        assert not (self.log_dir / "crash.log").exists(), "Crash log created during shutdown"

    def test_missing_log_directory_is_created(self):
        recovery = CrashRecovery(log_dir=self.log_dir / "missing" / "nested")
        recovery.install()
        try:
            raise ValueError("boom")
        except Exception:
            exc_type, exc_value, exc_tb = sys.exc_info()
            recovery._excepthook(exc_type, exc_value, exc_tb)
        assert (self.log_dir / "missing" / "nested" / "crash.log").exists()

    def test_repeated_crash_handling_overwrites_log(self):
        self.recovery.install()
        for msg in ("first", "second"):
            try:
                raise ValueError(msg)
            except Exception:
                exc_type, exc_value, exc_tb = sys.exc_info()
                self.recovery._excepthook(exc_type, exc_value, exc_tb)
        content = (self.log_dir / "crash.log").read_text(encoding="utf-8")
        assert "second" in content

    def test_unwritable_log_directory_does_not_raise(self):
        recovery = CrashRecovery(log_dir=Path(self.tmp) / "locked")
        recovery.install()
        try:
            raise ValueError("boom")
        except Exception:
            exc_type, exc_value, exc_tb = sys.exc_info()
            recovery._excepthook(exc_type, exc_value, exc_tb)
        # Should not raise even if log dir is not writable/creatable.

    def test_thread_exception_captured(self):
        self.recovery.install()

        class FakeThread:
            name = "crash_worker"

        fake_args = type("FakeArgs", (), {
            "exc_type": RuntimeError,
            "exc_value": RuntimeError("thread-boom"),
            "exc_traceback": None,
            "thread": FakeThread(),
        })()
        self.recovery._thread_excepthook(fake_args)
        content = (self.log_dir / "crash.log").read_text(encoding="utf-8")
        assert "RuntimeError" in content
        assert "thread-boom" in content

    def test_install_is_idempotent(self):
        self.recovery.install()
        orig = sys.excepthook
        self.recovery.install()
        assert sys.excepthook is orig

    def test_uninstall_restores_original_hooks(self):
        self.recovery.install()
        self.recovery.uninstall()
        assert sys.excepthook is sys.__excepthook__
        assert threading.excepthook is threading.__excepthook__


if __name__ == "__main__":
    unittest.main()
