"""Proposal state and typed models."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from typing import Any


class ProposalStatus(str, Enum):
    DRAFT = "draft"
    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONFIRMED = "confirmed"
    EXECUTED = "executed"
    CANCELLED = "cancelled"


class ProposalRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class SourceReference:
    source_type: str
    identifier: str
    excerpt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProposedAction:
    action_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    tool: str = ""
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)


@dataclass
class Proposal:
    proposal_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    objective: str = ""
    source_references: list[SourceReference] = field(default_factory=list)
    proposed_actions: list[ProposedAction] = field(default_factory=list)
    affected_context: dict[str, Any] = field(default_factory=dict)
    risk_level: ProposalRiskLevel = ProposalRiskLevel.MEDIUM
    requires_confirmation: bool = True
    status: ProposalStatus = ProposalStatus.DRAFT
    validation_errors: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())
    expires_at: str = ""
    audit_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.expires_at:
            self.expires_at = self._default_expiry()

    def _default_expiry(self) -> str:
        from datetime import timedelta
        return (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None).isoformat()

    def is_expired(self) -> bool:
        try:
            return datetime.now(UTC).replace(tzinfo=None).isoformat() > self.expires_at
        except Exception:
            return True
