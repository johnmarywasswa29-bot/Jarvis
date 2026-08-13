"""Proposal manager: creation, validation, lifecycle."""
from __future__ import annotations

from typing import Any

from proposal.state import Proposal, ProposalStatus, ProposedAction, SourceReference, ProposalRiskLevel
from proposal.validator import ProposalValidator, ProposalValidationError


class ProposalManager:
    def __init__(self, validator: ProposalValidator | None = None) -> None:
        self.validator = validator or ProposalValidator()
        self._store: dict[str, Proposal] = {}

    def create_proposal(
        self,
        *,
        objective: str,
        actions: list[dict[str, Any]] | None = None,
        sources: list[dict[str, Any]] | None = None,
        risk_level: ProposalRiskLevel = ProposalRiskLevel.MEDIUM,
        requires_confirmation: bool = True,
        affected_context: dict[str, Any] | None = None,
        audit_metadata: dict[str, Any] | None = None,
    ) -> Proposal:
        proposed_actions = [ProposedAction(tool=a.get("tool", ""), description=a.get("description", ""), parameters=a.get("parameters", {}), dependencies=a.get("dependencies", [])) for a in (actions or [])]
        source_refs = [SourceReference(source_type=s.get("source_type", ""), identifier=s.get("identifier", ""), excerpt=s.get("excerpt", ""), metadata=s.get("metadata", {})) for s in (sources or [])]
        proposal = Proposal(objective=objective, proposed_actions=proposed_actions, source_references=source_refs, risk_level=risk_level, requires_confirmation=requires_confirmation, affected_context=affected_context or {}, audit_metadata=audit_metadata or {})
        self.validator.validate(proposal)
        self._store[proposal.proposal_id] = proposal
        return proposal

    def get(self, proposal_id: str) -> Proposal | None:
        return self._store.get(proposal_id)

    def mark(self, proposal_id: str, status: ProposalStatus) -> Proposal | None:
        proposal = self._store.get(proposal_id)
        if proposal:
            proposal.status = status
        return proposal
