"""Phase 9C deterministic tests: TRUE MULTI-ROUND iterative research.

These tests exercise the iterative research loop WITHOUT a live LLM or
internet by injecting a deterministic ``FakeSynthesizer`` (and a
``SeqGapFake`` that replays a preset sequence of gap-sets) into
``ResearchPipeline``.

Offline seam (same as Phase 9B):
  * ``research.pipeline.search_web`` is patched (note: pipeline imports it via
    ``from research.fetcher import search_web``, binding it separately, so the
    correct patch target is ``research.pipeline.search_web``).
  * ``research.pipeline.fetch_url`` / ``research.pipeline.extract_content`` are
    patched so no real HTTP request happens.

Each test asserts the DETERMINISTIC stop conditions of Phase 9C:
  2+ research rounds, gap -> search -> new gap -> search again, convergence
  when gaps empty, max-step / max-search / max-fetch termination, duplicate
  query/source prevention, timeout termination, provider/synthesizer failure
  without infinite looping, and no-progress termination.

All tests are ``@pytest.mark.offline``.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from research.pipeline import (
    ResearchPipeline,
    ResearchLimits,
    ResearchSource,
    ResearchStep,
    ResearchSynthesizer,
    ResearchSynthesisResult,
    ResearchSynthesisError,
    run_research,
)
from research.fetcher import FetchResult
from research.extractor import ExtractionResult
from modules.config import JarvisConfig


# -----------------------------------------------------------------------------
# Deterministic fakes & mocks
# -----------------------------------------------------------------------------
class SeqGapFake(ResearchSynthesizer):
    """Replays a preset list of gap-sets, one per identify_gaps call.

    The last entry should be ``[]`` to model convergence. If the pipeline
    keeps calling identify_gaps beyond the sequence, the final entry is
    repeated (so a non-converging sequence keeps returning gaps and lets the
    limit-based stop conditions take over).
    """

    def __init__(self, gap_sequence: list[list[str]], synthesis: str = "S [Source 1]."):
        self.gap_sequence = list(gap_sequence)
        self.synthesis = synthesis
        self.identify_calls = 0
        self.synthesize_calls = 0

    def identify_gaps(self, question: str, sources: list[ResearchSource]) -> list[str]:
        i = min(self.identify_calls, len(self.gap_sequence) - 1)
        self.identify_calls += 1
        return list(self.gap_sequence[i])

    def synthesize(self, question, sources, citations) -> ResearchSynthesisResult:
        self.synthesize_calls += 1
        return ResearchSynthesisResult(synthesis=self.synthesis, confidence=0.8, gaps=[])


class FailIdentifyFake(ResearchSynthesizer):
    """identify_gaps always raises (models provider down / rate-limit)."""

    def __init__(self, rate_limited: bool = True):
        self.rate_limited = rate_limited

    def identify_gaps(self, question, sources):
        raise ResearchSynthesisError("provider unavailable", rate_limited=self.rate_limited)

    def synthesize(self, question, sources, citations):
        return ResearchSynthesisResult(synthesis="degraded", confidence=0.0, gaps=[])


def _ok_fetch(url: str) -> FetchResult:
    return FetchResult(
        success=True, url=url, final_url=url, status_code=200,
        content=f"<html><body>{url}</body></html>", content_type="text/html",
    )


def _ok_extract(content: str, url: str = "") -> ExtractionResult:
    return ExtractionResult(success=True, text="extracted", title="T", method="t")


def _distinct_search_factory():
    """Returns a ``search_web`` mock that yields UNIQUE urls per call so each
    round can discover NEW sources (proving multi-round growth)."""
    calls = {"n": 0}

    def _search(query, **kwargs):
        i = calls["n"]
        calls["n"] += 1
        base = chr(ord("a") + (i % 26))
        return [
            {"title": f"P{j}", "href": f"https://{base}{i}.example.com/{j}", "body": "b"}
            for j in range(1, 3)
        ]

    return _search


def _make_pipeline(synth, limits):
    return ResearchPipeline(config=JarvisConfig(), limits=limits, synthesizer=synth)


# -----------------------------------------------------------------------------
# 1. Two or more research rounds occur
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestMultipleRounds:
    def test_two_additional_search_rounds(self):
        fake = SeqGapFake([["cost gap"], ["license gap"], []])
        search = _distinct_search_factory()
        with patch("research.pipeline.search_web", side_effect=search), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract(c, url)):
            limits = ResearchLimits(max_steps=40, max_searches=10, max_fetches=20, overall_timeout_s=30.0)
            result = _make_pipeline(fake, limits).research("Compare local LLMs")

        rounds = [s for s in result.research_steps if s.action == "additional_search"]
        assert len(rounds) >= 2, "expected at least 2 additional_search rounds"
        # gap detection ran once per round (initial + each iteration).
        assert fake.identify_calls >= 3
        # New sources were discovered across rounds.
        assert len(result.sources) > 2
        # Converged.
        assert result.gaps == []


# -----------------------------------------------------------------------------
# 2. gap -> search -> new gap -> search again
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestGapDrivesIterativeSearch:
    def test_new_gap_emerges_after_second_round(self):
        # Round 1 gap differs from round 2 gap; both must trigger a search.
        fake = SeqGapFake([["first gap"], ["second distinct gap"], []])
        search = _distinct_search_factory()
        with patch("research.pipeline.search_web", side_effect=search), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract(c, url)):
            limits = ResearchLimits(max_steps=40, max_searches=10, max_fetches=20, overall_timeout_s=30.0)
            result = _make_pipeline(fake, limits).research("Q")

        rounds = [s for s in result.research_steps if s.action == "additional_search"]
        assert len(rounds) == 2
        # Synthesis ran last.
        assert result.research_steps[-1].action == "synthesize"
        assert result.confidence == 0.8


# -----------------------------------------------------------------------------
# 3. Convergence when gaps become empty
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestConvergence:
    def test_stops_when_synthesizer_reports_no_gaps(self):
        fake = SeqGapFake([["a gap"], []])
        search = _distinct_search_factory()
        with patch("research.pipeline.search_web", side_effect=search), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract(c, url)):
            limits = ResearchLimits(max_steps=40, max_searches=10, max_fetches=20, overall_timeout_s=30.0)
            result = _make_pipeline(fake, limits).research("Q")

        # Only one additional_search round occurred, then convergence.
        rounds = [s for s in result.research_steps if s.action == "additional_search"]
        assert len(rounds) == 1
        assert result.gaps == []
        # No runaway: bounded step count.
        assert len(result.research_steps) < 15

    def test_already_converged_after_initial_round(self):
        # Synthesizer reports no gaps from the very first identify -> no rounds.
        fake = SeqGapFake([[]])
        search = _distinct_search_factory()
        with patch("research.pipeline.search_web", side_effect=search), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract(c, url)):
            limits = ResearchLimits(max_steps=40, max_searches=10, max_fetches=20, overall_timeout_s=30.0)
            result = _make_pipeline(fake, limits).research("Q")

        assert not any(s.action == "additional_search" for s in result.research_steps)
        assert result.gaps == []
        assert result.research_steps[-1].action == "synthesize"


# -----------------------------------------------------------------------------
# 4. max-step termination
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestMaxStepTermination:
    def test_loop_stops_at_max_steps(self):
        # Always return a gap -> would loop forever if not bounded.
        fake = SeqGapFake([["gap"] * 5])  # never converges
        search = _distinct_search_factory()
        with patch("research.pipeline.search_web", side_effect=search), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract(c, url)):
            limits = ResearchLimits(max_steps=11, max_searches=10, max_fetches=20, overall_timeout_s=30.0)
            t0 = time.perf_counter()
            result = _make_pipeline(fake, limits).research("loop")
            elapsed = time.perf_counter() - t0

        assert len(result.research_steps) <= limits.max_steps
        # No runaway: terminates quickly.
        assert elapsed < 15.0
        # Synthesis still produced.
        assert result.research_steps[-1].action == "synthesize"


# -----------------------------------------------------------------------------
# 5. max-search / max-fetch termination
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestMaxSearchFetchTermination:
    def test_max_searches_stops_loop(self):
        fake = SeqGapFake([["gap"] * 5])  # never converges
        search = _distinct_search_factory()
        with patch("research.pipeline.search_web", side_effect=search), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract(c, url)):
            # max_searches=3 => initial 1 + 2 additional rounds max.
            limits = ResearchLimits(max_steps=40, max_searches=3, max_fetches=20, overall_timeout_s=30.0)
            result = _make_pipeline(fake, limits).research("Q")

        search_steps = sum(1 for s in result.research_steps if s.action in ("search", "additional_search"))
        assert search_steps <= limits.max_searches
        rounds = [s for s in result.research_steps if s.action == "additional_search"]
        assert len(rounds) <= limits.max_searches - 1

    def test_max_fetches_stops_loop(self):
        fake = SeqGapFake([["gap"] * 5])  # never converges
        search = _distinct_search_factory()
        with patch("research.pipeline.search_web", side_effect=search), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract(c, url)):
            # max_fetches=1 => only the initial fetch round, no iterations.
            limits = ResearchLimits(max_steps=40, max_searches=10, max_fetches=1, overall_timeout_s=30.0)
            result = _make_pipeline(fake, limits).research("Q")

        fetch_steps = sum(1 for s in result.research_steps if s.action == "fetch")
        assert fetch_steps <= limits.max_fetches
        assert not any(s.action == "additional_search" for s in result.research_steps)


# -----------------------------------------------------------------------------
# 6. Duplicate query / source prevention
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestDeduplication:
    def test_duplicate_urls_not_added_across_rounds(self):
        fake = SeqGapFake([["gap1"], ["gap2"], []])
        # Return the SAME urls every call -> must be deduped, so no growth.
        with patch("research.pipeline.search_web", return_value=[
            {"title": "P1", "href": "https://dup.example.com/1", "body": "b"},
            {"title": "P2", "href": "https://dup.example.com/2", "body": "b"},
        ]), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract(c, url)):
            limits = ResearchLimits(max_steps=30, max_searches=10, max_fetches=20, overall_timeout_s=30.0)
            result = _make_pipeline(fake, limits).research("Q")

        urls = [s.url for s in result.sources]
        assert len(urls) == len(set(urls)), "duplicate URLs leaked across rounds"

    def test_duplicate_query_not_researched(self):
        # Two gaps produce the SAME query string -> only searched once.
        fake = SeqGapFake([["same"], []])
        seen = []

        def _tracking_search(query, **kwargs):
            seen.append(query.lower())
            return [
                {"title": f"P{j}", "href": f"https://q{len(seen)}.example.com/{j}", "body": "b"}
                for j in range(1, 2)
            ]

        with patch("research.pipeline.search_web", side_effect=_tracking_search), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract(c, url)):
            limits = ResearchLimits(max_steps=30, max_searches=10, max_fetches=20, overall_timeout_s=30.0)
            result = _make_pipeline(fake, limits).research("base")

        # The generated query is "base same" for both gaps; deduped to once.
        assert seen.count("base same") == 1


# -----------------------------------------------------------------------------
# 7. Timeout termination
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestTimeoutTermination:
    def test_overall_timeout_stops_loop(self):
        fake = SeqGapFake([["gap"] * 5])  # never converges
        search = _distinct_search_factory()
        with patch("research.pipeline.search_web", side_effect=search), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract(c, url)):
            # Tiny timeout; the loop must stop and still synthesize.
            limits = ResearchLimits(max_steps=40, max_searches=10, max_fetches=20, overall_timeout_s=0.05)
            t0 = time.perf_counter()
            result = _make_pipeline(fake, limits).research("Q")
            elapsed = time.perf_counter() - t0

        assert elapsed < 10.0  # no runaway past the timeout
        assert result.research_steps[-1].action == "synthesize"
        # Step count is bounded because the loop exited on timeout.
        assert len(result.research_steps) <= limits.max_steps


# -----------------------------------------------------------------------------
# 8. Provider / synthesizer failure without infinite looping
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestProviderFailureNoLoop:
    def test_identify_gaps_failure_does_not_loop(self):
        fake = FailIdentifyFake(rate_limited=True)
        search = _distinct_search_factory()
        with patch("research.pipeline.search_web", side_effect=search), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract(c, url)):
            limits = ResearchLimits(max_steps=40, max_searches=10, max_fetches=20, overall_timeout_s=30.0)
            result = _make_pipeline(fake, limits).research("Q")

        # identify_gaps failed -> gaps treated as empty -> no additional rounds.
        assert not any(s.action == "additional_search" for s in result.research_steps)
        # Still reached synthesis (graceful).
        assert result.research_steps[-1].action == "synthesize"
        assert result.confidence == 0.0


# -----------------------------------------------------------------------------
# 9. No-progress termination
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestNoProgressTermination:
    def test_loop_stops_when_no_new_sources(self):
        # Gaps always present, but every additional search returns urls that are
        # ALREADY known -> no new sources -> must stop (no infinite loop).
        fake = SeqGapFake([["gap"] * 5])  # never converges, but...
        known = {"https://known.example.com/1", "https://known.example.com/2"}

        def _search_returns_known(query, **kwargs):
            return [
                {"title": "P1", "href": "https://known.example.com/1", "body": "b"},
                {"title": "P2", "href": "https://known.example.com/2", "body": "b"},
            ]

        with patch("research.pipeline.search_web", side_effect=_search_returns_known), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract(c, url)):
            limits = ResearchLimits(max_steps=40, max_searches=10, max_fetches=20, overall_timeout_s=30.0)
            t0 = time.perf_counter()
            result = _make_pipeline(fake, limits).research("Q")
            elapsed = time.perf_counter() - t0

        # Only the initial round added sources; subsequent rounds added none.
        assert len(result.sources) == 2
        # No runaway.
        assert elapsed < 15.0
        # Synthesis still produced.
        assert result.research_steps[-1].action == "synthesize"

    def test_no_progress_with_failing_fetches(self):
        # Search discovers new urls, but every fetch FAILS -> no successful
        # sources added -> no progress -> loop must terminate.
        fake = SeqGapFake([["gap"] * 5])
        search = _distinct_search_factory()
        with patch("research.pipeline.search_web", side_effect=search), \
             patch("research.pipeline.fetch_url",
                   return_value=FetchResult(success=False, url="x", error="Timeout")), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract(c, url)):
            limits = ResearchLimits(max_steps=40, max_searches=10, max_fetches=20, overall_timeout_s=30.0)
            result = _make_pipeline(fake, limits).research("Q")

        # No successful sources -> confidence collapses, no infinite loop.
        assert result.confidence == 0.0
        # Terminated run: synthesize is the final step and steps are bounded.
        assert result.research_steps[-1].action == "synthesize"
        assert len(result.research_steps) <= limits.max_steps
