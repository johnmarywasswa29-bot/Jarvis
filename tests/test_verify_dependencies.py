"""Deterministic regression tests for the startup dependency gate.

These verify that scripts.verify_dependencies.check_dependencies():
  * completes without importing heavy optional (audio/wake-word/ML) packages;
  * reports a missing OPTIONAL dependency as a non-fatal warning;
  * still fails (SystemExit) on a missing REQUIRED dependency;
  * does not attempt to import a heavy optional package during the check.

The tests monkeypatch the probe helpers so they are offline and deterministic
(no real imports of torch/onnxruntime/etc.).
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

import scripts.verify_dependencies as vd
from scripts.verify_dependencies import Dependency, check_dependencies


@pytest.fixture(autouse=True)
def _capture_stderr():
    import io
    buf = io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        yield buf
    finally:
        sys.stderr = old


def test_startup_check_completes(monkeypatch):
    # All deps "present": check must complete without raising.
    monkeypatch.setattr(vd, "_import_available", lambda name: True)
    monkeypatch.setattr(vd, "_spec_available", lambda name: True)
    # Should not raise.
    check_dependencies()


def test_missing_optional_reported_safely(monkeypatch, capsys):
    monkeypatch.setattr(vd, "_import_available", lambda name: True)
    # Pretend openwakeword (heavy optional) is missing; everything else present.
    def spec(name):
        return None if name == "openwakeword" else object()
    monkeypatch.setattr(vd, "_spec_available", spec)

    # Must NOT raise (optional missing is non-fatal).
    check_dependencies()
    captured = capsys.readouterr()
    assert "openwakeword" in captured.err
    assert "optional dependency missing" in captured.err.lower()


def test_missing_required_still_fails(monkeypatch):
    monkeypatch.setattr(vd, "_import_available", lambda name: name != "yaml")
    monkeypatch.setattr(vd, "_spec_available", lambda name: True)

    with pytest.raises(SystemExit) as exc:
        check_dependencies()
    assert exc.value.code == 1


def test_heavy_optional_not_imported_during_check(monkeypatch, capsys):
    """check_dependencies must probe optional deps by SPEC only, never by
    importing their module body (which would pull torch/onnxruntime)."""
    imported_bodies = []

    real_import = importlib.import_module

    def tracking_import(name, *a, **k):
        if name.split(".")[0] in {"openwakeword", "vosk", "torch", "onnxruntime", "transformers"}:
            imported_bodies.append(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(vd, "_import_available", lambda name: True)
    # Even if a heavy optional package's spec is reported present, it must not
    # be imported by the checker.
    monkeypatch.setattr(vd, "_spec_available", lambda name: True)
    monkeypatch.setattr(importlib, "import_module", tracking_import)

    check_dependencies()
    assert imported_bodies == [], (
        f"check_dependencies imported heavy optional module(s): {imported_bodies}"
    )


def test_required_is_lightweight_set():
    # Sanity: required set must not include the heavy audio/ML packages whose
    # import was the original startup hang.
    required_names = {d.import_name for d in vd.REQUIRED_DEPENDENCIES}
    for heavy in ("openwakeword", "vosk", "torch", "chromadb", "sentence_transformers"):
        assert heavy not in required_names, (
            f"{heavy} must not be a REQUIRED startup dependency"
        )


def test_optional_marked_optional():
    for d in vd.OPTIONAL_DEPENDENCIES:
        assert d.optional is True
    # The two packages that caused the original hang must be optional.
    optional_names = {d.import_name for d in vd.OPTIONAL_DEPENDENCIES}
    assert "openwakeword" in optional_names
    assert "vosk" in optional_names
