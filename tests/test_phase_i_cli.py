"""Phase I — operator visibility / preflight warning / result artifact.

Reuses the proven Phase F fixtures (tests.test_phase_f_cli) and the existing
runtime AgentLoop to verify the three safety-preserving Phase I improvements
in jarvis.py run_agent():

  #1 iteration/replan visibility  -> agent.* EventBus events surfaced to CLI
  #2 preflight readiness warning  -> read-only, non-blocking WARN only
  #3 machine-readable artifact    -> logs/agent_last.json (non-secret fields)

All gates preserved: ResearchPipeline/ProposalValidator/PermissionManager/
AgentLoop untouched; no auto-approve; no second engine; HARD_MAX=20.
"""
import json
import socket

import pytest

from research.pipeline import ResearchFindings, ResearchPipeline
from research.planner import ResearchPlanner
from proposal.agent_loop import AgentLoopStatus

import jarvis as jarvis_mod
import tests.test_phase_f_cli as F  # proven fixtures


class _EmptyResearchPipeline(ResearchPipeline):
    """ResearchPipeline whose research yields zero successfully-fetched sources."""

    def research(self, query, *, limits=None):
        return ResearchFindings(query=query or "")


@pytest.fixture
def ctx_mini():
    mini, ctx = F._build_minimal_assistant()
    return mini, ctx


# --------------------------------------------------------------------------- #
# #1 iteration/replan visibility
# --------------------------------------------------------------------------- #
def test_i_iteration_events_surfaced(ctx_mini, capsys):
    mini, ctx = ctx_mini
    ctx.agent_loop._executor = F.ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, F._pass)
    planner = F._planner(ctx, F.VerifyPlanSynthesizer(ctx.config))
    pipeline = F.FakeResearchPipeline(config=ctx.config, tool_registry=ctx.tool_registry)

    res = mini.run_agent("run the unit tests", pipeline=pipeline, planner=planner,
                         confirm_fn=lambda t, details="": True)

    out = capsys.readouterr().out
    assert res.status == AgentLoopStatus.DONE
    # Per-iteration agent.* events surfaced (observation only).
    assert "[agent] iteration 1 started" in out
    assert "[agent] execution completed:" in out
    assert "[agent] verification:" in out
    assert "[agent] completed:" in out
    # Existing final summary preserved.
    assert "[agent] status=done" in out
    assert "[agent] iterations=1" in out
    # No traceback.
    assert "Traceback" not in capsys.readouterr().err


def test_i_denied_events_surfaced(ctx_mini, capsys):
    mini, ctx = ctx_mini
    ctx.agent_loop._executor = F.ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, F._pass)
    planner = F._planner(ctx, F.FakePlanSynthesizer(ctx.config))
    pipeline = F.FakeResearchPipeline(config=ctx.config, tool_registry=ctx.tool_registry)

    res = mini.run_agent("do a thing", pipeline=pipeline, planner=planner,
                         confirm_fn=lambda t, details="": False)

    out = capsys.readouterr().out
    assert res.status == AgentLoopStatus.STOPPED_DENIED
    assert "[agent] aborted:" in out
    assert "[agent] status=stopped_denied" in out


# --------------------------------------------------------------------------- #
# #2 preflight readiness warning (informational only, never blocks/fails-open)
# --------------------------------------------------------------------------- #
def test_i_preflight_warning_informational(ctx_mini, capsys, monkeypatch):
    mini, ctx = ctx_mini

    # Force both probes to fail -> warnings should be produced.
    def _boom(*a, **k):
        raise OSError("forced")
    monkeypatch.setattr(socket, "create_connection", _boom)

    warnings = jarvis_mod.agent_readiness_warnings(mini.config)
    assert warnings, "expected at least one readiness warning when probes fail"
    joined = " ".join(warnings)
    assert "Ollama" in joined
    assert "connectivity" in joined

    # The warning must NOT block or fail-open: a valid run still reaches DONE.
    ctx.agent_loop._executor = F.ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, F._pass)
    planner = F._planner(ctx, F.VerifyPlanSynthesizer(ctx.config))
    pipeline = F.FakeResearchPipeline(config=ctx.config, tool_registry=ctx.tool_registry)
    res = mini.run_agent("run the unit tests", pipeline=pipeline, planner=planner,
                         confirm_fn=lambda t, details="": True)

    captured = capsys.readouterr()
    assert "[agent] readiness warning:" in captured.err
    assert res is not None and res.status == AgentLoopStatus.DONE
    # Safety path intact: AgentLoop executed, not bypassed.
    assert res.final_verification is not None


# --------------------------------------------------------------------------- #
# #3 machine-readable result artifact (logs/agent_last.json)
# --------------------------------------------------------------------------- #
@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(jarvis_mod, "REPO", tmp_path)
    return tmp_path


def _read_artifact(tmp_path):
    return json.loads((tmp_path / "logs" / "agent_last.json").read_text(encoding="utf-8"))


def test_i_json_artifact_success(ctx_mini, isolated_repo):
    mini, ctx = ctx_mini
    ctx.agent_loop._executor = F.ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, F._pass)
    planner = F._planner(ctx, F.VerifyPlanSynthesizer(ctx.config))
    pipeline = F.FakeResearchPipeline(config=ctx.config, tool_registry=ctx.tool_registry)

    res = mini.run_agent("run the unit tests", pipeline=pipeline, planner=planner,
                         confirm_fn=lambda t, details="": True)
    assert res.status == AgentLoopStatus.DONE

    art = _read_artifact(isolated_repo)
    assert art["status"] == "done"
    assert art["objective"] == "run the unit tests"
    assert isinstance(art["iterations"], list) and len(art["iterations"]) >= 1
    assert art["final_verification_status"] == "success"
    # Non-secret: no raw tool args / credentials present.
    blob = json.dumps(art)
    assert "details" not in blob or "command" not in blob


def test_i_json_artifact_denied(ctx_mini, isolated_repo):
    mini, ctx = ctx_mini
    ctx.agent_loop._executor = F.ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, F._pass)
    planner = F._planner(ctx, F.FakePlanSynthesizer(ctx.config))
    pipeline = F.FakeResearchPipeline(config=ctx.config, tool_registry=ctx.tool_registry)

    res = mini.run_agent("do a thing", pipeline=pipeline, planner=planner,
                         confirm_fn=lambda t, details="": False)
    assert res.status == AgentLoopStatus.STOPPED_DENIED

    art = _read_artifact(isolated_repo)
    assert art["status"] == "stopped_denied"
    assert isinstance(art["iterations"], list)


def test_i_json_artifact_research_failed(ctx_mini, isolated_repo):
    mini, ctx = ctx_mini
    res = mini.run_agent("do a thing",
                         pipeline=_EmptyResearchPipeline(ctx.config, ctx.tool_registry),
                         planner=None)
    assert res is None  # Phase H clean failure

    art = _read_artifact(isolated_repo)
    assert art["status"] == "research_failed"
    assert "no valid plan produced" in art["message"]


# --------------------------------------------------------------------------- #
# safety regression
# --------------------------------------------------------------------------- #
def test_i_no_execution_when_planning_fails(ctx_mini):
    mini, ctx = ctx_mini
    calls = {"run": 0}

    def _spy(*a, **k):
        calls["run"] += 1
        raise AssertionError("AgentLoop.run must NOT be reached on plan failure")
    ctx.agent_loop.run = _spy

    res = mini.run_agent("do a thing",
                         pipeline=_EmptyResearchPipeline(ctx.config, ctx.tool_registry),
                         planner=None)
    assert res is None
    assert calls["run"] == 0  # no execution occurred


def test_i_valid_research_still_reaches_agent_loop(ctx_mini):
    mini, ctx = ctx_mini
    reached = {"run": 0}
    real = ctx.agent_loop.run

    def _spy(o, p, **k):
        reached["run"] += 1
        return real(o, p, **k)
    ctx.agent_loop.run = _spy
    ctx.agent_loop._executor = F.ScenarioExecutor(ctx.tool_registry, ctx.permission_manager, F._pass)
    planner = F._planner(ctx, F.VerifyPlanSynthesizer(ctx.config))
    pipeline = F.FakeResearchPipeline(config=ctx.config, tool_registry=ctx.tool_registry)

    res = mini.run_agent("run the unit tests", pipeline=pipeline, planner=planner,
                         confirm_fn=lambda t, details="": True)
    assert reached["run"] == 1
    assert res.status == AgentLoopStatus.DONE
