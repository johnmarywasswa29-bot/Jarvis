"""Tests for orchestration.manager."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from orchestration.manager import Orchestrator
from proposal.manager import ProposalManager
from proposal.state import Proposal, ProposalStatus, ProposedAction
from research.orchestrator import ResearchFindings
from workflows.state import WorkflowState, WorkflowStep, StepStatus


class FakeRag:
    def search(self, query, k=5):
        return [{"text": "demo text", "source": "doc1", "score": 0.9}]


class DemoTool:
    name = "paper_executor"
    enabled = True
    def run(self, **kwargs):
        return {"status": "ok"}


registry = SimpleNamespace(tools=[DemoTool()], run_tool=lambda t, **kw: type("R", (), {"__dict__": t.run(**kw)})())


class TestOrchestrator(unittest.TestCase):
    def setUp(self):
        self.orch = Orchestrator(rag=FakeRag(), tool_registry=registry, require_confirmation=True)

    def test_research_success(self):
        findings, proposal = self.orch.research_then_propose("demo")
        self.assertEqual(findings.query, "demo")
        self.assertGreaterEqual(len(findings.structured), 1)

    def test_research_failure(self):
        orch = Orchestrator(rag=None, tool_registry=registry, require_confirmation=False)
        findings, proposal = orch.research_then_propose("demo")
        self.assertEqual(findings.structured, [])
        self.assertEqual(proposal.status, ProposalStatus.REJECTED)

    def test_structured_findings(self):
        findings, proposal = self.orch.research_then_propose("demo")
        self.assertEqual(findings.structured[0]["source"], "doc1")

    def test_proposal_created(self):
        _, proposal = self.orch.research_then_propose("demo")
        self.assertIsNotNone(proposal.proposal_id)

    def test_invalid_proposal_rejected(self):
        orch = Orchestrator(rag=FakeRag(), tool_registry=registry, require_confirmation=False)
        findings, proposal = orch.research_then_propose("")
        self.assertEqual(proposal.status, ProposalStatus.REJECTED)

    def test_confirmation_required(self):
        _, proposal = self.orch.research_then_propose("demo")
        state = self.orch.build_workflow(proposal)
        self.assertTrue(any(s.requires_confirmation for s in state.steps))

    def test_execution_blocked_before_confirmation(self):
        _, proposal = self.orch.research_then_propose("demo")
        result = self.orch.execute_after_confirmation(proposal, approved=False)
        self.assertEqual(result.status, StepStatus.FAILED)
        self.assertEqual(result.steps[0].status, StepStatus.REJECTED)

    def test_approval_allows_execution(self):
        _, proposal = self.orch.research_then_propose("demo")
        result = self.orch.execute_after_confirmation(proposal, approved=True)
        self.assertEqual(result.status, StepStatus.COMPLETED)
        self.assertEqual(result.steps[0].status, StepStatus.COMPLETED)

    def test_rejection_prevents_execution(self):
        _, proposal = self.orch.research_then_propose("demo")
        result = self.orch.execute_after_confirmation(proposal, approved=False)
        self.assertEqual(result.steps[0].status, StepStatus.REJECTED)

    def test_cancellation(self):
        calls = {"count": 0}
        def cancel():
            calls["count"] += 1
            return True
        orch = Orchestrator(rag=FakeRag(), tool_registry=registry, require_confirmation=False)
        _, proposal = orch.research_then_propose("demo")
        state = orch.build_workflow(proposal)
        result = orch.executor.execute(state, cancel_callback=cancel)
        self.assertEqual(result.status, StepStatus.CANCELLED)

    def test_retry(self):
        class FlakyTool:
            name = "paper_executor"; enabled = True
            def __init__(self):
                self.calls = 0
            def run(self, **kwargs):
                self.calls += 1
                if self.calls < 2:
                    raise RuntimeError("transient")
                return {"ok": True}
        flaky = FlakyTool()
        reg = SimpleNamespace(tools=[flaky], run_tool=lambda t, **kw: type("R", (), {"__dict__": t.run(**kw)})())
        orch = Orchestrator(rag=FakeRag(), tool_registry=reg, require_confirmation=False)
        _, proposal = orch.research_then_propose("demo")
        result = orch.execute_after_confirmation(proposal, approved=True)
        self.assertEqual(result.status, StepStatus.COMPLETED)

    def test_paper_simulation(self):
        orch = Orchestrator(rag=FakeRag(), tool_registry=registry, require_confirmation=False)
        orch.simulation.state.update_market("DEMO", 100.0)
        mgr = ProposalManager()
        proposal = mgr.create_proposal(objective="demo", actions=[{"tool":"paper_executor","description":"run","parameters":{"instrument":"DEMO","quantity":1}}])
        result = orch.run_paper_execution(proposal)
        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["filled_price"], 100.0)

    def test_audit_event_logging(self):
        bus = SimpleNamespace(publish=lambda event: None)
        orch = Orchestrator(rag=FakeRag(), tool_registry=registry, require_confirmation=False, event_bus=bus)
        _, proposal = orch.research_then_propose("demo")
        orch.events.proposal_created(proposal)
        # event bridge uses bus.publish; no exception means accepted path

    def test_complete_end_to_end(self):
        orch = Orchestrator(rag=FakeRag(), tool_registry=registry, require_confirmation=True)
        orch.simulation.state.update_market("DEMO", 100.0)
        mgr = ProposalManager()
        proposal = mgr.create_proposal(objective="demo", actions=[{"tool":"paper_executor","description":"run","parameters":{"instrument":"DEMO","quantity":1}}])
        result = orch.run_paper_execution(proposal)
        self.assertEqual(result["status"], "executed")
        self.assertIn("order_id", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
