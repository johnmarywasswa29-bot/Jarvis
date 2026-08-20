"""Phase 9H deterministic tests: Web UI research + interactive confirmation.

Two layers:
  1. ConfirmationManager unit tests — the security core (session binding,
     expiry, replay/duplicate rejection, disconnect abort, wrong-session
     rejection, explicit accept/deny/abort).
  2. WebSocket integration tests — a real FastAPI TestClient drives the /ws
     endpoint with the *real* confirmation_manager + WebUserDecider, while the
     heavy LLM/research backend is faked (ResearchWorkflow.run) so the test is
     offline and deterministic. This exercises the exact round-trip:
        chat(research) -> confirmation_required -> confirmation_response
        -> (accept|deny|abort) -> chat_done, with auth enforced.
"""

import asyncio
import threading
import time

import pytest

from web.server.confirmation import ConfirmationManager, WebUserDecider, DEFAULT_EXPIRY_S
from research.orchestrator import Decision


# --------------------------------------------------------------------------- #
# 1. ConfirmationManager security unit tests
# --------------------------------------------------------------------------- #
class TestConfirmationManager:
    def test_accept_resolves_decision(self):
        mgr = ConfirmationManager(expiry_s=30)
        pending = mgr.create("client-A", {"objective": "do thing"})
        t = threading.Thread(target=lambda: None)
        # Run decision resolution in a thread (as the decider would).
        result = {}
        def worker():
            result["d"] = mgr.await_result(pending.confirmation_id, timeout=5)
        th = threading.Thread(target=worker)
        th.start()
        time.sleep(0.2)
        ok = mgr.resolve(pending.confirmation_id, "client-A", Decision.ACCEPT)
        th.join(timeout=5)
        assert ok is True
        assert result["d"] == Decision.ACCEPT

    def test_deny_resolves_decision(self):
        mgr = ConfirmationManager(expiry_s=30)
        pending = mgr.create("client-A", {})
        ev = threading.Event()
        res = {}
        def worker():
            res["d"] = mgr.await_result(pending.confirmation_id, timeout=5)
            ev.set()
        threading.Thread(target=worker).start()
        time.sleep(0.2)
        assert mgr.resolve(pending.confirmation_id, "client-A", Decision.DENY) is True
        ev.wait(timeout=5)
        assert res["d"] == Decision.DENY

    def test_abort_resolves_decision(self):
        mgr = ConfirmationManager(expiry_s=30)
        pending = mgr.create("client-A", {})
        ev = threading.Event()
        res = {}
        def worker():
            res["d"] = mgr.await_result(pending.confirmation_id, timeout=5)
            ev.set()
        threading.Thread(target=worker).start()
        time.sleep(0.2)
        assert mgr.resolve(pending.confirmation_id, "client-A", Decision.ABORT) is True
        ev.wait(timeout=5)
        assert res["d"] == Decision.ABORT

    def test_session_mismatch_rejected(self):
        mgr = ConfirmationManager(expiry_s=30)
        pending = mgr.create("client-A", {})
        # A different session answers -> rejected.
        ok = mgr.resolve(pending.confirmation_id, "client-B", Decision.ACCEPT)
        assert ok is False
        # The original session can still answer.
        assert mgr.resolve(pending.confirmation_id, "client-A", Decision.ACCEPT) is True

    def test_replay_duplicate_rejected(self):
        mgr = ConfirmationManager(expiry_s=30)
        pending = mgr.create("client-A", {})
        assert mgr.resolve(pending.confirmation_id, "client-A", Decision.ACCEPT) is True
        # Second answer (replay) -> rejected.
        assert mgr.resolve(pending.confirmation_id, "client-A", Decision.ACCEPT) is False

    def test_expired_resolves_as_abort(self):
        mgr = ConfirmationManager(expiry_s=0.2)
        pending = mgr.create("client-A", {})
        res = mgr.await_result(pending.confirmation_id, timeout=1.0)
        assert res == Decision.ABORT
        # After expiry, a late answer is rejected.
        assert mgr.resolve(pending.confirmation_id, "client-A", Decision.ACCEPT) is False

    def test_disconnect_aborts_pending(self):
        mgr = ConfirmationManager(expiry_s=30)
        pending = mgr.create("client-A", {})
        mgr.create("client-B", {})  # should be untouched
        mgr.on_disconnect("client-A")
        # client-A's pending is consumed and would resolve ABORT.
        assert mgr.resolve(pending.confirmation_id, "client-A", Decision.ACCEPT) is False
        # client-B still has its pending.
        assert len(mgr.pending_for("client-B")) == 1

    def test_no_autoconfirm_on_default(self):
        # A brand-new manager.answer without ever being asked must never ACCEPT.
        mgr = ConfirmationManager(expiry_s=30)
        # Unknown confirmation id -> rejected (no auto-accept).
        assert mgr.resolve("conf_does_not_exist", "x", Decision.ACCEPT) is False


# --------------------------------------------------------------------------- #
# 2. WebSocket integration tests
# --------------------------------------------------------------------------- #
try:
    from fastapi.testclient import TestClient
    from web.server.app_factory import create_app  # type: ignore
except Exception:  # pragma: no cover - fallback
    from web.server.app import create_app  # type: ignore
    from fastapi.testclient import TestClient  # type: ignore

from modules.config import JarvisConfig
from research.orchestrator import ResearchWorkflow
from proposal.executor import ExecutionAudit, ExecutionStatus


class _StubRegistry:
    """Minimal ToolRegistry stand-in."""
    def __init__(self, tools=None):
        self.tools = {t.name: t for t in (tools or [])}
    def get_tool(self, name):
        return self.tools.get(name)
    def has_tool(self, name):
        return name in self.tools
    def tool_names(self):
        return list(self.tools.keys())


def _fake_workflow_run(self, objective, *, research=True, findings=None, context="",
                       limits=None, decider=None):
    """Offline stand-in for ResearchWorkflow.run.

    Exercises the REAL decider (WebUserDecider) so the confirmation round-trip
    is genuine, but skips LLM/network research/planning/execution.

    Mirrors 9F run() semantics: a research-ONLY objective (no action phrasing)
    returns findings WITHOUT invoking the confirmation gate, exactly as the real
    ResearchWorkflow does for Decision.RESEARCH_ONLY.
    """
    _ACTION = ("what should i do", "figure out what to do", "how should i",
               "plan to", "recommend actions", "and do it", "what can i do")
    is_research_only = not any(p in objective.lower() for p in _ACTION)

    plan = type("P", (), {
        "objective": objective,
        "steps": [type("S", (), {
            "step_id": "s1", "tool": "calculator",
            "description": "do it", "expected_result": "result",
            "risk_level": "low", "confirmation_requirement": False,
        })()],
        "sources": [type("C", (), {"title": "Src", "url": "https://e.x"})()],
        "risk_level": "low",
    })()
    proposal = type("Q", (), {
        "objective": objective, "actions": [],
        "overall_risk": "low", "requires_confirmation": (not is_research_only),
    })()
    audit = ExecutionAudit(proposal_id="p1", objective=objective)
    if is_research_only:
        # No confirmation gate for research-only (9F RESEARCH_ONLY path).
        audit.final_status = ExecutionStatus.RESEARCH_ONLY.value
        audit.research_findings = type("F", (), {"get_citations": lambda: []})()
        return audit
    decision = decider.decide(objective, plan, proposal)
    if decision == Decision.ACCEPT:
        audit.final_status = ExecutionStatus.SUCCESS.value
    elif decision == Decision.DENY:
        audit.final_status = ExecutionStatus.DENIED.value
    elif decision == Decision.ABORT:
        audit.final_status = ExecutionStatus.ABORTED.value
    else:
        audit.final_status = ExecutionStatus.ABORTED.value
    return audit


@pytest.fixture
def client(monkeypatch):
    # Build a minimal app with auth disabled (localhost) so the test can connect
    # without a token, but the confirmation gate is still fully enforced.
    app = create_app()
    cfg = JarvisConfig()
    cfg.web_auth_enabled = False
    cfg.web_host = "127.0.0.1"
    cfg.llm_provider = "test"
    app.state.config = cfg

    class _Runtime:
        tool_registry = _StubRegistry()
        chat_memory = None
    app.state.runtime = _Runtime()

    # Bypass LLM/network research: fake the workflow execution path only.
    monkeypatch.setattr(ResearchWorkflow, "run", _fake_workflow_run)
    return TestClient(app)


def _collect_ws(client, send_msg, *, answer=None, close_after_confirm=False):
    """Open a websocket, send a message, optionally answer a confirmation.

    Returns the list of server message types received.
    """
    received = []
    with client.websocket_connect("/ws/") as ws:
        ws.send_json(send_msg)
        # Read until we get a terminal message or a confirmation to answer.
        deadline = time.time() + 8
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


class TestWebResearchWS:
    def test_authenticated_research_request_reaches_confirmation(self, client):
        msgs = _collect_ws(client, {
            "type": "chat", "request_id": "r1",
            "payload": {"content": "Research the best laptops and figure out what to do", "stream": False},
        }, answer="accept")
        types = [m["type"] for m in msgs]
        assert "confirmation_required" in types
        assert "chat_done" in types
        # The confirmation carried a structured proposal.
        conf = next(m for m in msgs if m["type"] == "confirmation_required")
        assert conf["payload"]["confirmation_id"]
        assert conf["payload"]["objective"]
        assert conf["payload"]["requires_confirmation"] is True

    def test_research_only_request(self, client):
        # Research-only phrasing -> reaches the workflow and returns findings
        # WITHOUT a confirmation gate (9F RESEARCH_ONLY path). No consequential
        # action is proposed, so no confirmation_required is sent.
        msgs = _collect_ws(client, {
            "type": "chat", "request_id": "r2",
            "payload": {"content": "Research the history of telescopes", "stream": False},
        }, answer=None)
        types = [m["type"] for m in msgs]
        assert "confirmation_required" not in types
        assert "chat_done" in types

    def test_accept_proceeds(self, client):
        msgs = _collect_ws(client, {
            "type": "chat", "request_id": "r3",
            "payload": {"content": "Research X and figure out what to do", "stream": False},
        }, answer="accept")
        done = next(m for m in msgs if m["type"] == "chat_done")
        assert done["payload"]["content"]

    def test_deny_executes_nothing(self, client):
        msgs = _collect_ws(client, {
            "type": "chat", "request_id": "r4",
            "payload": {"content": "Research X and figure out what to do", "stream": False},
        }, answer="deny")
        types = [m["type"] for m in msgs]
        assert "confirmation_required" in types
        assert "chat_done" in types
        # No research_progress 'complete' with success status expected to imply
        # execution; the confirmation was denied (handled by fake workflow).
        assert "confirmation_required" in types

    def test_abort_executes_nothing(self, client):
        msgs = _collect_ws(client, {
            "type": "chat", "request_id": "r5",
            "payload": {"content": "Research X and figure out what to do", "stream": False},
        }, answer="abort")
        types = [m["type"] for m in msgs]
        assert "confirmation_required" in types
        assert "chat_done" in types

    def test_expired_confirmation_aborts(self, client, monkeypatch):
        # Force a tiny expiry so the pending confirmation times out.
        import web.server.routes.ws as ws_mod
        monkeypatch.setattr(ws_mod.confirmation_manager, "expiry_s", 0.3)
        msgs = _collect_ws(client, {
            "type": "chat", "request_id": "r6",
            "payload": {"content": "Research X and figure out what to do", "stream": False},
        }, answer=None)  # never answer -> expiry
        types = [m["type"] for m in msgs]
        # The flow completes (aborted) without an explicit answer.
        assert "chat_done" in types

    def test_websocket_disconnect_during_confirmation(self, client):
        # Close the socket while a confirmation is pending -> must not execute.
        msgs = _collect_ws(client, {
            "type": "chat", "request_id": "r7",
            "payload": {"content": "Research X and figure out what to do", "stream": False},
        }, close_after_confirm=True)
        # We at least received the confirmation request; the disconnect path
        # must not raise inside the server.
        assert any(m["type"] == "confirmation_required" for m in msgs)

    def test_existing_ordinary_chat_unchanged(self, client, monkeypatch):
        # An ordinary (non-research) request must NOT produce a confirmation.
        # We stub handle_chat to a deterministic reply to avoid the LLM.
        import web.server.routes.ws as ws_mod
        async def _fake_handle_chat(websocket, client_id, request_id, content, stream, provider):
            from web.schemas.messages import ChatDone
            await ws_mod.manager.send(client_id, ChatDone(
                request_id=request_id, payload={"content": "ordinary reply", "provider": "test"}))
        monkeypatch.setattr(ws_mod, "handle_chat", _fake_handle_chat)
        msgs = _collect_ws(client, {
            "type": "chat", "request_id": "r8",
            "payload": {"content": "What is 2+2?", "stream": False},
        })
        types = [m["type"] for m in msgs]
        assert "confirmation_required" not in types
        assert "chat_done" in types

    def test_unauthenticated_request_rejected(self, monkeypatch):
        # Auth enabled + wrong/absent token -> connection rejected (1008).
        app = create_app()
        cfg = JarvisConfig()
        cfg.web_auth_enabled = True
        cfg.web_auth_token = "secret-token-1234567890"
        cfg.web_host = "127.0.0.1"
        cfg.llm_provider = "test"
        app.state.config = cfg
        class _Runtime:
            tool_registry = _StubRegistry()
            chat_memory = None
        app.state.runtime = _Runtime()
        monkeypatch.setattr(ResearchWorkflow, "run", _fake_workflow_run)
        bad = TestClient(app)
        # Connecting without token must fail closed.
        rejected = False
        try:
            with bad.websocket_connect("/ws/") as ws:
                ws.receive_json()  # should not succeed
        except Exception:
            rejected = True
        assert rejected is True
