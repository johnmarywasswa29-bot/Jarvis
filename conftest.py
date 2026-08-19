"""Global pytest configuration for Jarvis.

Anti-hang strategy (two layers):

1. ``pyproject.toml`` enables ``pytest-timeout`` with a short global default
   (``timeout = 45``) and ``timeout_method = "thread"``. The ``thread`` method
   fails any single test that exceeds the limit instead of hanging the suite
   forever. This is the primary guard and works even when a test is blocked on
   a stalled network socket.

2. Markers ``network`` and ``offline`` let us run the two suites separately:
   ``pytest -m offline`` (no internet) and ``pytest -m network`` (live IO).
   Network tests additionally carry their own short timeouts at the
   fetcher/search level so a single slow endpoint cannot dominate.

Per-test timeouts can be tightened with the ``timeout`` marker, e.g.
``@pytest.mark.timeout(8)``.
"""

from __future__ import annotations

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "network: test performs live network IO (requires internet)"
    )
    config.addinivalue_line(
        "markers", "offline: test runs without any network access"
    )
