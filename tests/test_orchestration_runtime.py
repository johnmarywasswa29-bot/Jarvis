"""Focused runtime integration tests for orchestration wiring."""
from __future__ import annotations

import unittest
from pathlib import Path
import sys

repo = Path(r"C:\Users\User NA\Desktop\jarvis")
sys.path.insert(0, str(repo))

from modules.config import JarvisConfig
from runtime.runtime import build_runtime
from proposal.state import ProposalRiskLevel


def build_ctx():
    return build_runtime(config=JarvisConfig(project_root=repo), repo=repo)


class TestOrchestrationRuntimeIntegration(unittest.TestCase):
    def test_runtime_exposes_orchestration(self):
        ctx = build_ctx()
        self.assertIsNotNone(ctx.orchestration)
        self.assertTrue(hasattr(ctx.orchestration, "research_then_propose"))

    def test_orchestration_reuses_event_bus(self):
        ctx = build_ctx()
        self.assertIs(ctx.orchestration.events.bus, ctx.event_bus)

    def test_orchestration_reuses_tool_registry(self):
        ctx = build_ctx()
        self.assertIs(ctx.orchestration.executor.tool_registry, ctx.tool_registry)

    def test_orchestration_reuses_knowledge(self):
        ctx = build_ctx()
        self.assertIs(ctx.orchestration.research.rag, ctx.knowledge)

    def test_build_runtime_preserves_lazy_knowledge(self):
        ctx = build_ctx()
        self.assertEqual(type(ctx.knowledge).__name__, "_LazyKnowledgeEngine")

    def test_invalid_proposal_is_rejected_safely(self):
        ctx = build_ctx()
        orch = ctx.orchestration
        findings, proposal = orch.research_then_propose("")
        status = proposal.status.value if hasattr(proposal.status, "value") else str(proposal.status)
        self.assertEqual(status, "rejected")

    def test_confirmation_required_blocks_execution(self):
        ctx = build_ctx()
        orch = ctx.orchestration
        proposal = orch.proposal_manager.create_proposal(
            objective="runtime confirmation",
            actions=[{"tool": "paper_executor", "description": "simulated", "parameters": {"instrument": "AAA", "quantity": 1, "order_type": "market"}}],
            sources=[{"source_type": "rag", "identifier": "runtime", "excerpt": "test"}],
            risk_level=ProposalRiskLevel.MEDIUM,
        )
        state = orch.execute_after_confirmation(proposal, approved=False)
        status = state.status.value if hasattr(state.status, "value") else str(state.status)
        self.assertEqual(status, "failed")

    def test_orchestration_in_all_managers(self):
        ctx = build_ctx()
        managers = ctx.all_managers()
        self.assertIn("orchestration", managers)
        self.assertIs(managers["orchestration"], ctx.orchestration)


if __name__ == "__main__":
    unittest.main(verbosity=2)
