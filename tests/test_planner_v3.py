"""Tests for Planner V3."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1] / "Desktop" / "jarvis"
if not REPO.exists():
    REPO = Path.cwd()
os.chdir(REPO)
sys.path.insert(0, str(REPO))


class TestPlanStep(unittest.TestCase):
    def test_defaults(self):
        from modules.planner_v3 import PlanStep
        s = PlanStep(id="1", description="do it")
        self.assertEqual(s.tool, "")
        self.assertEqual(s.arguments, {})
        self.assertEqual(s.depends_on, [])
        self.assertEqual(s.estimated_duration, 0.0)
        self.assertEqual(s.confidence, 1.0)
        self.assertFalse(s.requires_confirmation)

    def test_validation_rejects_empty_id(self):
        from modules.planner_v3 import Plan, PlanStep, PlanValidationError
        with self.assertRaises(PlanValidationError):
            Plan([PlanStep(id="", description="x")])

    def test_validation_rejects_duplicate_ids(self):
        from modules.planner_v3 import Plan, PlanStep, PlanValidationError
        with self.assertRaises(PlanValidationError):
            Plan([PlanStep(id="1", description="a"), PlanStep(id="1", description="b")])

    def test_validation_allows_empty_plan(self):
        from modules.planner_v3 import Plan, PlanStep, PlanValidationError
        with self.assertRaises(PlanValidationError):
            Plan([])

    def test_dependency_resolution(self):
        from modules.planner_v3 import Plan, PlanStep
        steps = [
            PlanStep(id="a", description="first", depends_on=[]),
            PlanStep(id="b", description="second", depends_on=["a"]),
        ]
        p = Plan(steps)
        order = [s.id for s in p.steps]
        self.assertEqual(order, ["a", "b"])

    def test_missing_dependency_raises(self):
        from modules.planner_v3 import Plan, PlanStep, PlanValidationError
        with self.assertRaises(PlanValidationError):
            Plan([PlanStep(id="a", description="x", depends_on=["missing"])])

    def test_serialization_roundtrip(self):
        from modules.planner_v3 import Plan, PlanStep
        p = Plan([
            PlanStep(id="1", description="search", tool="web_search", arguments={"query": "x"}, depends_on=[], estimated_duration=1.5, confidence=0.8, requires_confirmation=False),
            PlanStep(id="2", description="read", tool="filesystem", arguments={"source": "f"}, depends_on=["1"], estimated_duration=0.5, confidence=1.0, requires_confirmation=True),
        ])
        data = p.to_dict()
        p2 = Plan.from_dict(data)
        self.assertEqual([s.id for s in p2.steps], ["1", "2"])
        self.assertEqual(p2.steps[1].tool, "filesystem")
        self.assertTrue(p2.steps[1].requires_confirmation)

    def test_to_legacy_format(self):
        from modules.planner_v3 import Plan, PlanStep
        p = Plan([PlanStep(id="1", description="search", tool="web_search", arguments={"query": "x"}, requires_confirmation=True)])
        legacy = p.to_legacy()
        self.assertIn("1: search", legacy)
        self.assertIn("tool=web_search", legacy)
        self.assertIn("NEEDS_CONFIRMATION", legacy)

    def test_negative_duration_raises(self):
        from modules.planner_v3 import Plan, PlanStep, PlanValidationError
        with self.assertRaises(PlanValidationError):
            Plan([PlanStep(id="1", description="x", estimated_duration=-1)])

    def test_invalid_confidence_raises(self):
        from modules.planner_v3 import Plan, PlanStep, PlanValidationError
        with self.assertRaises(PlanValidationError):
            Plan([PlanStep(id="1", description="x", confidence=1.5)])


if __name__ == "__main__":
    unittest.main()
