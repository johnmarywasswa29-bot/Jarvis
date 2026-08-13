"""Confirmation API tests for the orchestration layer."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from orchestration.manager import Orchestrator
from proposal.manager import ProposalManager
from proposal.state import ProposalStatus, ProposedAction, ProposalRiskLevel
from workflows.state import StepStatus


class ConfirmationTool:
    name = "paper_executor"
    enabled = True

    def run(self, **kwargs):
        return {"status": "ok"}


registry = SimpleNamespace(
    tools=[ConfirmationTool()],
    run_tool=lambda t, **kw: type("R", (), {"__dict__": t.run(**kw)})(),
)


def make_proposal(orch, objective="api test"):
    proposal = orch.proposal_manager.create_proposal(
        objective=objective,
        actions=[{"tool": "paper_executor", "description": "run", "parameters": {"instrument": "AAA", "quantity": 1, "order_type": "market"}}],
        risk_level=ProposalRiskLevel.MEDIUM,
        requires_confirmation=True,
    )
    return proposal


class TestConfirmationAPI(unittest.TestCase):
    def test_list_pending_confirmations_empty(self):
        orch = Orchestrator(rag=None, tool_registry=registry, require_confirmation=False)
        self.assertEqual(orch.get_pending_confirmations(), [])

    def test_list_pending_confirmations_after_start(self):
        orch = Orchestrator(rag=None, tool_registry=registry, require_confirmation=True)
        proposal = make_proposal(orch)
        state = orch.start_workflow(proposal)
        self.assertEqual(state.status, StepStatus.WAITING_FOR_CONFIRMATION)
        pending = orch.get_pending_confirmations()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["proposal_id"], proposal.proposal_id)

    def test_confirm_valid_pending(self):
        orch = Orchestrator(rag=None, tool_registry=registry, require_confirmation=True)
        proposal = make_proposal(orch)
        state = orch.start_workflow(proposal)
        step = state.steps[0]
        result = orch.confirm(proposal.proposal_id, step.uuid, step.confirmation_token, approved=True)
        self.assertEqual(result.status, StepStatus.COMPLETED)
        self.assertEqual(orch.proposal_manager.get(proposal.proposal_id).status, ProposalStatus.CONFIRMED)

    def test_cancel_valid_pending(self):
        orch = Orchestrator(rag=None, tool_registry=registry, require_confirmation=True)
        proposal = make_proposal(orch)
        state = orch.start_workflow(proposal)
        step = state.steps[0]
        result = orch.cancel(proposal.proposal_id, step.uuid, step.confirmation_token)
        self.assertIn(result.status, {StepStatus.FAILED, StepStatus.REJECTED, StepStatus.CANCELLED})
        self.assertIn(orch.proposal_manager.get(proposal.proposal_id).status, {ProposalStatus.REJECTED, ProposalStatus.CANCELLED})

    def test_confirm_unknown_id_fails_safely(self):
        orch = Orchestrator(rag=None, tool_registry=registry, require_confirmation=True)
        result = orch.confirm("missing", "bad-step", "bad-token", approved=True)
        self.assertEqual(result.status, StepStatus.FAILED)

    def test_duplicate_confirmation_is_safe_noop(self):
        orch = Orchestrator(rag=None, tool_registry=registry, require_confirmation=True)
        proposal = make_proposal(orch)
        state = orch.start_workflow(proposal)
        step = state.steps[0]
        orch.confirm(proposal.proposal_id, step.uuid, step.confirmation_token, approved=True)
        duplicate = orch.confirm(proposal.proposal_id, step.uuid, step.confirmation_token, approved=False)
        self.assertEqual(step.status, StepStatus.COMPLETED)
        self.assertIn(duplicate.status, {StepStatus.FAILED, StepStatus.COMPLETED})

    def test_cancel_then_confirm_does_not_execute(self):
        orch = Orchestrator(rag=None, tool_registry=registry, require_confirmation=True)
        proposal = make_proposal(orch)
        state = orch.start_workflow(proposal)
        step = state.steps[0]
        orch.cancel(proposal.proposal_id, step.uuid, step.confirmation_token)
        second = orch.confirm(proposal.proposal_id, step.uuid, step.confirmation_token, approved=True)
        self.assertNotEqual(step.status, StepStatus.COMPLETED)

    def test_confirmation_gate_cannot_be_bypassed(self):
        orch = Orchestrator(rag=None, tool_registry=registry, require_confirmation=True)
        proposal = make_proposal(orch)
        state = orch.build_workflow(proposal)
        self.assertTrue(any(s.requires_confirmation for s in state.steps))
        executed = orch.executor.execute(state)
        self.assertEqual(executed.status, StepStatus.WAITING_FOR_CONFIRMATION)

    def test_get_pending_exposes_expected_fields(self):
        orch = Orchestrator(rag=None, tool_registry=registry, require_confirmation=True)
        proposal = make_proposal(orch)
        orch.start_workflow(proposal)
        pending = orch.get_pending_confirmations()
        item = pending[0]
        for key in [
            "confirmation_id",
            "proposal_id",
            "step_uuid",
            "objective",
            "risk_level",
            "action_tool",
            "action_description",
            "action_parameters",
            "source_references",
            "validation_errors",
            "workflow_status",
            "step_status",
            "created_at",
            "expires_at",
        ]:
            self.assertIn(key, item)


if __name__ == "__main__":
    unittest.main(verbosity=2)
