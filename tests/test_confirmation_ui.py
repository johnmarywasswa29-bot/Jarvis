"""Focused tests for Confirmation UI integration."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from PySide6.QtWidgets import QApplication

from ui.confirmation_panel import ConfirmationPanel
from ui.main_window import AgentRuntime
from ui.sidebar import Sidebar
from core.events import Event, EventType
from orchestration.manager import Orchestrator
from proposal.state import ProposalRiskLevel


class ConfirmationTool:
    name = "paper_executor"
    enabled = True

    def run(self, **kwargs):
        return {"status": "ok"}


registry = SimpleNamespace(
    tools=[ConfirmationTool()],
    run_tool=lambda t, **kw: type("R", (), {"__dict__": t.run(**kw)})(),
)


def make_pending_item(orch):
    proposal = orch.proposal_manager.create_proposal(
        objective="ui test",
        actions=[{"tool": "paper_executor", "description": "run", "parameters": {"instrument": "AAA", "quantity": 1, "order_type": "market"}}],
        risk_level=ProposalRiskLevel.MEDIUM,
        requires_confirmation=True,
    )
    state = orch.start_workflow(proposal)
    step = state.steps[0]
    return {
        "confirmation_id": step.confirmation_token,
        "proposal_id": proposal.proposal_id,
        "step_uuid": step.uuid,
        "objective": proposal.objective,
        "risk_level": proposal.risk_level.value,
        "action_tool": step.tool,
        "action_description": step.description,
        "action_parameters": step.parameters,
        "source_references": [],
        "validation_errors": list(proposal.validation_errors),
        "workflow_status": state.status.value,
        "step_status": step.status.value,
        "created_at": step.created_at,
        "expires_at": getattr(proposal, "expires_at", ""),
    }


class BaseQAppTest(unittest.TestCase):
    def setUp(self):
        self._app = QApplication.instance() or QApplication(sys.argv)

    def tearDown(self):
        self._app.processEvents()


class TestConfirmationPanel(BaseQAppTest):
    def test_empty_panel(self):
        panel = ConfirmationPanel()
        self.assertEqual(panel._cards, {})

    def test_add_and_remove_pending(self):
        panel = ConfirmationPanel()
        orch = Orchestrator(rag=None, tool_registry=registry, require_confirmation=True)
        item = make_pending_item(orch)
        panel.add_pending(item)
        self.assertEqual(len(panel._cards), 1)
        panel.remove_pending(item["confirmation_id"])
        self.assertEqual(panel._cards, {})

    def test_duplicate_add_ignored(self):
        panel = ConfirmationPanel()
        orch = Orchestrator(rag=None, tool_registry=registry, require_confirmation=True)
        item = make_pending_item(orch)
        panel.add_pending(item)
        panel.add_pending(item)
        self.assertEqual(len(panel._cards), 1)

    def test_clear_removes_all(self):
        panel = ConfirmationPanel()
        orch = Orchestrator(rag=None, tool_registry=registry, require_confirmation=True)
        item1 = make_pending_item(orch)
        item2 = make_pending_item(orch)
        panel.add_pending(item1)
        panel.add_pending(item2)
        panel.clear()
        self.assertEqual(panel._cards, {})

    def test_remove_unknown_id_is_noop(self):
        panel = ConfirmationPanel()
        panel.remove_pending("missing")
        self.assertEqual(panel._cards, {})

    def test_approve_reject_signals(self):
        panel = ConfirmationPanel()
        orch = Orchestrator(rag=None, tool_registry=registry, require_confirmation=True)
        item = make_pending_item(orch)
        panel.add_pending(item)
        approved = []
        rejected = []
        panel.approveRequested.connect(lambda cid: approved.append(cid))
        panel.rejectRequested.connect(lambda cid: rejected.append(cid))
        card = list(panel._cards.values())[0]
        card.approveRequested.emit(item["confirmation_id"])
        card.rejectRequested.emit(item["confirmation_id"])
        self.assertEqual(approved, [item["confirmation_id"]])
        self.assertEqual(rejected, [item["confirmation_id"]])


class TestConfirmationUIRuntime(BaseQAppTest):
    def test_agent_runtime_receives_orchestration(self):
        rt = AgentRuntime(repo=REPO)
        self.assertIsNotNone(rt.orchestration)

    def test_sidebar_has_confirm_section(self):
        sidebar = Sidebar()
        self.assertIn("confirm", sidebar._items)

    def test_refresh_without_runtime_is_safe(self):
        from ui.main_window import JarvisWindow

        window = JarvisWindow()
        try:
            window.runtime.orchestration = None
            window._refresh_confirmations()
        finally:
            window.close()

    def test_event_handler_subscribes_to_event_types(self):
        from ui.main_window import JarvisWindow

        window = JarvisWindow()
        try:
            window._init_confirmation()
            event_types = getattr(window, "_confirm_event_types", [])
            self.assertIn(EventType.TRADE_CONFIRMATION_REQUIRED, event_types)
            self.assertIn(EventType.TRADE_CONFIRMED, event_types)
            self.assertIn(EventType.TRADE_REJECTED, event_types)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
