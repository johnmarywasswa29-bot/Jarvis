"""Focused tests for the planner JSON-extraction robustness (Phase L, #A).

These exercise the full ``LLMResearchPlanSynthesizer.synthesize_plan`` path
offline by injecting a fake LLM whose ``chat`` returns a configured string, so
the real extraction/validation logic is tested without touching the network.
"""

from __future__ import annotations

import pytest

from research.pipeline import ResearchFindings, ResearchSource
from research.planner import (
    LLMResearchPlanSynthesizer,
    PlanValidationError,
    _extract_json_object,
)


def _findings_with_sources() -> ResearchFindings:
    """A findings object whose citations pass get_citations()."""
    f = ResearchFindings(query="Calculate 127 x 43")
    f.sources.append(
        ResearchSource(
            title="Calculator.net",
            url="https://www.calculator.net",
            search_query="Calculate 127 x 43",
            fetch_status="success",
            extracted_text="127 * 43 = 5461.",
        )
    )
    f.synthesis = "127 * 43 = 5461."
    return f


class _FakeLLM:
    """Minimal LLM stand-in: returns a fixed chat response."""

    def __init__(self, response: str) -> None:
        self._response = response

    def is_available(self) -> bool:
        return True

    def chat(self, messages, *, stream=False, **kwargs):  # noqa: ANN001, ANN003
        return self._response


class _FakeSynthesizer(LLMResearchPlanSynthesizer):
    """Injects a fake LLM so synthesize_plan runs without Ollama."""

    def __init__(self, response: str) -> None:
        super().__init__()
        self._fake = _FakeLLM(response)

    def _get_llm(self):  # noqa: ANN204
        return self._fake


# ---------------------------------------------------------------------------
# Unit tests for the extraction helper itself
# ---------------------------------------------------------------------------

@pytest.mark.offline
class TestExtractJsonObject:
    def test_pure_json(self):
        out = _extract_json_object('{"a": 1}')
        assert out == '{"a": 1}'

    def test_prose_preamble(self):
        raw = 'Here is the JSON plan:\n\n{"objective": "x", "steps": []}'
        out = _extract_json_object(raw)
        assert out == '{"objective": "x", "steps": []}'

    def test_leading_and_trailing_fence(self):
        raw = '```json\n{"a": 1}\n```'
        assert _extract_json_object(raw) == '{"a": 1}'

    def test_fence_without_language_tag(self):
        raw = '```\n{"a": 1}\n```'
        assert _extract_json_object(raw) == '{"a": 1}'

    def test_whitespace_only(self):
        assert _extract_json_object("   \n  ") is None

    def test_empty(self):
        assert _extract_json_object("") is None

    def test_none(self):
        assert _extract_json_object(None) is None

    def test_no_object_present(self):
        assert _extract_json_object("no json here at all") is None

    def test_truncated_object_returns_none(self):
        # A truncated object has no closing '}' -> no valid span -> None, and
        # the caller fails safe (never returns a partial/invented plan).
        assert _extract_json_object('{"a": 1') is None


# ---------------------------------------------------------------------------
# End-to-end synthesize_plan behavior (offline, fake LLM)
# ---------------------------------------------------------------------------

VALID_PLAN = (
    '{"objective": "Calculate 127 x 43",'
    ' "rationale": "research shows 5461",'
    ' "steps": [{"step_id": "s1", "description": "multiply",'
    ' "tool": "calculator", "action": "", "parameters": {},'
    ' "dependencies": [], "expected_result": "5461",'
    ' "risk_level": "low", "confirmation_requirement": true,'
    ' "rationale": "compute"}]}'
)


@pytest.mark.offline
class TestSynthesizePlanExtraction:
    def test_pure_valid_json_parses(self):
        plan = _FakeSynthesizer(VALID_PLAN).synthesize_plan(_findings_with_sources())
        assert plan.objective == "Calculate 127 x 43"
        assert len(plan.steps) == 1

    def test_prose_preamble_parses(self):
        raw = "Here is the JSON plan to achieve the objective:\n\n" + VALID_PLAN
        plan = _FakeSynthesizer(raw).synthesize_plan(_findings_with_sources())
        assert plan.objective == "Calculate 127 x 43"

    def test_fenced_json_parses(self):
        raw = "```json\n" + VALID_PLAN + "\n```"
        plan = _FakeSynthesizer(raw).synthesize_plan(_findings_with_sources())
        assert plan.objective == "Calculate 127 x 43"

    def test_whitespace_response_fails_cleanly(self):
        with pytest.raises(PlanValidationError):
            _FakeSynthesizer("   \n  ").synthesize_plan(_findings_with_sources())

    def test_empty_response_fails_cleanly(self):
        with pytest.raises(PlanValidationError):
            _FakeSynthesizer("").synthesize_plan(_findings_with_sources())

    def test_truncated_json_fails_safely(self):
        # Never produces a partial/invented plan; the parse must raise.
        with pytest.raises(PlanValidationError):
            _FakeSynthesizer(VALID_PLAN[:-10]).synthesize_plan(_findings_with_sources())

    def test_malformed_json_fails_safely(self):
        bad = '{"objective": "x", "steps": [ this is not json }'
        with pytest.raises(PlanValidationError):
            _FakeSynthesizer(bad).synthesize_plan(_findings_with_sources())

    def test_missing_required_fields_rejected(self):
        # Valid JSON but an EMPTY objective -> the planner's schema validation
        # marks the plan REJECTED (it raises at to_proposal(); plan() surfaces
        # the rejection via status, so we do not weaken validation to pass).
        from research.planner import ResearchPlanner, PlanStatus

        incomplete = '{"objective": "", "steps": []}'
        planner = ResearchPlanner(synthesizer=_FakeSynthesizer(incomplete))
        plan = planner.plan(_findings_with_sources())
        assert plan.status == PlanStatus.REJECTED
        assert plan.validation_errors  # non-empty: schema validation fired

    def test_existing_valid_behavior_unchanged(self):
        # The happy path yields the same structured plan as before.
        plan = _FakeSynthesizer(VALID_PLAN).synthesize_plan(_findings_with_sources())
        assert plan.status.value == "draft"
        assert plan.steps[0].tool == "calculator"
        assert plan.steps[0].confirmation_requirement is True
