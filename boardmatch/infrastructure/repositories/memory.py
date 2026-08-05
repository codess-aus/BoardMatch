"""In-memory repository implementations for testing and development."""

from __future__ import annotations

from boardmatch.models import Candidate, Opportunity


class InMemoryCandidateRepository:
    """User-scoped in-memory store for candidate profiles."""

    def __init__(self) -> None:
        self._store: dict[str, Candidate] = {}

    def get_for_user(self, user_id: str) -> Candidate | None:
        """Return the candidate profile owned by the specified user."""
        return self._store.get(user_id)

    def save_for_user(self, user_id: str, candidate: Candidate) -> Candidate:
        """Save and return the updated candidate profile."""
        self._store[user_id] = candidate
        return candidate


class InMemoryOpportunityRepository:
    """In-memory store for board opportunities."""

    def __init__(self, opportunities: list[Opportunity] | None = None) -> None:
        self._store: dict[str, Opportunity] = {}
        if opportunities:
            for opp in opportunities:
                self._store[opp.id] = opp

    def add(self, opportunity: Opportunity) -> None:
        """Seed an opportunity into the store (test helper)."""
        self._store[opportunity.id] = opportunity

    def get_by_id(self, opportunity_id: str) -> Opportunity | None:
        """Return one opportunity or None when not found."""
        return self._store.get(opportunity_id)

    def search(self, **filters: object) -> list[Opportunity]:
        """Return opportunities matching the requested filters.

        Supported filters:
          - sector: str — case-insensitive match on opportunity sector
          - location: str — case-insensitive match on opportunity location
          - remuneration: str — match on remuneration value
          - min_fee: int — minimum annual fee (AUD)
        """
        results = list(self._store.values())

        if "sector" in filters:
            sector = str(filters["sector"]).lower()
            results = [o for o in results if o.sector.lower() == sector]

        if "location" in filters:
            location = str(filters["location"]).lower()
            results = [o for o in results if o.location.lower() == location]

        if "remuneration" in filters:
            rem = str(filters["remuneration"])
            results = [o for o in results if o.remuneration.value == rem]

        if "min_fee" in filters:
            min_fee = int(str(filters["min_fee"]))
            results = [
                o for o in results if o.fee_aud is not None and o.fee_aud >= min_fee
            ]

        return results
