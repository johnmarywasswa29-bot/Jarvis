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
    ResearchPlanner,
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


class _CapturingFakeLLM:
    """Records the exact prompt (user message) sent to the LLM."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.captured_messages = None

    def is_available(self) -> bool:
        return True

    def chat(self, messages, *, stream=False, **kwargs):  # noqa: ANN001, ANN003
        self.captured_messages = messages
        return self._response


class _CapturingSynthesizer(LLMResearchPlanSynthesizer):
    """Injects a capturing LLM so we can inspect the generated prompt."""

    def __init__(self, response: str) -> None:
        super().__init__()
        self._fake = _CapturingFakeLLM(response)

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


@pytest.mark.offline
class TestToolNameAdherence:
    """Phase M: the model must emit EXACT registered tool IDs, not labels."""

    def _plan_with_tool(self, tool_value: str):
        from research.planner import ResearchPlanner

        plan_json = (
            '{"objective": "Calculate 127 x 43",'
            ' "rationale": "research",'
            ' "steps": [{"step_id": "s1", "description": "multiply",'
            f' "tool": "{tool_value}", "action": "", "parameters": {{}},'
            ' "dependencies": [], "expected_result": "5461",'
            ' "risk_level": "low", "confirmation_requirement": true,'
            ' "rationale": "compute"}]}'
        )
        planner = ResearchPlanner(synthesizer=_FakeSynthesizer(plan_json))
        return planner.plan(_findings_with_sources())

    def test_exact_registered_id_calculator_accepted(self):
        plan = self._plan_with_tool("calculator")
        assert plan.status.value == "validated"
        assert plan.steps[0].tool == "calculator"

    def test_display_name_online_calculator_rejected(self):
        # The real blocker from the acceptance test: 'Online Calculator'.
        plan = self._plan_with_tool("Online Calculator")
        assert plan.status.value == "rejected"
        assert any("unknown/unsupported tool" in e for e in plan.validation_errors)

    def test_synonym_calculator_tool_rejected(self):
        plan = self._plan_with_tool("Calculator Tool")
        assert plan.status.value == "rejected"

    def test_arbitrary_unknown_tool_rejected(self):
        plan = self._plan_with_tool("math calculator")
        assert plan.status.value == "rejected"
        assert any("unknown/unsupported tool" in e for e in plan.validation_errors)

    def test_no_execution_when_tool_rejected(self):
        # Validation rejection must never reach execution; the plan is simply
        # not validated, so it can never become a proposal/execution.
        plan = self._plan_with_tool("Online Calculator")
        assert plan.status.value == "rejected"
        assert plan.steps[0].tool == "Online Calculator"  # stored as given
        assert plan.sources  # research citations still attached, no execution

    def _captured_prompt(self) -> str:
        from research.planner import ResearchPlanner

        synth = _CapturingSynthesizer(
            '{"objective": "Calculate 127 x 43", "rationale": "r", '
            '"steps": [{"step_id": "s1", "description": "d", "tool": "calculator", '
            '"action": "", "parameters": {}, "dependencies": [], '
            '"expected_result": "5461", "risk_level": "low", '
            '"confirmation_requirement": true, "rationale": "x"}]}'
        )
        planner = ResearchPlanner(synthesizer=synth)
        planner.plan(_findings_with_sources())
        # The user message (index 1) holds the full planner prompt.
        messages = synth._fake.captured_messages
        return " ".join(m.get("content", "") for m in messages)

    def test_prompt_contains_exact_registered_tool_id(self):
        prompt = self._captured_prompt()
        assert "calculator" in prompt  # the exact registered ID is present

    def test_prompt_contains_valid_calculator_example(self):
        prompt = self._captured_prompt()
        assert '{"tool": "calculator"}' in prompt  # correct few-shot example

    def test_prompt_contains_invalid_online_calculator_example(self):
        prompt = self._captured_prompt()
        # The forbidden free-form name must be shown as the INCORRECT example.
        assert "Online Calculator" in prompt

    def test_prompt_forbids_freform_and_lists_allowed_ids(self):
        prompt = self._captured_prompt()
        assert "ALLOWED TOOL IDS" in prompt
        assert "EXAMPLES of the 'tool' field" in prompt
        # It must instruct the model to copy an exact ID, not a display name.
        assert "EXACT registered tool ID" in prompt or "exact registered" in prompt


@pytest.mark.offline
class TestPlannerCallCountAndSafety:
    """Phase Q: pin the single-call planner invariant + safety gates.

    These use fast fakes (no real LLM / no real execution) to document that:
      * a valid first plan triggers exactly ONE planner LLM (synthesizer) call;
      * an invalid first plan still fails validation (replan preserved);
      * the validated plan still passes ProposalValidator;
      * the confirmation gate remains required;
      * DENY => no execution; APPROVE => calculator step dispatched.
    """

    def _valid_plan_json(self, tool="calculator"):
        return (
            '{"objective": "Calculate 127 x 43", "rationale": "r",'
            ' "steps": [{"step_id": "s1", "description": "d", "tool": "%s",'
            ' "action": "", "parameters": {}, "dependencies": [],'
            ' "expected_result": "5461", "risk_level": "low",'
            ' "confirmation_requirement": true, "rationale": "x"}]}' % tool
        )

    def test_valid_first_plan_uses_exactly_one_synthesizer_call(self):
        # Counting fake: records how many times the LLM synthesizer is invoked.
        calls = []

        class CountingSynth(_FakeSynthesizer):
            def synthesize_plan(self, findings, context=""):
                calls.append(1)
                return super().synthesize_plan(findings, context=context)

        planner = ResearchPlanner(synthesizer=CountingSynth(self._valid_plan_json()))
        plan = planner.plan(_findings_with_sources())
        assert plan.status.value == "validated"
        assert len(calls) == 1  # exactly ONE synthesizer/LLM call on valid path

    def test_invalid_first_plan_still_fails_validation(self):
        # The planner must NOT silently 'fix' an invalid plan; validation must
        # fire (status REJECTED). Raising/aborting is the AgentLoop's replan
        # responsibility, which is preserved (unchanged).
        planner = ResearchPlanner(
            synthesizer=_FakeSynthesizer(self._valid_plan_json("Online Calculator"))
        )
        plan = planner.plan(_findings_with_sources())
        assert plan.status.value == "rejected"  # validation fired, no invention
        assert any("unknown/unsupported tool" in e for e in plan.validation_errors)

    def test_valid_calculator_plan_passes_proposal_validator(self):
        planner = ResearchPlanner(
            synthesizer=_FakeSynthesizer(self._valid_plan_json("calculator"))
        )
        plan = planner.plan(_findings_with_sources())
        proposal = planner.to_proposal(plan)
        assert proposal.status.value == "validated"  # ProposalValidator reused

    def test_confirmation_gate_still_required(self):
        from modules.permission_manager import PermissionManager

        planner = ResearchPlanner(
            synthesizer=_FakeSynthesizer(self._valid_plan_json("calculator"))
        )
        plan = planner.plan(_findings_with_sources())
        # The per-step confirmation requirement is DERIVED from the tool's
        # permission level (not bypassed). calculator is SAFE, so the gate is
        # correctly False here; the gate still engages for CAUTION/DANGEROUS
        # tools via the same unchanged PermissionManager path.
        expected = PermissionManager().requires_confirmation("calculator")
        assert plan.steps[0].confirmation_requirement is expected
        # The gate mechanism is preserved: a DANGEROUS tool would require it.
        assert PermissionManager().requires_confirmation("shell") is True

    def test_deny_means_no_execution(self):
        from research.orchestrator import Decision, ResearchWorkflow, UserDecider

        class DenyDecider(UserDecider):
            def decide(self, objective, plan, proposal):
                return Decision.DENY

        class RecordingExecutor:
            def __init__(self):
                self.calls = []

            def execute(self, proposal, **kwargs):
                self.calls.append(proposal)
                raise AssertionError("executor must not run on DENY")

        planner = ResearchPlanner(
            synthesizer=_FakeSynthesizer(self._valid_plan_json("calculator"))
        )
        wf = ResearchWorkflow(
            planner=planner,
            executor=RecordingExecutor(),
            decider=DenyDecider(),
        )
        audit = wf.run("Calculate 127 x 43 and tell me the result.", research=False,
                       findings=_findings_with_sources())
        # No execution occurred (executor never called).
        assert len(wf.executor.calls) == 0
        assert audit.final_status in ("denied", "aborted")

    def test_approve_dispatches_calculator_step(self):
        from research.orchestrator import Decision, ResearchWorkflow, UserDecider

        captured = []

        class ApproveDecider(UserDecider):
            def decide(self, objective, plan, proposal):
                return Decision.ACCEPT

        class _Audit:
            final_status = "success"
            metadata = {}
            executed_steps = []

        class FakeExecutor:
            def execute(self, proposal, **kwargs):
                captured.append([a.tool for a in proposal.proposed_actions])
                return _Audit()

        planner = ResearchPlanner(
            synthesizer=_FakeSynthesizer(self._valid_plan_json("calculator"))
        )
        wf = ResearchWorkflow(
            planner=planner,
            executor=FakeExecutor(),
            decider=ApproveDecider(),
        )
        wf.run("Calculate 127 x 43 and tell me the result.", research=False,
               findings=_findings_with_sources())
        assert captured and captured[0] == ["calculator"]
