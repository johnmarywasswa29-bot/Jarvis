"""Phase 9H Web UI end-to-end test: authentication -> research -> plan ->
confirmation -> accept/deny/abort -> execution -> final audit, over the REAL
WebSocket using the REAL backend (LLM/network seams faked only).

This drives the actual web server path:
    ws auth -> dispatch -> ResearchBridge classification
    -> JarvisBrain.run -> ResearchBridge.handle
    -> ResearchWorkflow.run (REAL planner/executor/permission/tool-registry,
       with FAKE LLM synthesizer + FAKE research pipeline)
    -> WebUserDecider -> ConfirmationManager -> confirmation_required event
    -> client confirmation_response -> resolve -> ProposalExecutor.execute
    -> research_progress + chat_done with the final ExecutionAudit.

Only the LLM-dependent (plan synthesizer) and network-dependent (research
pipeline) seams are faked, exactly like the 9C/9D/9F unit tests. Everything
else -- plan validation, tool/permission derivation, proposal validation,
the confirmation gate, execution, and the audit -- is the real implementation.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from modules.config import JarvisConfig
from modules.tools import ToolRegistry, WebSearchTool, CalculatorTool
from modules.permission_manager import PermissionManager
from research.pipeline import ResearchFindings, ResearchSource
from research.planner import ResearchPlanner, PlanSynthesizer, ResearchPlan, PlanStep
from research.orchestrator import ResearchWorkflow, Decision
from research.bridge import ResearchBridge
from proposal.executor import ProposalExecutor

from web.server.app import create_app


# --------------------------------------------------------------------------- #
# Fakes for the LLM / network seams only
# --------------------------------------------------------------------------- #
class _FakeResearchSynthesizer:
    def identify_gaps(self, findings, objective, max_gaps=3):
        return []
    def synthesize(self, findings, objective, max_content_per_page=8000):
        return "Synthesis: search the web and compute a value."


class FakeResearchPipeline:
    def __init__(self, *args, **kwargs):
        self.config = kwargs.get("config")
        self.tool_registry = kwargs.get("tool_registry")
        self.limits = kwargs.get("limits")
        self.synthesizer = _FakeResearchSynthesizer()

    def research(self, objective, context="", limits=None):
        src = ResearchSource(
            title="Reliable Source on Topic X",
            url="https://example.com/topic-x",
            search_query=objective,
            fetch_status="success",
            extracted_text="Topic X recommends searching the web and computing a value.",
            extraction_method="fake",
        )
        return ResearchFindings(
            query=objective,
            sources=[src],
            findings=[{"claim": "Topic X is well documented.", "source": "https://example.com/topic-x"}],
            synthesis="Synthesis: search the web and compute a value.",
            confidence=0.8,
            gaps=[],
        )


class FakePlanSynthesizer(PlanSynthesizer):
    def __init__(self, config=None):
        self.config = config

    def synthesize_plan(self, findings, context=""):
        plan = ResearchPlan(
            objective=findings.query,
            rationale="Research supports a web search followed by a computation.",
            sources=findings.get_citations(),
            metadata={"synthesizer": "fake"},
        )
        plan.steps.append(PlanStep(
            step_id="s1",
            description="Search the web for current information",
            tool="web_search",
            parameters={"query": findings.query},
            expected_result="Search results for the objective",
            risk_level="medium",
            confirmation_requirement=True,
            rationale="Need up-to-date facts from the web.",
        ))
        plan.steps.append(PlanStep(
            step_id="s2",
            description="Compute the recommended value",
            tool="calculator",
            parameters={"expression": "6*7"},
            dependencies=["s1"],
            expected_result="42",
            risk_level="low",
            confirmation_requirement=False,
            rationale="A safe local computation.",
        ))
        return plan


def _make_fake_workflow():
    """Build a REAL ResearchWorkflow with fakes injected for LLM/network seams."""
    config = JarvisConfig()
    tool_registry = ToolRegistry(config)
    if not tool_registry.has_tool("web_search"):
        tool_registry.register_tool(WebSearchTool())
    if not tool_registry.has_tool("calculator"):
        tool_registry.register_tool(CalculatorTool())
    permission_manager = PermissionManager()

    planner = ResearchPlanner(
        config=config,
        tool_registry=tool_registry,
        permission_manager=permission_manager,
        synthesizer=FakePlanSynthesizer(config),
    )
    workflow = ResearchWorkflow(
        config=config,
        research_pipeline=FakeResearchPipeline(config=config, tool_registry=tool_registry),
        planner=planner,
        permission_manager=permission_manager,
        tool_registry=tool_registry,
        # decider is injected per-run by ResearchBridge.handle
    )
    return workflow


@pytest.fixture
def ws_app(monkeypatch):
    """Web app with auth disabled (localhost) and the research workflow faked."""
    app = create_app()
    cfg = JarvisConfig()
    cfg.web_auth_enabled = False
    cfg.web_host = "127.0.0.1"
    cfg.llm_provider = "test"
    app.state.config = cfg

    class _Runtime:
        tool_registry = None
        chat_memory = None
    app.state.runtime = _Runtime()

    # Inject the fake-backed research workflow into the brain's bridge.
    import modules.brain as brain_mod
    def _fake_get_research_bridge(self):
        return ResearchBridge(workflow=_make_fake_workflow())
    monkeypatch.setattr(brain_mod.JarvisBrain, "_get_research_bridge", _fake_get_research_bridge)
    return TestClient(app)


def _drive(client, content, *, answer=None, close_after_confirm=False):
    received = []
    with client.websocket_connect("/ws/") as ws:
        ws.send_json({"type": "chat", "request_id": "r1",
                      "payload": {"content": content, "stream": False}})
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                msg = ws.receive_json()
            except Exception:
                break
            received.append(msg)
            if msg.get("type") == "confirmation_required":
                if answer is not None:
                    ws.send_json({
                        "type": "confirmation_response",
                        "request_id": msg.get("request_id", ""),
                        "payload": {
                            "confirmation_id": msg["payload"]["confirmation_id"],
                            "decision": answer,
                        },
                    })
                elif close_after_confirm:
                    ws.close()
                    break
            if msg.get("type") == "chat_done":
                break
            if msg.get("type") == "error":
                break
    return received


class TestWebResearchE2E:
    def test_auth_then_research_plan_confirm_accept_executes(self, ws_app):
        msgs = _drive(ws_app, "Research the best approach and figure out what to do",
                     answer="accept")
        types = [m["type"] for m in msgs]
        assert "confirmation_required" in types
        assert "chat_done" in types

        # The proposal carried objective / sources / steps / risk.
        conf = next(m for m in msgs if m["type"] == "confirmation_required")
        p = conf["payload"]
        assert p["objective"]
        assert p["requires_confirmation"] is True
        assert any(s["tool"] == "calculator" for s in p["steps"])
        assert any(s["tool"] == "web_search" for s in p["steps"])

        # Final audit: execution succeeded and the calculator ran.
        done = next(m for m in msgs if m["type"] == "chat_done")
        assert "42" in (done["payload"]["content"] or "")

    def test_research_only_no_execution(self, ws_app):
        msgs = _drive(ws_app, "Research the history of telescopes", answer=None)
        types = [m["type"] for m in msgs]
        assert "confirmation_required" not in types
        assert "chat_done" in types
        done = next(m for m in msgs if m["type"] == "chat_done")
        assert "Sources:" in (done["payload"]["content"] or "")

    def test_deny_executes_nothing(self, ws_app):
        msgs = _drive(ws_app, "Research X and figure out what to do", answer="deny")
        types = [m["type"] for m in msgs]
        assert "confirmation_required" in types
        assert "chat_done" in types
        # No execution: the rendered response must report denial / no actions.
        done = next(m for m in msgs if m["type"] == "chat_done")
        content = done["payload"]["content"] or ""
        assert ("denied" in content.lower()) or ("no actions" in content.lower()) or ("aborted" in content.lower())

    def test_abort_executes_nothing(self, ws_app):
        msgs = _drive(ws_app, "Research X and figure out what to do", answer="abort")
        types = [m["type"] for m in msgs]
        assert "confirmation_required" in types
        assert "chat_done" in types
        done = next(m for m in msgs if m["type"] == "chat_done")
        content = done["payload"]["content"] or ""
        assert ("aborted" in content.lower()) or ("no actions" in content.lower()) or ("denied" in content.lower())

    def test_websocket_disconnect_during_confirmation(self, ws_app):
        # Disconnect while pending -> must not execute; server must not crash.
        msgs = _drive(ws_app, "Research X and figure out what to do", close_after_confirm=True)
        # At minimum the confirmation was shown; disconnect handled gracefully.
        assert any(m["type"] == "confirmation_required" for m in msgs)

    def test_unauthenticated_request_rejected(self, monkeypatch):
        app = create_app()
        cfg = JarvisConfig()
        cfg.web_auth_enabled = True
        cfg.web_auth_token = "test-token-1234567890123456"
        cfg.web_host = "127.0.0.1"
        cfg.llm_provider = "test"
        app.state.config = cfg

        class _Runtime:
            tool_registry = None
            chat_memory = None
        app.state.runtime = _Runtime()

        import modules.brain as brain_mod
        def _fake_get_research_bridge(self):
            return ResearchBridge(workflow=_make_fake_workflow())
        monkeypatch.setattr(brain_mod.JarvisBrain, "_get_research_bridge", _fake_get_research_bridge)

        bad = TestClient(app)
        rejected = False
        try:
            with bad.websocket_connect("/ws/") as ws:
                ws.receive_json()
        except Exception:
            rejected = True
        assert rejected is True

    def test_existing_ordinary_chat_unchanged(self, ws_app, monkeypatch):
        import web.server.routes.ws as ws_mod
        async def _fake_handle_chat(websocket, client_id, request_id, content, stream, provider):
            from web.schemas.messages import ChatDone
            await ws_mod.manager.send(client_id, ChatDone(
                request_id=request_id, payload={"content": "ordinary reply", "provider": "test"}))
        monkeypatch.setattr(ws_mod, "handle_chat", _fake_handle_chat)
        msgs = _drive(ws_app, "What is 2+2?", answer=None)
        types = [m["type"] for m in msgs]
        assert "confirmation_required" not in types
        assert "chat_done" in types
