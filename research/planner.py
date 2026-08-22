"""Domain-agnostic research -> plan conversion (Phase 9D).

This module turns a :class:`~research.pipeline.ResearchFindings` object into a
structured, *validated* :class:`ResearchPlan` and, from there, into a validated
:class:`~proposal.state.Proposal`.

Design principles (per Phase 9D requirements):
  * Builds ON the committed Phase 9A/9B/9C research pipeline; research and
    planning are kept SEPARATE so research remains independently usable.
  * REUSES the existing ``ToolRegistry``, ``PermissionManager``,
    ``ProposalManager`` and ``ProposalValidator``. None of those are replaced.
  * Does NOT execute any consequential action. Plans are produced and validated
    only; execution is deliberately out of scope for this phase.
  * Unknown / unsupported actions FAIL SAFE (rejected with a validation error)
    rather than being invented.
  * A plan is validated (tool, dependency, schema) before it can become an
    executable proposal.
  * The LLM-dependent step is behind a dependency-injection seam
    (:class:`PlanSynthesizer) so the planner is fully exercisable offline with a
    deterministic :class:`FakePlanSynthesizer` in tests. The production
    implementation wraps the EXISTING ``get_llm_provider`` architecture and is
    not modified.
  * No LangGraph, no new external providers, no changes to LLM providers.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from modules.config import JarvisConfig
from modules.llm_providers import get_llm_provider, LLMProvider
from modules.permission_manager import PermissionManager
from modules.tools import ToolRegistry
from proposal.manager import ProposalManager
from proposal.state import Proposal, ProposalRiskLevel, ProposedAction, SourceReference
from research.pipeline import ResearchFindings

logger = logging.getLogger("research.planner")


def _extract_json_object(raw: str) -> Optional[str]:
    """Pull a single JSON object out of an LLM response, or return None.

    Handles the shapes local models actually emit:
      * pure JSON,
      * a natural-language preamble followed by the JSON object,
      * the object wrapped in leading/trailing markdown ``` (or ```json) fences.

    The extraction is strict about *location* only: it returns the substring
    from the first '{' to the last '}'. It does NOT repair, complete, or invent
    anything — if that span is truncated or malformed, the caller's json.loads
    will raise and fail safe. Returns None when no object is present (e.g. an
    empty or whitespace-only response), so the caller can produce a clean
    failure instead of feeding whitespace to json.loads.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None

    # Strip a single leading/trailing markdown code fence (with optional
    # language tag such as ```json). Only one fence on each side is removed.
    if text.startswith("```"):
        text = text[3:]
        # Drop an optional language identifier on that first line.
        newline = text.find("\n")
        if newline != -1 and not text[:newline].strip():
            text = text[newline + 1:]
        elif text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    if text.endswith("```"):
        text = text[:-3].strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    return text[start : end + 1]


# =============================================================================
# Plan model
# =============================================================================
class PlanStatus(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    REJECTED = "rejected"


@dataclass
class PlanStep:
    """A single ordered step in a research-derived plan.

    Every field required by the Phase 9D spec is present:
      * tool / action        -> ``tool`` (a registered ToolRegistry name)
      * parameters            -> ``parameters``
      * dependencies          -> ``dependencies`` (list of step_id strings)
      * expected result       -> ``expected_result``
      * risk level            -> ``risk_level`` (low|medium|high)
      * confirmation required -> ``confirmation_requirement`` (bool)
      * rationale             -> ``rationale``
    """

    step_id: str
    description: str = ""
    tool: str = ""
    action: str = ""  # optional sub-action within the tool (e.g. filesystem:write)
    parameters: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    expected_result: str = ""
    risk_level: str = "medium"  # "low" | "medium" | "high"
    confirmation_requirement: bool = True
    rationale: str = ""


@dataclass
class ResearchPlan:
    """A validated, structured plan derived from research findings."""

    objective: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    rationale: str = ""
    risk_level: str = "medium"  # overall plan risk (max of step risks)
    status: PlanStatus = PlanStatus.DRAFT
    validation_errors: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)  # citation metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "steps": [s.__dict__ for s in self.steps],
            "rationale": self.rationale,
            "risk_level": self.risk_level,
            "status": self.status.value,
            "validation_errors": self.validation_errors,
            "sources": self.sources,
            "metadata": self.metadata,
        }


class PlanValidationError(Exception):
    """Raised when a plan cannot be built or validated safely."""


# =============================================================================
# Synthesizer abstraction (dependency-injection seam, mirrors Phase 9B)
# =============================================================================
class PlanSynthesizer:
    """Abstraction for the LLM-dependent plan generation step.

    Implemented by :class:`LLMResearchPlanSynthesizer` in production (which
    wraps ``get_llm_provider`` unchanged) and by a deterministic fake in tests.
    This seam lets Phase 9D exercise findings->plan conversion offline.
    """

    def synthesize_plan(
        self, findings: ResearchFindings, context: str = ""
    ) -> ResearchPlan:
        raise NotImplementedError


class LLMResearchPlanSynthesizer(PlanSynthesizer):
    """Production planner synthesizer: delegates to the existing LLM provider.

    Faithful to the Phase 9A/9B pattern: it asks the LLM for a structured plan
    as JSON, parses it, and on ANY failure (no provider, parse error, empty
    research) raises :class:`PlanValidationError` so the planner fails safe and
    never invents unsupported actions.
    """

    def __init__(
        self,
        config: Optional[JarvisConfig] = None,
        tool_registry: Optional[ToolRegistry] = None,
    ) -> None:
        self.config = config or JarvisConfig()
        self._llm_provider: Optional[LLMProvider] = None
        # Exact registered tool IDs, surfaced verbatim to the model so it must
        # emit an exact ID (e.g. "calculator"), never a display name or synonym
        # (e.g. "Online Calculator"). Validation still rejects anything not in
        # this list — this only improves the model's adherence.
        if tool_registry is not None:
            self._tool_ids = list(tool_registry.tool_names())
        else:
            try:
                self._tool_ids = list(ToolRegistry(self.config).tool_names())
            except Exception:  # pragma: no cover - defensive
                self._tool_ids = []

    def _tool_directive(self) -> str:
        """Emit an explicit, enumerable list of the EXACT registered tool IDs.

        The model is told these are the ONLY valid values for a step's 'tool'
        field. This is the lowest-risk adherence fix: we surface the canonical
        IDs the validator already enforces, and forbid display names/synonyms.
        No fuzzy matching, no aliases, no weakening of validation.
        """
        if not self._tool_ids:
            return ""
        listed = "\n".join(f"  - {tid}" for tid in self._tool_ids)
        return (
            "ALLOWED TOOL IDS (use EXACTLY one of these as the 'tool' value; "
            "do not paraphrase):\n"
            f"{listed}\n\n"
        )

    def _tool_fewshot(self) -> str:
        """Concise few-shot examples of the REQUIRED 'tool' field format.

        Shows the model a correct example (exact registry ID) and an incorrect
        one (a free-form display name) so it learns the value must be the
        exact registered ID, not a human-readable label. Pure prompt guidance
        only — validation still rejects anything not in the registry, so this
        adds no fuzzy matching, aliases, or normalization.
        """
        return (
            "EXAMPLES of the 'tool' field (value MUST be an exact registered "
            "ID from the list above):\n"
            '  CORRECT:   {"tool": "calculator"}\n'
            '  INCORRECT: {"tool": "Online Calculator"}\n'
            '  INCORRECT: {"tool": "Calculator Tool"}\n'
            '  INCORRECT: {"tool": "math calculator"}\n'
            "Only the CORRECT form is accepted; any other wording is rejected.\n\n"
        )

    def _get_llm(self) -> Optional[LLMProvider]:
        if self._llm_provider is None:
            try:
                self._llm_provider = get_llm_provider(self.config)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("Failed to initialize LLM provider: %s", e)
                self._llm_provider = None
        return self._llm_provider

    def synthesize_plan(
        self, findings: ResearchFindings, context: str = ""
    ) -> ResearchPlan:
        citations = findings.get_citations()
        if not citations:
            # Fail safe: do not invent a plan from no evidence.
            raise PlanValidationError(
                "Insufficient research: no successfully fetched sources to plan from."
            )

        context_blob = "\n\n".join(
            f"[Source {c['index']}] {c['title']} ({c['url']})" for c in citations
        )
        synthesis_excerpt = findings.synthesis[:4000]
        prompt = (
            "You are a planning assistant. Using ONLY the research findings below, "
            "produce an actionable, ordered plan to achieve the user's objective.\n\n"
            f"OBJECTIVE: {findings.query}\n\n"
            f"RESEARCH SYNTHESIS:\n{synthesis_excerpt}\n\n"
            f"SOURCES:\n{context_blob}\n\n"
            "Return a single JSON object with this exact shape and nothing else:\n"
            "{\n"
            '  "objective": "<restated objective>",\n'
            '  "rationale": "<why this plan follows from the research>",\n'
            '  "steps": [\n'
            "    {\n"
            '      "step_id": "s1",\n'
            '      "description": "<what to do>",\n'
            '      "tool": "<the EXACT registered tool ID from the allowed list below>",\n'
            '      "action": "<optional sub-action, e.g. filesystem:write>",\n'
            '      "parameters": {"key": "value"},\n'
            '      "dependencies": ["<step_id this depends on, or empty>"],\n'
            '      "expected_result": "<what success looks like>",\n'
            '      "risk_level": "low|medium|high",\n'
            '      "confirmation_requirement": true,\n'
            '      "rationale": "<why this step>"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            + self._tool_directive()
            + self._tool_fewshot()
            + "Rules: every step's 'tool' MUST be the EXACT registered tool ID, copied "
            "verbatim from the allowed list above — never a display name, description, "
            "synonym, or natural-language label. For example, use \"calculator\", not "
            "\"Online Calculator\" or \"Calculator Tool\". Do NOT invent tools or "
            "actions. Do NOT include steps that execute consequential changes unless the "
            "research clearly supports them. Dependencies must reference step_ids defined "
            "in the same plan."
        )

        llm = self._get_llm()
        if not llm or not llm.is_available():
            raise PlanValidationError(
                "LLM provider unavailable; cannot synthesize plan safely."
            )

        try:
            response = llm.chat([
                {"role": "system", "content": "You output only valid JSON plans."},
                {"role": "user", "content": prompt},
            ], stream=False)
        except Exception as e:
            raise PlanValidationError(f"Plan synthesis LLM call failed: {e}") from e

        if not response:
            raise PlanValidationError("Plan synthesis returned an empty response.")

        # Best-effort JSON extraction. Local models (e.g. llama3) frequently
        # prepend a natural-language preamble ("Here is the JSON plan:") or wrap
        # the object in markdown code fences — sometimes both. We therefore:
        #   1. strip any leading/trailing ``` fences (with optional language tag),
        #   2. locate the JSON object span (first '{' … last '}'),
        #   3. parse ONLY that span.
        # We never repair, guess, or invent missing JSON: if the object is
        # truncated or malformed, json.loads raises and we fail safe.
        text = _extract_json_object(response)
        if text is None:
            raise PlanValidationError(
                "Plan synthesis returned no JSON object to parse."
            )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise PlanValidationError(f"Plan synthesis produced invalid JSON: {e}") from e

        plan = ResearchPlan(
            objective=str(data.get("objective", findings.query)),
            rationale=str(data.get("rationale", "")),
            sources=citations,
            metadata={"synthesizer": "llm"},
        )
        for raw in data.get("steps", []):
            plan.steps.append(PlanStep(
                step_id=str(raw.get("step_id", f"s{len(plan.steps) + 1}")),
                description=str(raw.get("description", "")),
                tool=str(raw.get("tool", "")),
                action=str(raw.get("action", "")),
                parameters=dict(raw.get("parameters", {}) or {}),
                dependencies=list(raw.get("dependencies", []) or []),
                expected_result=str(raw.get("expected_result", "")),
                risk_level=str(raw.get("risk_level", "medium")).lower(),
                confirmation_requirement=bool(raw.get("confirmation_requirement", True)),
                rationale=str(raw.get("rationale", "")),
            ))
        return plan


# =============================================================================
# Planner: findings -> validated plan -> proposal
# =============================================================================
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


class ResearchPlanner:
    """Converts research findings into a validated, structured plan.

    The planner:
      1. Delegates the LLM-dependent generation to a ``PlanSynthesizer``
         (dependency-injected; deterministic fake in tests).
      2. Validates the produced plan against the live ``ToolRegistry`` (unknown
         tools are rejected, not invented) and ``PermissionManager`` (risk level
         and confirmation requirement are derived from the tool's permission
         level).
      3. Validates step ordering/dependencies (references resolve, no cycles,
         no self-dependency).
      4. Exposes ``to_proposal()`` which routes the validated plan through the
         EXISTING ``ProposalManager`` / ``ProposalValidator`` so a plan becomes
         an executable proposal ONLY after validation.
    """

    def __init__(
        self,
        config: Optional[JarvisConfig] = None,
        tool_registry: Optional[ToolRegistry] = None,
        permission_manager: Optional[PermissionManager] = None,
        synthesizer: Optional[PlanSynthesizer] = None,
        proposal_manager: Optional[ProposalManager] = None,
    ) -> None:
        self.config = config or JarvisConfig()
        self.tool_registry = tool_registry or ToolRegistry(self.config)
        self.permission_manager = permission_manager or PermissionManager()
        self.synthesizer = synthesizer or LLMResearchPlanSynthesizer(
            self.config, tool_registry=self.tool_registry
        )
        self.proposal_manager = proposal_manager or ProposalManager()

    # ------------------------------------------------------------------ public
    def plan(self, findings: ResearchFindings, context: str = "") -> ResearchPlan:
        """Build and validate a plan from research findings.

        Raises :class:`PlanValidationError` if synthesis or hard validation
        fails. Never silently invents unsupported actions.
        """
        try:
            plan = self.synthesizer.synthesize_plan(findings, context=context)
        except PlanValidationError:
            raise
        except Exception as e:  # synthesizer threw something unexpected
            raise PlanValidationError(f"Plan synthesis failed: {e}") from e

        # Attach research citations as the evidence provenance for the plan.
        # The planner is authoritative for where a plan's evidence comes from,
        # so this holds regardless of what the synthesizer returned.
        plan.sources = findings.get_citations()
        self._enrich_and_validate(plan)
        return plan

    def to_proposal(
        self, plan: ResearchPlan, *, requires_confirmation: bool | None = None
    ) -> Proposal:
        """Validate the plan, then convert it into a validated Proposal.

        The plan is first re-validated locally; if it is not VALIDATED it cannot
        become a proposal (raises PlanValidationError). When valid, it is handed
        to the EXISTING ``ProposalManager.create_proposal`` (which runs the
        existing ``ProposalValidator``), so proposal validation is reused, not
        replaced.
        """
        if plan.status != PlanStatus.VALIDATED:
            self._enrich_and_validate(plan)
        if plan.status != PlanStatus.VALIDATED:
            raise PlanValidationError(
                "Plan is not valid; refusing to create a proposal: "
                + "; ".join(plan.validation_errors)
            )

        actions = [
            {
                "tool": s.tool,
                "description": s.description or f"{s.tool}: {s.expected_result}",
                "parameters": s.parameters,
                "dependencies": s.dependencies,
            }
            for s in plan.steps
        ]
        sources = [
            {
                "source_type": "research",
                "identifier": src.get("url", ""),
                "excerpt": src.get("title", ""),
                "metadata": src,
            }
            for src in plan.sources
        ]
        confirm = requires_confirmation if requires_confirmation is not None else any(
            s.confirmation_requirement for s in plan.steps
        )
        proposal = self.proposal_manager.create_proposal(
            objective=plan.objective,
            actions=actions,
            sources=sources,
            risk_level=ProposalRiskLevel(plan.risk_level),
            requires_confirmation=confirm,
            audit_metadata={"origin": "research_planner", "rationale": plan.rationale},
        )
        # Align each ProposedAction.action_id with the originating plan step_id
        # so dependency resolution at execution time (which uses the step_ids
        # carried in ProposedAction.dependencies) resolves correctly.
        for step, action in zip(plan.steps, proposal.proposed_actions):
            action.action_id = step.step_id
        return proposal

    # -------------------------------------------------------------- validation
    def _enrich_and_validate(self, plan: ResearchPlan) -> None:
        """Derive risk/confirmation from permissions and run hard validation."""
        errors: list[str] = []

        if not plan.objective.strip():
            errors.append("plan objective is empty")

        # Track seen step ids for dependency resolution.
        seen_ids: set[str] = set()
        for step in plan.steps:
            if not step.step_id:
                errors.append("a step is missing step_id")
                continue
            if step.step_id in seen_ids:
                errors.append(f"duplicate step_id: {step.step_id}")
            seen_ids.add(step.step_id)

            # Tool validation against the live registry (fail safe, no invention).
            if not step.tool:
                errors.append(f"step {step.step_id} has no tool")
            elif not self.tool_registry.has_tool(step.tool):
                errors.append(
                    f"step {step.step_id} references unknown/unsupported tool: "
                    f"{step.tool!r}"
                )
            else:
                # Risk level is DERIVED from the tool's permission level. The
                # tool's permission is authoritative for safety classification:
                # SAFE -> low, CAUTION -> medium, DANGEROUS -> high. This keeps
                # risk honest and prevents a step from being under-rated.
                level = self.permission_manager.get_level(step.tool)
                step.risk_level = {
                    "SAFE": "low",
                    "CAUTION": "medium",
                    "DANGEROUS": "high",
                }[level]
                step.confirmation_requirement = self.permission_manager.requires_confirmation(
                    step.tool
                )

            if not step.expected_result.strip():
                errors.append(f"step {step.step_id} missing expected_result")
            if not step.rationale.strip():
                # Non-fatal but recorded; plans should explain themselves.
                step.rationale = step.rationale or "(no rationale provided)"

        # Dependency validation: resolve, no self-dep, no cycles.
        self._validate_dependencies(plan, seen_ids, errors)

        # Overall plan risk = max of step risks.
        if plan.steps:
            plan.risk_level = max(
                (s.risk_level for s in plan.steps),
                key=lambda r: _RISK_ORDER.get(r, 1),
            )

        plan.validation_errors = errors
        plan.status = PlanStatus.VALIDATED if not errors else PlanStatus.REJECTED

    @staticmethod
    def _validate_dependencies(
        plan: ResearchPlan, seen_ids: set[str], errors: list[str]
    ) -> None:
        for step in plan.steps:
            for dep in step.dependencies:
                if dep == step.step_id:
                    errors.append(f"step {step.step_id} depends on itself")
                elif dep not in seen_ids:
                    errors.append(
                        f"step {step.step_id} depends on unknown step_id: {dep}"
                    )

        # Cycle detection over the dependency graph.
        graph = {s.step_id: list(s.dependencies) for s in plan.steps}
        visited: set[str] = set()
        in_stack: set[str] = set()

        def _dfs(node: str) -> bool:
            if node in in_stack:
                return True  # cycle
            if node in visited:
                return False
            in_stack.add(node)
            for nxt in graph.get(node, []):
                if nxt in graph and _dfs(nxt):
                    return True
            in_stack.discard(node)
            visited.add(node)
            return False

        for sid in graph:
            if _dfs(sid):
                errors.append(f"dependency cycle detected involving step {sid}")
                break


# Convenience helper matching the Phase 9B pattern.
def plan_from_research(
    findings: ResearchFindings,
    *,
    config: Optional[JarvisConfig] = None,
    tool_registry: Optional[ToolRegistry] = None,
    permission_manager: Optional[PermissionManager] = None,
    synthesizer: Optional[PlanSynthesizer] = None,
    context: str = "",
) -> ResearchPlan:
    """One-shot: build a validated plan from research findings."""
    return ResearchPlanner(
        config=config,
        tool_registry=tool_registry,
        permission_manager=permission_manager,
        synthesizer=synthesizer,
    ).plan(findings, context=context)
