"""Application-level crash recovery and crash logging."""
from __future__ import annotations

import logging
import os
import platform
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional


logger = logging.getLogger("crash_recovery")


class CrashRecovery:
    """Capture unhandled exceptions and write a secret-safe crash log."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        self._log_path = log_dir / "crash.log"
        self._shutting_down = False
        self._installed = False

    def install(self) -> None:
        if self._installed:
            return
        self._installed = True
        sys.excepthook = self._excepthook
        threading.excepthook = self._thread_excepthook

    def uninstall(self) -> None:
        if not self._installed:
            return
        self._installed = False
        sys.excepthook = sys.__excepthook__
        threading.excepthook = threading.__excepthook__

    def mark_shutdown(self) -> None:
        self._shutting_down = True

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    def _excepthook(self, exc_type, exc_value, exc_tb) -> None:  # noqa: ANN001
        if self._shutting_down:
            return
        self._write_crash_log(
            exc_type=exc_type,
            exc_value=exc_value,
            exc_tb=exc_tb,
            context="unhandled",
        )
        try:
            logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_tb))
        except Exception:
            pass
        try:
            sys.stderr.write("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
            sys.stderr.flush()
        except Exception:
            pass

    def _thread_excepthook(self, args: threading.ExceptHookArgs) -> None:
        if self._shutting_down:
            return
        self._write_crash_log(
            exc_type=args.exc_type,
            exc_value=args.exc_value,
            exc_tb=args.exc_traceback,
            context=f"thread:{args.thread.name}",
        )
        try:
            logger.critical(
                "Unhandled thread exception",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        except Exception:
            pass
        try:
            sys.stderr.write(
                "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
            )
            sys.stderr.flush()
        except Exception:
            pass

    def _write_crash_log(
        self,
        exc_type: type,
        exc_value: BaseException,
        exc_tb,
        context: str,
    ) -> None:
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            header = (
                f"timestamp: {datetime.now().isoformat()}\n"
                f"context: {context}\n"
                f"python_version: {sys.version}\n"
                f"executable: {sys.executable}\n"
                f"cwd: {os.getcwd()}\n"
                f"platform: {platform.platform()}\n"
                f"pid: {os.getpid()}\n"
                f"thread: {threading.current_thread().name}\n\n"
            )
            body = header + "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            body = self._sanitize_crash_text(body)
            self._log_path.write_text(body, encoding="utf-8", errors="replace")
        except Exception:
            pass

    @staticmethod
    def _sanitize_crash_text(text: str) -> str:
        """Redact common secret-bearing values from crash logs.

        This is a best-effort redaction layer. It does not guarantee that
        every possible secret shape is caught, but it removes lines that
        contain common secret-bearing keys to reduce accidental credential
        exposure in persisted crash logs.
        """
        secret_prefixes = (
            "token",
            "access_token",
            "refresh_token",
            "client_secret",
            "client-secret",
            "authorization",
            "bearer",
            "password",
            "secret",
        )
        redacted: list[str] = []
        for line in text.splitlines():
            lower = line.lower()
            if any(prefix in lower for prefix in secret_prefixes):
                redacted.append("[REDACTED SECRET]")
            else:
                redacted.append(line)
        return "\n".join(redacted) + "\n"
