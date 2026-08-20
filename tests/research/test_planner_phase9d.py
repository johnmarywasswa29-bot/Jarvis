"""Phase 9D deterministic tests: research findings -> validated plan -> proposal.

These exercise the domain-agnostic planner WITHOUT a live LLM or internet by
injecting a deterministic ``FakePlanSynthesizer`` (and a real, offline
``ToolRegistry`` / ``PermissionManager`` built from ``JarvisConfig``).

All tests are ``@pytest.mark.offline``.

Covered:
  * research findings -> plan conversion
  * multi-step ordering / dependencies
  * tool validation (against live ToolRegistry)
  * invalid / unknown tool rejection (fail safe, no invention)
  * risk classification (from permission level)
  * confirmation requirements (from PermissionManager)
  * empty / insufficient research handling
  * planner / provider failure handling (raise, never invent)
  * plan determinism / schema validation
  * integration: research -> plan -> validated proposal
"""

from __future__ import annotations

import pytest

from research.planner import (
    PlanStep,
    PlanStatus,
    PlanValidationError,
    PlanSynthesizer,
    ResearchPlan,
    ResearchPlanner,
    plan_from_research,
)
from research.pipeline import ResearchFindings, ResearchSource
from modules.config import JarvisConfig
from modules.tools import ToolRegistry
from modules.permission_manager import PermissionManager
from proposal.manager import ProposalManager


# -----------------------------------------------------------------------------
# Shared fixtures / helpers
# -----------------------------------------------------------------------------
def _findings_with_sources(n: int = 2) -> ResearchFindings:
    sources = [
        ResearchSource(
            title=f"Source {i}",
            url=f"https://example.com/{i}",
            search_query="local llms",
            fetch_status="success",
            extracted_text=f"evidence {i}",
        )
        for i in range(1, n + 1)
    ]
    return ResearchFindings(
        query="Plan a comparison of local LLMs",
        sources=sources,
        synthesis="Synthesis with [Source 1] and [Source 2]." ,
        confidence=0.8,
    )


def _findings_empty() -> ResearchFindings:
    return ResearchFindings(query="Q", sources=[], synthesis="", confidence=0.0)


class FakePlanSynthesizer(PlanSynthesizer):
    """Returns a canned plan so planner behavior is deterministic offline."""

    def __init__(self, plan: ResearchPlan | None = None, raise_error: bool = False,
                 error_cls=PlanValidationError, error_msg: str = "synthesizer down"):
        self.plan = plan
        self.raise_error = raise_error
        self.error_cls = error_cls
        self.error_msg = error_msg
        self.calls = 0

    def synthesize_plan(self, findings, context=""):
        self.calls += 1
        if self.raise_error:
            raise self.error_cls(self.error_msg)
        return self.plan


def _planner(synth, config=None, tool_registry=None, permission_manager=None):
    return ResearchPlanner(
        config=config or JarvisConfig(),
        tool_registry=tool_registry or ToolRegistry(JarvisConfig()),
        permission_manager=permission_manager or PermissionManager(),
        synthesizer=synth,
    )


# -----------------------------------------------------------------------------
# 1. Findings -> plan conversion
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestFindingsToPlan:
    def test_finding_to_plan_maps_fields(self):
        plan_in = ResearchPlan(
            objective="Build a report",
            rationale="research supports it",
            steps=[
                PlanStep(step_id="s1", description="Search", tool="web_search",
                         parameters={"query": "local llm"}, expected_result="hits",
                         rationale="need data"),
            ],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert plan.objective == "Build a report"
        assert plan.rationale == "research supports it"
        assert len(plan.steps) == 1
        assert plan.steps[0].tool == "web_search"
        assert plan.steps[0].parameters == {"query": "local llm"}

    def test_plan_carries_source_citations(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[PlanStep(step_id="s1", tool="web_search", expected_result="x",
                            rationale="r")],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        # The synthesizer's plan had no sources; the planner injects citations
        # from the findings so the proposal can carry evidence references.
        assert plan.sources, "planner should attach research citations to the plan"


# -----------------------------------------------------------------------------
# 2. Multi-step ordering / dependencies
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestOrderingAndDependencies:
    def test_ordered_steps_preserved(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[
                PlanStep(step_id="s1", tool="web_search", expected_result="x", rationale="r"),
                PlanStep(step_id="s2", tool="calculator", dependencies=["s1"],
                         expected_result="y", rationale="r"),
                PlanStep(step_id="s3", tool="web_fetch", dependencies=["s2"],
                         expected_result="z", rationale="r"),
            ],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert [s.step_id for s in plan.steps] == ["s1", "s2", "s3"]
        assert plan.steps[1].dependencies == ["s1"]
        assert plan.steps[2].dependencies == ["s2"]

    def test_dependency_on_unknown_step_rejected(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[
                PlanStep(step_id="s1", tool="web_search", expected_result="x", rationale="r"),
                PlanStep(step_id="s2", tool="calculator", dependencies=["s99"],
                         expected_result="y", rationale="r"),
            ],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert plan.status == PlanStatus.REJECTED
        assert any("unknown step_id" in e for e in plan.validation_errors)

    def test_self_dependency_rejected(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[PlanStep(step_id="s1", tool="web_search", dependencies=["s1"],
                            expected_result="x", rationale="r")],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert plan.status == PlanStatus.REJECTED
        assert any("depends on itself" in e for e in plan.validation_errors)

    def test_duplicate_step_id_rejected(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[
                PlanStep(step_id="s1", tool="web_search", expected_result="x", rationale="r"),
                PlanStep(step_id="s1", tool="calculator", expected_result="y", rationale="r"),
            ],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert plan.status == PlanStatus.REJECTED
        assert any("duplicate step_id" in e for e in plan.validation_errors)


# -----------------------------------------------------------------------------
# 3. Tool validation / unknown tool rejection
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestToolValidation:
    def test_known_tools_accepted(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[
                PlanStep(step_id="s1", tool="web_search", expected_result="x", rationale="r"),
                PlanStep(step_id="s2", tool="web_fetch", expected_result="y", rationale="r"),
                PlanStep(step_id="s3", tool="calculator", expected_result="z", rationale="r"),
            ],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert plan.status == PlanStatus.VALIDATED

    def test_unknown_tool_rejected_fails_safe(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[PlanStep(step_id="s1", tool="time_travel_machine",
                            expected_result="x", rationale="r")],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert plan.status == PlanStatus.REJECTED
        assert any("unknown/unsupported tool" in e for e in plan.validation_errors)
        # The unsupported action is recorded, never silently executed/invented.
        assert plan.steps[0].tool == "time_travel_machine"

    def test_step_without_tool_rejected(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[PlanStep(step_id="s1", tool="", expected_result="x", rationale="r")],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert plan.status == PlanStatus.REJECTED

    def test_validation_uses_live_registry_names(self):
        reg = ToolRegistry(JarvisConfig())
        assert reg.has_tool("web_search")
        assert not reg.has_tool("does_not_exist_xyz")
        assert reg.get_tool("web_search") is not None
        assert reg.get_tool("does_not_exist_xyz") is None


# -----------------------------------------------------------------------------
# 4. Risk classification
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestRiskClassification:
    def test_safe_tool_low_risk(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[PlanStep(step_id="s1", tool="calculator", expected_result="x", rationale="r")],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert plan.steps[0].risk_level == "low"
        # overall plan risk = max step risk
        assert plan.risk_level == "low"

    def test_caution_tool_medium_risk(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[PlanStep(step_id="s1", tool="web_search", expected_result="x", rationale="r")],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert plan.steps[0].risk_level == "medium"

    def test_dangerous_tool_high_risk(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[PlanStep(step_id="s1", tool="code_execution", expected_result="x", rationale="r")],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert plan.steps[0].risk_level == "high"
        assert plan.risk_level == "high"

    def test_overall_risk_is_max(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[
                PlanStep(step_id="s1", tool="calculator", expected_result="x", rationale="r"),
                PlanStep(step_id="s2", tool="code_execution", expected_result="y", rationale="r"),
            ],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert plan.risk_level == "high"


# -----------------------------------------------------------------------------
# 5. Confirmation requirements
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestConfirmationRequirements:
    def test_safe_tool_no_confirmation(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[PlanStep(step_id="s1", tool="calculator", expected_result="x", rationale="r")],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert plan.steps[0].confirmation_requirement is False

    def test_caution_tool_requires_confirmation(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[PlanStep(step_id="s1", tool="web_search", expected_result="x", rationale="r")],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert plan.steps[0].confirmation_requirement is True

    def test_dangerous_tool_requires_confirmation(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[PlanStep(step_id="s1", tool="terminal", expected_result="x", rationale="r")],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert plan.steps[0].confirmation_requirement is True


# -----------------------------------------------------------------------------
# 6. Empty / insufficient research handling
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestEmptyInsufficientResearch:
    def test_empty_findings_proposal_fails_safe(self):
        # The production LLM synthesizer refuses to plan from zero sources.
        from research.planner import LLMResearchPlanSynthesizer
        with pytest.raises(PlanValidationError):
            LLMResearchPlanSynthesizer(JarvisConfig()).synthesize_plan(_findings_empty())

    def test_planner_raises_on_synth_error(self):
        planner = _planner(FakePlanSynthesizer(raise_error=True))
        with pytest.raises(PlanValidationError):
            planner.plan(_findings_with_sources())

    def test_no_objective_rejected(self):
        plan_in = ResearchPlan(
            objective="",
            steps=[PlanStep(step_id="s1", tool="web_search", expected_result="x", rationale="r")],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert plan.status == PlanStatus.REJECTED
        assert any("objective is empty" in e for e in plan.validation_errors)


# -----------------------------------------------------------------------------
# 7. Failure handling (provider down, never invent)
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestFailureHandling:
    def test_synth_exception_propagates_as_plan_error(self):
        planner = _planner(FakePlanSynthesizer(raise_error=True))
        with pytest.raises(PlanValidationError):
            planner.plan(_findings_with_sources())

    def test_broken_synth_returns_nonsense_rejected_by_validation(self):
        # Even if a synthesizer returns a step with an unknown tool, validation
        # rejects it instead of letting it become executable.
        plan_in = ResearchPlan(
            objective="O",
            steps=[PlanStep(step_id="s1", tool="magic_wand", expected_result="x", rationale="r")],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert plan.status == PlanStatus.REJECTED
        # Crucially, no proposal is created from an invalid plan.
        with pytest.raises(PlanValidationError):
            planner.to_proposal(plan)


# -----------------------------------------------------------------------------
# 8. Determinism / schema validation
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestDeterminismSchema:
    def test_same_inputs_same_validated_plan(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[
                PlanStep(step_id="s1", tool="web_search", expected_result="x", rationale="r"),
                PlanStep(step_id="s2", tool="calculator", dependencies=["s1"],
                         expected_result="y", rationale="r"),
            ],
        )
        p1 = _planner(FakePlanSynthesizer(plan_in)).plan(_findings_with_sources())
        p2 = _planner(FakePlanSynthesizer(plan_in)).plan(_findings_with_sources())
        assert p1.status == p2.status == PlanStatus.VALIDATED
        assert [s.tool for s in p1.steps] == [s.tool for s in p2.steps]
        assert p1.risk_level == p2.risk_level

    def test_to_dict_roundtrips_fields(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[PlanStep(step_id="s1", tool="web_search", parameters={"q": "x"},
                            expected_result="y", rationale="r")],
        )
        plan = _planner(FakePlanSynthesizer(plan_in)).plan(_findings_with_sources())
        d = plan.to_dict()
        assert d["objective"] == "O"
        assert d["steps"][0]["tool"] == "web_search"
        assert d["steps"][0]["parameters"] == {"q": "x"}
        assert d["status"] == "validated"


# -----------------------------------------------------------------------------
# 9. Integration: research -> plan -> validated proposal
# -----------------------------------------------------------------------------
@pytest.mark.offline
class TestIntegrationResearchToProposal:
    def test_research_to_plan_to_proposal(self):
        plan_in = ResearchPlan(
            objective="Produce a local-LLM comparison report",
            rationale="research gathered sources on local LLMs",
            steps=[
                PlanStep(step_id="s1", description="Gather benchmarks",
                         tool="web_search", parameters={"query": "local llm benchmark"},
                         expected_result="benchmark list", rationale="baseline"),
                PlanStep(step_id="s2", description="Summarize findings",
                         tool="calculator", dependencies=["s1"],
                         expected_result="summary", rationale="aggregate"),
            ],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert plan.status == PlanStatus.VALIDATED

        proposal = planner.to_proposal(plan)
        # Reuses the EXISTING ProposalManager + ProposalValidator.
        assert proposal.status.value == "validated"
        assert len(proposal.proposed_actions) == 2
        assert proposal.proposed_actions[0].tool == "web_search"
        # Sources carried from research citations.
        assert len(proposal.source_references) >= 1

    def test_invalid_plan_cannot_become_proposal(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[PlanStep(step_id="s1", tool="unknown_tool", expected_result="x", rationale="r")],
        )
        planner = _planner(FakePlanSynthesizer(plan_in))
        plan = planner.plan(_findings_with_sources())
        assert plan.status == PlanStatus.REJECTED
        with pytest.raises(PlanValidationError):
            planner.to_proposal(plan)

    def test_plan_from_research_helper(self):
        plan_in = ResearchPlan(
            objective="O",
            steps=[PlanStep(step_id="s1", tool="web_search", expected_result="x", rationale="r")],
        )
        plan = plan_from_research(_findings_with_sources(), synthesizer=FakePlanSynthesizer(plan_in))
        assert plan.status == PlanStatus.VALIDATED
        assert len(plan.steps) == 1
