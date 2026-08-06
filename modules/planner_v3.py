"""Planner V3: structured plan objects, validation, and legacy fallback."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


class PlanValidationError(Exception):
    """Raised when a plan cannot be executed safely."""


@dataclass
class PlanStep:
    id: str
    description: str
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    estimated_duration: float = 0.0
    confidence: float = 1.0
    requires_confirmation: bool = False


class Plan:
    def __init__(self, steps: list[PlanStep]) -> None:
        if not steps:
            raise PlanValidationError("Plan must contain at least one step")
        self.steps = list(steps)
        self.created_at = time.time()
        self._validate()

    def _validate(self) -> None:
        ids = {s.id for s in self.steps}
        if len(ids) != len(self.steps):
            raise PlanValidationError("Duplicate step ids detected")
        for step in self.steps:
            if not step.id or not step.description:
                raise PlanValidationError(f"Invalid step: {step.id}")
            missing = [dep for dep in step.depends_on if dep not in ids]
            if missing:
                raise PlanValidationError(f"Step {step.id} missing deps: {missing}")
            if step.confidence < 0 or step.confidence > 1:
                raise PlanValidationError(f"Step {step.id} has invalid confidence")
            if step.estimated_duration < 0:
                raise PlanValidationError(f"Step {step.id} has negative duration")

    def to_legacy(self) -> str:
        lines = []
        for step in self.steps:
            parts = [f"{step.id}: {step.description}"]
            if step.tool:
                parts.append(f"[tool={step.tool}]")
            if step.arguments:
                parts.append(f"args={json.dumps(step.arguments, ensure_ascii=False)}")
            if step.depends_on:
                parts.append(f"depends_on={','.join(step.depends_on)}")
            if step.estimated_duration:
                parts.append(f"~{step.estimated_duration:.1f}s")
            if step.confidence < 1:
                parts.append(f"conf={step.confidence:.0%}")
            if step.requires_confirmation:
                parts.append("NEEDS_CONFIRMATION")
            lines.append(" | ".join(parts))
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": [
                {
                    "id": s.id,
                    "description": s.description,
                    "tool": s.tool,
                    "arguments": dict(s.arguments),
                    "depends_on": list(s.depends_on),
                    "estimated_duration": s.estimated_duration,
                    "confidence": s.confidence,
                    "requires_confirmation": s.requires_confirmation,
                }
                for s in self.steps
            ],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        steps = [
            PlanStep(
                id=s["id"],
                description=s["description"],
                tool=s.get("tool", ""),
                arguments=s.get("arguments", {}),
                depends_on=s.get("depends_on", []),
                estimated_duration=float(s.get("estimated_duration", 0)),
                confidence=float(s.get("confidence", 1)),
                requires_confirmation=bool(s.get("requires_confirmation", False)),
            )
            for s in data.get("steps", [])
        ]
        return cls(steps)


def _default_tool_for(name: str) -> str:
    return name.split(":")[0]


def build_plan_from_transcript(
    transcript: str,
    selected_tools: list[str],
    *,
    llm_builder: Any | None = None,
) -> Plan:
    text = (transcript or "").strip()
    if not text:
        raise PlanValidationError("Empty transcript")

    tool = selected_tools[0] if selected_tools else ""
    step = PlanStep(id="1", description=text, tool=tool, arguments={"transcript": text})
    return Plan([step])
