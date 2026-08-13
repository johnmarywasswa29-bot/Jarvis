"""Proposal validation."""
from __future__ import annotations

from typing import Any

from proposal.state import Proposal, ProposedAction, ProposalStatus


class ProposalValidationError(Exception):
    """Raised when a proposal is invalid."""


class ProposalValidator:
    def validate(self, proposal: Proposal) -> Proposal:
        proposal.validation_errors = []
        if not proposal.objective.strip():
            proposal.validation_errors.append("objective is empty")
        if not proposal.proposed_actions:
            proposal.validation_errors.append("no proposed actions")
        for action in proposal.proposed_actions:
            self._validate_action(action, proposal)
        if proposal.is_expired():
            proposal.status = ProposalStatus.EXPIRED
        elif proposal.validation_errors:
            proposal.status = ProposalStatus.REJECTED
        else:
            proposal.status = ProposalStatus.VALIDATED
        return proposal

    def _validate_action(self, action: ProposedAction, proposal: Proposal) -> None:
        if not action.tool and not action.description:
            proposal.validation_errors.append("action missing tool/description")
        if action.parameters is None:
            proposal.validation_errors.append(f"action {action.action_id} missing parameters")
