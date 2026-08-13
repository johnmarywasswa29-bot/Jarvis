"""Focused tests for proposal package."""
from __future__ import annotations

import unittest

from proposal.state import Proposal, SourceReference, ProposedAction, ProposalStatus, ProposalRiskLevel
from proposal.validator import ProposalValidator, ProposalValidationError
from proposal.manager import ProposalManager
from research.orchestrator import ResearchOrchestrator, ResearchFindings


class TestProposalValidator(unittest.TestCase):
    def setUp(self):
        self.validator = ProposalValidator()

    def test_valid_proposal(self):
        proposal = Proposal(objective="demo", proposed_actions=[ProposedAction(tool="demo", description="run", parameters={"x": 1})])
        result = self.validator.validate(proposal)
        self.assertEqual(result.status, ProposalStatus.VALIDATED)
        self.assertEqual(result.validation_errors, [])

    def test_empty_objective_rejected(self):
        proposal = Proposal(objective="   ")
        result = self.validator.validate(proposal)
        self.assertEqual(result.status, ProposalStatus.REJECTED)
        self.assertTrue(any("objective" in e for e in result.validation_errors))

    def test_empty_actions_rejected(self):
        proposal = Proposal(objective="demo", proposed_actions=[])
        result = self.validator.validate(proposal)
        self.assertEqual(result.status, ProposalStatus.REJECTED)
        self.assertTrue(any("no proposed actions" in e for e in result.validation_errors))

    def test_missing_tool_description_rejected(self):
        proposal = Proposal(objective="demo", proposed_actions=[ProposedAction(tool="", description="")])
        result = self.validator.validate(proposal)
        self.assertEqual(result.status, ProposalStatus.REJECTED)
        self.assertTrue(any("missing tool/description" in e for e in result.validation_errors))

    def test_missing_parameters_rejected(self):
        proposal = Proposal(objective="demo", proposed_actions=[ProposedAction(tool="demo", description="run", parameters=None)])
        result = self.validator.validate(proposal)
        self.assertEqual(result.status, ProposalStatus.REJECTED)
        self.assertTrue(any("missing parameters" in e for e in result.validation_errors))

    def test_expired_proposal_rejected(self):
        from datetime import datetime, timedelta, UTC
        proposal = Proposal(objective="demo", proposed_actions=[ProposedAction(tool="demo", description="run", parameters={})])
        proposal.expires_at = (datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)).isoformat()
        result = self.validator.validate(proposal)
        self.assertEqual(result.status, ProposalStatus.EXPIRED)


class TestProposalManager(unittest.TestCase):
    def test_create_valid_proposal(self):
        mgr = ProposalManager()
        p = mgr.create_proposal(objective="demo", actions=[{"tool": "demo", "description": "run", "parameters": {}}])
        self.assertEqual(p.status, ProposalStatus.VALIDATED)
        self.assertIsNotNone(mgr.get(p.proposal_id))

    def test_mark_status(self):
        mgr = ProposalManager()
        p = mgr.create_proposal(objective="demo", actions=[{"tool": "demo", "description": "run", "parameters": {}}])
        self.assertIsNotNone(mgr.mark(p.proposal_id, ProposalStatus.CONFIRMED))
        self.assertEqual(p.status, ProposalStatus.CONFIRMED)


class TestResearchOrchestrator(unittest.TestCase):
    def test_empty_query(self):
        orch = ResearchOrchestrator()
        findings = orch.research("")
        self.assertEqual(findings.query, "")
        self.assertEqual(findings.structured, [])

    def test_no_backend(self):
        orch = ResearchOrchestrator()
        findings = orch.research("demo")
        self.assertEqual(findings.structured, [])

    def test_normalizes_results(self):
        class FakeRag:
            def search(self, query, k=5):
                return [{"text": "hello world", "source": "doc1", "score": 0.9}]
        orch = ResearchOrchestrator(rag=FakeRag())
        findings = orch.research("demo")
        self.assertEqual(len(findings.structured), 1)
        self.assertEqual(findings.structured[0]["source"], "doc1")

    def test_skips_non_dict(self):
        class FakeRag:
            def search(self, query, k=5):
                return ["bad", {"text": "good"}]
        orch = ResearchOrchestrator(rag=FakeRag())
        findings = orch.research("demo")
        self.assertEqual(len(findings.structured), 1)

    def test_context_output(self):
        orch = ResearchOrchestrator()
        findings = ResearchFindings(query="q", results=[], structured=[{"source": "s", "text": "t"}])
        ctx = findings.as_context()
        self.assertIn("q", ctx)


if __name__ == "__main__":
    unittest.main(verbosity=2)
