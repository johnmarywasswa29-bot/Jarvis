"""Result types for intent confidence engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ExecutionStrategy(str, Enum):
    execute_immediately = "execute_immediately"
    require_confirmation = "require_confirmation"
    ask_clarification = "ask_clarification"
    llm_reasoning = "llm_reasoning"


@dataclass
class IntentResult:
    intent: str
    confidence: float
    entities: dict[str, Any] = field(default_factory=dict)
    strategy: ExecutionStrategy = ExecutionStrategy.llm_reasoning
    explanation: str = ""
    source_signals: dict[str, float] = field(default_factory=dict)
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "entities": self.entities,
            "strategy": self.strategy.value,
            "explanation": self.explanation,
            "source_signals": self.source_signals,
            "latency_ms": self.latency_ms,
        }


@dataclass
class ExecutionPolicy:
    min_confidence_immediate: float = 0.98
    min_confidence_confirm: float = 0.80
    min_confidence_clarify: float = 0.70

    destructive_actions: tuple[str, ...] = (
        "filesystem.delete",
        "system_control.shutdown",
        "system_control.restart",
        "system_control.format",
        "system_control.uninstall",
        "filesystem.overwrite",
        "email.send",
    )

    def decide(self, result: IntentResult) -> ExecutionStrategy:
        if result.confidence >= self.min_confidence_immediate:
            if result.intent in self.destructive_actions:
                return ExecutionStrategy.require_confirmation
            return ExecutionStrategy.execute_immediately
        if result.confidence >= self.min_confidence_confirm:
            if result.intent in self.destructive_actions:
                return ExecutionStrategy.require_confirmation
            return ExecutionStrategy.execute_immediately
        if result.confidence >= self.min_confidence_clarify:
            return ExecutionStrategy.ask_clarification
        return ExecutionStrategy.llm_reasoning
