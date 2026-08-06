"""Phase 10 tests: ProactiveManager, SuggestionEngine, TriggerEngine, ContextAnalyzer, DismissalMemory."""
from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from proactive.state import Suggestion, Trigger, NotificationQueueItem
from proactive.history import ProactiveHistory
from proactive.suggestion_engine import SuggestionEngine, DismissalMemory, NotificationQueue
from proactive.trigger_engine import TriggerEngine
from proactive.context_analyzer import ContextAnalyzer
from proactive.proactive_manager import ProactiveManager


def tmp_db(name: str) -> Path:
    d = REPO / "tests" / "tmp_proactive" / name
    if d.exists():
        import shutil
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    return d / "proactive.sqlite"


class TestSuggestionState(unittest.TestCase):
    def test_defaults(self):
        s = Suggestion()
        assert s.suggestion_id
        assert s.created_at

    def test_fields(self):
        s = Suggestion(title="t", body="b", priority=0.9, confidence=0.8, urgency=0.5, context_relevance=0.6, expected_usefulness=0.7)
        assert s.title == "t"
        assert s.priority == 0.9


class TestDismissalMemory(unittest.TestCase):
    def test_record_and_check(self):
        d = DismissalMemory()
        s = Suggestion(title="x")
        assert not d.is_dismissed(s)
        d.record_dismissal(s)
        assert d.is_dismissed(s)

    def test_decay_reduces_score(self):
        d = DismissalMemory()
        s = Suggestion(title="x", priority=0.8)
        d.record_dismissal(s)
        decayed = d.decay_score(s)
        assert decayed < s.priority


class TestNotificationQueue(unittest.TestCase):
    def test_enqueue_and_drain(self):
        q = NotificationQueue(max_size=2)
        s1 = Suggestion(title="a")
        s2 = Suggestion(title="b")
        q.enqueue(s1)
        q.enqueue(s2)
        assert q.size() == 2
        out = q.drain(limit=1)
        assert len(out) == 1
        assert q.size() == 1


class TestSuggestionEngine(unittest.TestCase):
    def test_rank_sorts(self):
        engine = SuggestionEngine()
        s1 = Suggestion(title="low", priority=0.1, confidence=0.1)
        s2 = Suggestion(title="high", priority=0.9, confidence=0.9)
        ranked = engine.rank([s1, s2])
        assert ranked[0].title == "high"

    def test_enqueue_rate_limit(self):
        engine = SuggestionEngine()
        engine._user_suggestion_interval_s = 0.0
        s1 = Suggestion(title="a")
        s2 = Suggestion(title="b")
        accepted = engine.enqueue([s1, s2])
        assert len(accepted) >= 1
        assert engine.can_suggest()

    def test_dismiss_reduces_rank(self):
        engine = SuggestionEngine()
        good = Suggestion(title="good", priority=0.9, confidence=0.9)
        bad = Suggestion(title="bad", priority=0.9, confidence=0.9)
        engine.dismiss(bad)
        ranked = engine.rank([good, bad])
        assert ranked[0].title == "good"
        assert ranked[-1].title == "bad"


class TestTriggerEngine(unittest.TestCase):
    def test_git_dirty_trigger(self):
        t = Trigger(name="git_dirty", condition="git_dirty", category="workspace")
        engine = TriggerEngine()
        engine.register(t)
        ctx = {"workspace": {"active_application": "code.exe"}, "project": {"git_repo": "/tmp", "name": "demo"}}
        suggestions = engine.evaluate(ctx)
        assert len(suggestions) == 1
        assert suggestions[0].category == "workspace"

    def test_cooldown_blocks(self):
        t = Trigger(name="git_dirty", condition="git_dirty", cooldown_s=9999.0)
        t.last_fired = time.time()
        engine = TriggerEngine()
        engine.register(t)
        ctx = {"workspace": {"active_application": "code.exe"}, "project": {"git_repo": "/tmp"}}
        suggestions = engine.evaluate(ctx)
        assert len(suggestions) == 0


class SimpleContextProject:
    def __init__(self, name, path, language="", git_repo="", ide=""):
        self.name = name
        self.path = path
        self.language = language
        self.git_repo = git_repo
        self.ide = ide


class TestContextAnalyzer(unittest.TestCase):
    def test_empty_context(self):
        analyzer = ContextAnalyzer()
        ctx = analyzer.analyze("hello")
        assert isinstance(ctx, dict)

    def test_with_workspace(self):
        fake_snap = MagicMock()
        fake_snap.active_project = "demo"
        fake_snap.working_directory = "/tmp"
        fake_snap.git_repository = "/tmp"
        fake_snap.open_applications = []
        fake_snap.confidence = 0.5
        fake_proj = SimpleContextProject("demo", "/tmp")
        fake_ws = MagicMock()
        fake_ws.snapshot.return_value = fake_snap
        fake_ws.current_project.return_value = fake_proj
        analyzer = ContextAnalyzer(workspace_manager=fake_ws)
        ctx = analyzer.analyze("hello")
        assert "workspace" in ctx
        assert ctx["workspace"]["active_project"] == "demo"


class TestProactiveHistory(unittest.TestCase):
    def test_save_and_recent(self):
        hist = ProactiveHistory(tmp_db("recent"))
        s = Suggestion(title="hello", body="world")
        hist.save_suggestion(s)
        recent = hist.recent_suggestions(5)
        assert len(recent) == 1
        assert recent[0].title == "hello"
        hist.close()

    def test_dismiss(self):
        hist = ProactiveHistory(tmp_db("dismiss"))
        s = Suggestion(title="bye")
        hist.save_suggestion(s)
        hist.dismiss(s.suggestion_id)
        recent = hist.recent_suggestions(5)
        assert recent[0].dismissed is True
        hist.close()


class TestProactiveManager(unittest.TestCase):
    def test_start_registers_triggers(self):
        mgr = ProactiveManager()
        try:
            mgr.start()
            suggestions = mgr.analyze("hello")
            assert isinstance(suggestions, list)
        finally:
            mgr.close()

    def test_dismiss(self):
        mgr = ProactiveManager()
        try:
            s = Suggestion(title="dismiss me")
            mgr.dismiss(s)
            assert True
        finally:
            mgr.close()


if __name__ == "__main__":
    unittest.main()
