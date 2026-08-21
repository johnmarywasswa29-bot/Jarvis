"""Phase H — CLI clarity fix for research/plan failures (jarvis.py agent).

Reuses the proven Phase F fixtures to exercise the REAL runtime AgentLoop
path, but verifies the research-failure boundary behaves cleanly:

  * an empty/invalid research result must NOT produce a proposal,
  * the real PlanValidationError must be caught and surfaced as ONE clean
    operator message (no traceback),
  * the CLI agent command exits non-zero (run_agent returns None),
  * NO AgentLoop execution, NO ToolRegistry.execute, NO tool execution occurs.

The happy path is also re-checked so the wrap does not accidentally swallow
valid research.
"""
import sys

import pytest

from research.pipeline import ResearchFindings, ResearchPipeline
from research.planner import ResearchPlanner
from proposal.agent_loop import AgentLoopStatus

import tests.test_phase_f_cli as F  # proven fixtures


class _EmptyResearchPipeline(ResearchPipeline):
    """ResearchPipeline whose research yields zero successfully-fetched sources."""

    def research(self, query, *, limits=None):
        # Empty findings -> get_citations() returns [] -> planner raises
        # the real PlanValidationError. No network/LLM invoked.
        return ResearchFindings(query=query or "")


def test_h_research_failure_is_clean_and_safe(capsys):
    mini, ctx = F._build_minimal_assistant()
    calls = {"run": 0}

    def _run_spy(*a, **k):
        calls["run"] += 1
        raise AssertionError("agent_loop.run must NOT be reached on research failure")

    ctx.agent_loop.run = _run_spy  # spy: proves AgentLoop is never entered

    # No injected planner -> REAL ResearchPlanner (LLM synthesizer) is used.
    # With empty findings it raises the real PlanValidationError.
    result = mini.run_agent(
        "do a thing",
        pipeline=_EmptyResearchPipeline(ctx.config, ctx.tool_registry),
        planner=None,
    )

    captured = capsys.readouterr()
    err = captured.err

    # 1) Clean failure: run_agent returns None -> CLI exits non-zero.
    assert result is None

    # 2) Clear operator-facing message on stderr, with the real reason.
    assert "Research could not produce a valid plan" in err
    assert "No action was taken" in err
    assert "Insufficient research" in err  # the real PlanValidationError reason

    # 3) NO traceback emitted.
    assert "Traceback" not in err
    assert 'File "' not in err
    # The exception class name must not appear as an uncaught stack header.
    assert "\nPlanValidationError\n" not in err

    # 4) Safety: AgentLoop was never entered; nothing executed.
    assert calls["run"] == 0  # agent_loop.run never invoked
    # No ToolRegistry.execute and no tool executed (loop never reached, so
    # no proposal was ever produced to execute).
    assert "proposal" not in (err.lower())


def test_h_valid_research_still_reaches_agent_loop(capsys):
    """Regression: a valid research result must still reach AgentLoop.run."""
    mini, ctx = F._build_minimal_assistant()
    reached = {"run": 0}

    real_run = ctx.agent_loop.run

    def _run_spy(objective, proposal, **kw):
        reached["run"] += 1
        # Delegate to the real run so the full path executes with fakes.
        return real_run(objective, proposal, **kw)

    ctx.agent_loop.run = _run_spy
    ctx.agent_loop._executor = F.ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, F._pass)

    planner = F._planner(ctx, F.VerifyPlanSynthesizer(ctx.config))
    pipeline = F.FakeResearchPipeline(config=ctx.config, tool_registry=ctx.tool_registry)
    res = mini.run_agent(
        "run the unit tests", pipeline=pipeline, planner=planner,
        confirm_fn=lambda t, details="": True,
    )

    assert reached["run"] == 1  # happy path still reaches AgentLoop
    assert res is not None
    assert res.status == AgentLoopStatus.DONE
