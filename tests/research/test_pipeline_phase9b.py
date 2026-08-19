"""Phase 9B deterministic tests for the research pipeline.

These tests exercise the FULL multi-step research flow
(search -> fetch -> extract -> identify gaps -> additional search ->
synthesize) WITHOUT a live LLM or internet, by injecting a deterministic
``FakeSynthesizer`` into ``ResearchPipeline``.

Network is mocked at the seam documented in test_pipeline.py:
  * ``research.fetcher.search_web`` is patched so no real DDGS call runs.
  * ``research.pipeline.fetch_url`` / ``extract_content`` are patched so no
    real HTTP request happens.
The LLM-dependent reasoning (gap detection + synthesis) is supplied by the
fake, which makes the behavior fully deterministic and offline.

All tests are ``@pytest.mark.offline`` except where noted. The single live
network check remains in test_pipeline.py::TestResearchPipelineNetwork.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from research.pipeline import (
    ResearchPipeline,
    ResearchLimits,
    ResearchFindings,
    ResearchSource,
    ResearchStep,
    ResearchSynthesizer,
    ResearchSynthesisResult,
    ResearchSynthesisError,
    LLMResearchSynthesizer,
    run_research,
)
from research.fetcher import FetchResult
from research.extractor import ExtractionResult
from modules.config import JarvisConfig


# -----------------------------------------------------------------------------
# Deterministic fake synthesizer
# -----------------------------------------------------------------------------
class FakeSynthesizer(ResearchSynthesizer):
    """Returns canned gaps / synthesis so research behavior is deterministic.

    ``gaps`` is the list returned by identify_gaps. ``synthesis``,
    ``confidence``, and ``extra_gaps`` populate the ResearchSynthesisResult.
    If ``raise_on_identify`` / ``raise_on_synthesize`` are set, the
    corresponding method raises ``ResearchSynthesisError`` (rate_limited flag
    chosen by ``rate_limited``) to exercise graceful degradation.
    """

    def __init__(
        self,
        gaps: list[str] | None = None,
        synthesis: str = "Synthesized answer with [Source 1] citation.",
        confidence: float = 0.9,
        extra_gaps: list[str] | None = None,
        raise_on_identify: bool = False,
        raise_on_synthesize: bool = False,
        rate_limited: bool = True,
        gap_sequence: list[list[str]] | None = None,
    ) -> None:
        self.gaps = gaps if gaps is not None else []
        # If a gap_sequence is supplied, identify_gaps replays it (one entry per
        # call) and converges to the final entry; otherwise the fixed ``gaps``
        # list is returned on every call. This lets tests model multi-round
        # convergence under the Phase 9C iterative loop.
        self.gap_sequence = gap_sequence
        self._gap_index = 0
        self.synthesis = synthesis
        self.confidence = confidence
        self.extra_gaps = extra_gaps if extra_gaps is not None else []
        self.raise_on_identify = raise_on_identify
        self.raise_on_synthesize = raise_on_synthesize
        self.rate_limited = rate_limited
        self.identify_calls: list[tuple[str, list]] = []
        self.synthesize_calls: list[tuple[str, list, list]] = []

    def identify_gaps(self, question: str, sources: list[ResearchSource]) -> list[str]:
        self.identify_calls.append((question, list(sources)))
        if self.raise_on_identify:
            raise ResearchSynthesisError(
                "provider unavailable during gap analysis",
                rate_limited=self.rate_limited,
            )
        if self.gap_sequence is not None:
            i = min(self._gap_index, len(self.gap_sequence) - 1)
            self._gap_index += 1
            return list(self.gap_sequence[i])
        return list(self.gaps)

    def synthesize(self, question, sources, citations) -> ResearchSynthesisResult:
        self.synthesize_calls.append((question, list(sources), list(citations)))
        if self.raise_on_synthesize:
            raise ResearchSynthesisError(
                "provider rate-limited during synthesis",
                rate_limited=self.rate_limited,
            )
        return ResearchSynthesisResult(
            synthesis=self.synthesis,
            confidence=self.confidence,
            gaps=list(self.extra_gaps),
        )


# -----------------------------------------------------------------------------
# Shared mock fixtures
# -----------------------------------------------------------------------------
def _search_results(n: int, prefix: str = "example") -> list[dict[str, str]]:
    return [
        {
            "title": f"Page {i}",
            "href": f"https://{prefix}.com/{i}",
            "body": f"Snippet for result {i}",
        }
        for i in range(1, n + 1)
    ]


def _ok_fetch(url: str, text: str = "Fetched content for the page.") -> FetchResult:
    return FetchResult(
        success=True,
        url=url,
        final_url=url,
        status_code=200,
        content=f"<html><title>{url}</title><body><main>{text}</main></body></html>",
        content_type="text/html",
    )


# NOTE: research.pipeline imports ``search_web`` via ``from research.fetcher
# import search_web``, so the name is bound separately on the pipeline module.
# The correct offline patch target is ``research.pipeline.search_web``
# (patching ``research.fetcher.search_web`` does NOT affect the pipeline).
_SEARCH_PATCH = "research.pipeline.search_web"


def _ok_extract(text: str = "Fetched content for the page.") -> ExtractionResult:
    return ExtractionResult(success=True, text=text, title="Page", method="trafilatura")


def _make_pipeline(synthesizer, limits=None):
    config = JarvisConfig()
    return ResearchPipeline(config=config, limits=limits, synthesizer=synthesizer)


# -----------------------------------------------------------------------------
# 1. Multi-step research (search -> fetch -> extract -> gaps -> add search -> synth)
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestMultiStepResearch:
    def test_full_flow_completes_with_fake(self):
        # Convergence sequence: one gap this round, then empty (no more rounds).
        fake = FakeSynthesizer(gap_sequence=[["Need more detail on cost"], []])
        with patch(_SEARCH_PATCH, return_value=_search_results(2)), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract()):
            pipeline = _make_pipeline(fake, ResearchLimits(max_steps=20, max_searches=5, max_fetches=8))
            result = pipeline.research("Best local LLMs in 2026?")

        assert result.query == "Best local LLMs in 2026?"
        assert result.research_id
        # search, fetch, extract, identify_gaps, (gaps -> additional), fetch, extract, synthesize
        actions = [s.action for s in result.research_steps]
        assert "search" in actions
        assert "fetch" in actions
        assert "extract" in actions
        assert "identify_gaps" in actions
        assert "additional_search" in actions
        assert "synthesize" in actions
        # Synthesis executed with the fake's canned output.
        assert "Synthesized" in result.synthesis
        assert abs(result.confidence - 0.9) < 1e-9
        # Both gap identify and synthesize were invoked.
        assert fake.identify_calls
        assert fake.synthesize_calls

    def test_sources_recorded_with_status(self):
        fake = FakeSynthesizer(gaps=[])
        with patch(_SEARCH_PATCH, return_value=_search_results(2)), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract()):
            pipeline = _make_pipeline(fake, ResearchLimits(max_steps=4, max_searches=1, max_fetches=2))
            result = pipeline.research("Capital of France?")
        fetched = [s for s in result.sources if s.fetch_status == "success"]
        assert len(fetched) >= 1
        assert all(s.extracted_text for s in fetched)


# -----------------------------------------------------------------------------
# 2. Gap detection drives additional searches
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestGapDetectionDrivesAdditionalSearch:
    def test_gaps_trigger_additional_search(self):
        # Convergence sequence: gaps this round, then empty (stops after one round).
        fake = FakeSynthesizer(gap_sequence=[["Missing cost info", "Missing license info"], []])
        seen = {"n": 0}

        def _search(q, **k):
            i = seen["n"]
            seen["n"] += 1
            # Initial search -> one prefix; additional search -> a DIFFERENT
            # prefix so genuinely new sources are discovered.
            pfx = "more" if i == 0 else "extra"
            return [{"title": f"P{j}", "href": f"https://{pfx}.com/{j}", "body": "b"} for j in range(1, 3)]

        with patch(_SEARCH_PATCH, side_effect=_search), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract()):
            pipeline = _make_pipeline(fake, ResearchLimits(max_steps=20, max_searches=5, max_fetches=6, overall_timeout_s=30.0))
            result = pipeline.research("Compare local LLMs")

        assert any(s.action == "additional_search" for s in result.research_steps)
        # Initial 2 (more.com) + additional 2 (extra.com) = 4 source records.
        assert len(result.sources) >= 4
        # The additional search queries embed the gap text.
        add_step = next(s for s in result.research_steps if s.action == "additional_search")
        # results_count reflects new sources added.
        assert add_step.results_count >= 1

    def test_no_gaps_no_additional_search(self):
        fake = FakeSynthesizer(gaps=[])
        with patch(_SEARCH_PATCH, return_value=_search_results(2)), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract()):
            pipeline = _make_pipeline(fake, ResearchLimits(max_steps=4, max_searches=1, max_fetches=2))
            result = pipeline.research("Simple question")
        assert not any(s.action == "additional_search" for s in result.research_steps)


# -----------------------------------------------------------------------------
# 3. Iterative search + URL deduplication
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestIterativeSearchDedup:
    def test_duplicate_urls_not_added(self):
        # Two rounds; each search returns a mix of NEW and DUPLICATE urls.
        # The pipeline must dedupe across every round (no repeated sources).
        fake = FakeSynthesizer(gap_sequence=[["gap one"], []])
        seen = {"n": 0}

        def _search(q, **k):
            i = seen["n"]
            seen["n"] += 1
            if i == 0:
                # Initial search: unique urls.
                return [{"title": f"P{j}", "href": f"https://dup.com/{j}", "body": "b"} for j in range(1, 3)]
            # Additional search: returns dup.com/2 (already known) and a NEW
            # dup.com/3 as the first two results (the pipeline only inspects the
            # first two per gap), then a repeat of /1. /3 must be added, /1 & /2 deduped.
            return [
                {"title": "P2", "href": "https://dup.com/2", "body": "b"},
                {"title": "P3", "href": "https://dup.com/3", "body": "b"},
                {"title": "P1", "href": "https://dup.com/1", "body": "b"},
            ]

        with patch(_SEARCH_PATCH, side_effect=_search), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract()):
            pipeline = _make_pipeline(fake, ResearchLimits(max_steps=20, max_searches=5, max_fetches=10, overall_timeout_s=30.0))
            result = pipeline.research("Dup test")
        urls = [s.url for s in result.sources]
        assert len(urls) == len(set(urls)), "duplicate URLs were added across iterations"
        # Exactly 3 unique sources (dup.com/1, /2, /3).
        assert len(result.sources) == 3


# -----------------------------------------------------------------------------
# 4. Max-step enforcement / runaway protection
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestMaxStepEnforcement:
    def test_research_terminates_finitely_no_runaway(self):
        # Synthesizer always returns gaps, trying to force an infinite loop.
        # The pipeline must still terminate in a finite, bounded number of
        # steps (current design: one gap-iteration round, then synthesize).
        fake = FakeSynthesizer(gaps=["another gap"])
        with patch(_SEARCH_PATCH, return_value=_search_results(1)), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract()):
            import time
            t0 = time.perf_counter()
            result = _make_pipeline(
                fake,
                ResearchLimits(max_steps=6, max_searches=3, max_fetches=3, overall_timeout_s=30.0),
            ).research("Loop forever?")
            elapsed = time.perf_counter() - t0
        # Finite and fast: never a runaway loop.
        assert 1 <= len(result.research_steps) <= 10
        assert elapsed < 15.0

    def test_many_gaps_does_not_loop_unbounded(self):
        # Many initial gaps must NOT loop once per gap; the iterative loop runs
        # rounds until the synthesizer converges (returns no gaps), then stops.
        # Here the sequence converges after the first additional round.
        fake = FakeSynthesizer(gap_sequence=[["g1", "g2", "g3", "g4", "g5"], []])
        with patch(_SEARCH_PATCH, side_effect=lambda q, **k: _search_results(1, prefix="lim")), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract()):
            result = _make_pipeline(
                fake,
                ResearchLimits(max_steps=20, max_searches=5, max_fetches=10, overall_timeout_s=30.0),
            ).research("Limited iterations")
        # At least one gap-iteration round executed (gap -> search -> re-assess).
        assert any(s.action == "additional_search" for s in result.research_steps)
        # Step count is bounded and never explodes regardless of gap count.
        assert len(result.research_steps) <= 15
        # Converged: no remaining gaps.
        assert result.gaps == []

    def test_max_steps_gate_prevents_gap_iteration(self):
        # When max_steps is too small to fit a gap-iteration round, the
        # additional-search step is skipped entirely (graceful truncation).
        fake = FakeSynthesizer(gaps=["some gap"])
        with patch(_SEARCH_PATCH, return_value=_search_results(1)), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract()):
            # max_steps=4 means after search(1)+fetch(1)+extract(1)+identify_gaps(1)
            # the len(steps) < max_steps gate (4 < 4) is False -> no add search.
            result = _make_pipeline(
                fake,
                ResearchLimits(max_steps=4, max_searches=1, max_fetches=1, overall_timeout_s=30.0),
            ).research("Gate test")
        assert not any(s.action == "additional_search" for s in result.research_steps)
        assert any(s.action == "synthesize" for s in result.research_steps)


# -----------------------------------------------------------------------------
# 5. Fetch failures / timeouts
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestFetchFailures:
    def test_fetch_failure_marked_and_pipeline_continues(self):
        fake = FakeSynthesizer(gaps=[])
        with patch(_SEARCH_PATCH, return_value=_search_results(1)), \
             patch("research.pipeline.fetch_url", return_value=FetchResult(success=False, url="https://example.com/1", error="Timeout after 10s")), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract()):
            pipeline = _make_pipeline(fake, ResearchLimits(max_steps=4, max_searches=1, max_fetches=2))
            result = pipeline.research("Fetch fails")
        assert any(s.fetch_status == "failed" for s in result.sources)
        # Pipeline still reaches synthesis (graceful).
        assert result.synthesis != ""
        # With no successful sources, confidence should be 0 and synthesis notes lack of sources.
        assert result.confidence == 0.0

    def test_fetch_timeout_recorded(self):
        fake = FakeSynthesizer(gaps=[])
        with patch(_SEARCH_PATCH, return_value=_search_results(1)), \
             patch("research.pipeline.fetch_url", return_value=FetchResult(success=False, url="https://example.com/1", error="HTTP 504")), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract()):
            pipeline = _make_pipeline(fake, ResearchLimits(max_steps=4, max_searches=1, max_fetches=2))
            result = pipeline.research("Timeout test")
        failed = [s for s in result.sources if s.fetch_status == "failed"]
        assert failed
        assert "504" in failed[0].fetch_error


# -----------------------------------------------------------------------------
# 6. Synthesis with [Source N] citations
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestSynthesisCitations:
    def test_citations_match_fetched_sources(self):
        fake = FakeSynthesizer(
            gaps=[],
            synthesis="Paris is the capital of France [Source 1]. Another view [Source 2].",
            confidence=0.85,
        )
        with patch(_SEARCH_PATCH, return_value=_search_results(2)), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract()):
            pipeline = _make_pipeline(fake, ResearchLimits(max_steps=4, max_searches=1, max_fetches=2))
            result = pipeline.research("Capital of France?")
        citations = result.get_citations()
        assert len(citations) == 2
        assert "[Source 1]" in result.synthesis
        assert "[Source 2]" in result.synthesis
        assert abs(result.confidence - 0.85) < 1e-9
        # findings entry carries the citations.
        assert result.findings[0]["sources"] == citations

    def test_synthesis_preserves_url_attribution(self):
        fake = FakeSynthesizer(gaps=[], synthesis="Ref [Source 1].")
        with patch(_SEARCH_PATCH, return_value=_search_results(1, prefix="attr")), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract()):
            pipeline = _make_pipeline(fake, ResearchLimits(max_steps=4, max_searches=1, max_fetches=1))
            result = pipeline.research("Attr test")
        citations = result.get_citations()
        assert citations[0]["url"] == "https://attr.com/1"
        assert citations[0]["title"] == "https://attr.com/1" or citations[0]["title"] != ""


# -----------------------------------------------------------------------------
# 7. Provider failure / rate-limit graceful degradation
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestProviderFailureGraceful:
    def test_gap_identification_failure_does_not_crash(self):
        fake = FakeSynthesizer(raise_on_identify=True, rate_limited=True)
        with patch(_SEARCH_PATCH, return_value=_search_results(1)), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract()):
            pipeline = _make_pipeline(fake, ResearchLimits(max_steps=4, max_searches=1, max_fetches=1))
            result = pipeline.research("Provider down during gaps")
        # No exception leaked; research still completes.
        assert result is not None
        # Because gaps are empty (failure), no additional search loop.
        assert not any(s.action == "additional_search" for s in result.research_steps)
        # Synthesis still runs (degraded but present).
        assert result.synthesis != ""

    def test_synthesis_failure_does_not_crash(self):
        fake = FakeSynthesizer(raise_on_synthesize=True, rate_limited=True)
        with patch(_SEARCH_PATCH, return_value=_search_results(1)), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract()):
            pipeline = _make_pipeline(fake, ResearchLimits(max_steps=4, max_searches=1, max_fetches=1))
            result = pipeline.research("Provider down during synth")
        assert result is not None
        # Confidence collapses; no sources synthesized into text.
        assert result.confidence == 0.0
        # findings entry still records the citations that were available.
        assert "sources" in result.findings[0]


# -----------------------------------------------------------------------------
# 8. Empty / low-quality search results
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestEmptyLowQualityResults:
    def test_empty_search_results_graceful(self):
        fake = FakeSynthesizer(gaps=[])
        with patch(_SEARCH_PATCH, return_value=[]), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract()):
            pipeline = _make_pipeline(fake, ResearchLimits(max_steps=3, max_searches=1, max_fetches=1))
            result = pipeline.research("Obscure query xyz123")
        assert result is not None
        assert result.research_id
        # No sources -> identify_gaps short-circuits, synthesis yields no-source message.
        assert not any(s.fetch_status == "success" for s in result.sources)
        # With no usable sources, the synthesizer gets empty contexts and the
        # (fake) synthesizer collapses confidence to 0.0 to reflect no evidence.
        assert result.confidence == 0.0

    def test_low_quality_no_extracted_text_graceful(self):
        # Fetch succeeds but extraction yields nothing useful.
        fake = FakeSynthesizer(gaps=[])
        with patch(_SEARCH_PATCH, return_value=_search_results(1)), \
             patch("research.pipeline.fetch_url", return_value=_ok_fetch("https://example.com/1")), \
             patch("research.pipeline.extract_content",
                   return_value=ExtractionResult(success=False, error="extracted no content", method="none")):
            pipeline = _make_pipeline(fake, ResearchLimits(max_steps=4, max_searches=1, max_fetches=1))
            result = pipeline.research("Low quality")
        # No usable content -> no successful sources -> no crash.
        assert result is not None
        assert result.confidence == 0.0


# -----------------------------------------------------------------------------
# 9. Synthesizer seam plumbing
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestSynthesizerSeam:
    def test_default_synthesizer_is_llm(self):
        pipeline = ResearchPipeline(config=JarvisConfig())
        assert isinstance(pipeline.synthesizer, LLMResearchSynthesizer)

    def test_injected_synthesizer_used(self):
        fake = FakeSynthesizer(gaps=[])
        pipeline = _make_pipeline(fake, ResearchLimits(max_steps=2, max_searches=1, max_fetches=1))
        assert pipeline.synthesizer is fake

    def test_config_passed_to_llm_synthesizer(self):
        config = JarvisConfig()
        synth = LLMResearchSynthesizer(config)
        assert synth.config is config

    def test_run_research_default_synthesizer_offline(self):
        # run_research builds a default LLMResearchSynthesizer; mock the
        # provider to None so it exercises the no-LLM fallback path offline.
        with patch(_SEARCH_PATCH, return_value=_search_results(1)), \
             patch("research.pipeline.fetch_url", side_effect=lambda u: _ok_fetch(u)), \
             patch("research.pipeline.extract_content", side_effect=lambda c, url="": _ok_extract()), \
             patch("research.pipeline.get_llm_provider", return_value=None):
            result = run_research(
                "Capital?",
                config=JarvisConfig(),
                limits=ResearchLimits(max_steps=4, max_searches=1, max_fetches=1),
            )
            # run_research default synthesizer path still works (no crash).
            assert result is not None
