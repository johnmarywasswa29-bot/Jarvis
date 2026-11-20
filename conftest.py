"""Global pytest configuration for Jarvis.

Anti-hang strategy:

Markers ``network`` and ``offline`` let us run the two suites separately:
``pytest -m offline`` (no internet) and ``pytest -m network`` (live IO).
Network tests carry their own short timeouts at the fetcher/search level so a
single slow endpoint cannot dominate. There is deliberately no global
``pytest-timeout`` default: it interrupts native embedding/chroma work.

All per-test artifacts are kept inside pytest's ``tmp_path`` so a test run
never dirties the working tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_test_paths(request, tmp_path, monkeypatch):
    """Give each test a private scratch directory.

    ``unittest.TestCase`` methods cannot request fixtures directly, so the
    per-test ``tmp_path`` is attached to the instance as ``self.tmp_path``.
    Legacy tests that module-level artifacts are redirected here as well.
    """
    instance = getattr(request, "instance", None)
    if instance is not None:
        instance.tmp_path = tmp_path

    module = request.module
    module_file = Path(getattr(module, "__file__", __file__)).name

    if module_file in {"test_knowledge_phase3.py", "test_knowledge_hardening.py"}:
        knowledge_tmp = tmp_path / "knowledge"
        knowledge_tmp.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(module, "TMP_DIR", knowledge_tmp, raising=False)

    if module_file == "test_calendar_plugin.py":
        calendar_tmp = tmp_path / "tests"
        calendar_tmp.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(module, "REPO", tmp_path, raising=False)

        from plugins.calendar_plugin import plugin as calendar_plugin_module

        original_memory = calendar_plugin_module.CalendarMemory

        def _tmp_memory(*args, **kwargs):
            kwargs["path"] = str(tmp_path / "calendar_memory.json")
            return original_memory(*args, **kwargs)

        monkeypatch.setattr(calendar_plugin_module, "CalendarMemory", _tmp_memory)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "network: test performs live network IO (requires internet)"
    )
    config.addinivalue_line(
        "markers", "offline: test runs without any network access"
    )
