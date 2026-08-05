"""Repository protocol definitions.

These interfaces form the stable contract between domain services and
persistence implementations.  Services depend only on these protocols;
concrete implementations live in the infrastructure layer.
"""

from __future__ import annotations

from typing import Protocol

from boardmatch.models import Candidate, Opportunity


class CandidateRepository(Protocol):
    """Provides user-scoped access to candidate profiles."""

    def get_for_user(self, user_id: str) -> Candidate | None:
        """Return the candidate profile owned by the specified user."""
        ...

    def save_for_user(self, user_id: str, candidate: Candidate) -> Candidate:
        """Save and return the updated candidate profile."""
        ...


class OpportunityRepository(Protocol):
    """Provides access to canonical board opportunities."""

    def get_by_id(self, opportunity_id: str) -> Opportunity | None:
        """Return one opportunity or None when not found."""
        ...

    def search(self, **filters: object) -> list[Opportunity]:
        """Return opportunities matching the requested filters."""
        ...
