"""In-memory repository implementations for testing and development."""

from __future__ import annotations

import uuid
from datetime import date

from boardmatch.domain.repositories import PaginatedResult
from boardmatch.models import Application, ApplicationStage, Candidate, FitEvaluation, Opportunity


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

    def _apply_filters(
        self, results: list[Opportunity], filters: dict[str, object]
    ) -> list[Opportunity]:
        """Apply composable filters to a list of opportunities."""
        if "sector" in filters:
            sector = str(filters["sector"]).lower()
            results = [o for o in results if o.sector.lower() == sector]

        if "location" in filters:
            location = str(filters["location"]).lower()
            results = [o for o in results if o.location.lower() == location]

        if "remuneration" in filters:
            rem = str(filters["remuneration"])
            results = [o for o in results if o.remuneration.value == rem]

        if "paid_only" in filters and filters["paid_only"]:
            results = [o for o in results if o.is_paid]

        if "min_fee" in filters:
            min_fee = int(str(filters["min_fee"]))
            results = [
                o for o in results if o.fee_aud is not None and o.fee_aud >= min_fee
            ]

        if "source" in filters:
            source = str(filters["source"]).lower()
            results = [o for o in results if o.source.lower() == source]

        if "closes_after" in filters:
            after = str(filters["closes_after"])
            results = [
                o for o in results
                if o.closes_on is not None and o.closes_on >= after
            ]

        if "closes_before" in filters:
            before = str(filters["closes_before"])
            results = [
                o for o in results
                if o.closes_on is not None and o.closes_on <= before
            ]

        if "status" in filters and str(filters["status"]).lower() == "open":
            today = date.today().isoformat()
            results = [
                o for o in results
                if o.closes_on is None or o.closes_on >= today
            ]

        return results

    def _sort_deterministic(self, results: list[Opportunity]) -> list[Opportunity]:
        """Sort results deterministically by closes_on (ascending), then id."""
        return sorted(results, key=lambda o: (o.closes_on or "9999-12-31", o.id))

    def search(self, **filters: object) -> list[Opportunity]:
        """Return opportunities matching the requested filters.

        Supported filters:
          - sector: str — case-insensitive match on opportunity sector
          - location: str — case-insensitive match on opportunity location
          - remuneration: str — match on remuneration value
          - min_fee: int — minimum annual fee (AUD)
          - paid_only: bool — only paid opportunities
          - source: str — case-insensitive match on source
          - closes_after: str — YYYY-MM-DD, inclusive lower bound
          - closes_before: str — YYYY-MM-DD, inclusive upper bound
          - status: str — "open" excludes expired opportunities
        """
        results = list(self._store.values())
        results = self._apply_filters(results, filters)
        return self._sort_deterministic(results)

    def search_paginated(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        **filters: object,
    ) -> PaginatedResult:
        """Return a paginated slice of opportunities matching filters."""
        results = list(self._store.values())
        results = self._apply_filters(results, filters)
        results = self._sort_deterministic(results)

        total = len(results)
        start = (page - 1) * page_size
        end = start + page_size
        return PaginatedResult(items=results[start:end], total=total)


class InMemoryApplicationRepository:
    """User-scoped in-memory store for board applications."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Application]] = {}

    def list_for_user(self, user_id: str) -> list[Application]:
        """Return all applications for a user."""
        return list(self._store.get(user_id, {}).values())

    def create(self, user_id: str, application: Application) -> Application:
        """Persist a new application, assigning an id."""
        if not application.id:
            application.id = str(uuid.uuid4())
        self._store.setdefault(user_id, {})[application.id] = application
        return application

    def get_by_id(self, user_id: str, application_id: str) -> Application | None:
        """Return a single application owned by the user."""
        return self._store.get(user_id, {}).get(application_id)

    def update(
        self,
        user_id: str,
        application_id: str,
        *,
        stage: ApplicationStage | None = None,
        notes: str | None = None,
    ) -> Application | None:
        """Update fields on an existing application."""
        app = self.get_by_id(user_id, application_id)
        if app is None:
            return None
        if stage is not None:
            app.stage = stage
        if notes is not None:
            app.notes = notes
        return app

    def delete(self, user_id: str, application_id: str) -> bool:
        """Delete an application. Returns True if it existed."""
        user_apps = self._store.get(user_id, {})
        if application_id in user_apps:
            del user_apps[application_id]
            return True
        return False



class InMemoryFitEvaluationRepository:
    """User-scoped in-memory store for fit evaluations."""

    def __init__(self) -> None:
        self._store: dict[str, list[FitEvaluation]] = {}

    def find_existing(
        self,
        user_id: str,
        opportunity_id: str,
        profile_version: int,
        scoring_version: str,
    ) -> FitEvaluation | None:
        """Find an existing evaluation matching the exact version tuple."""
        for ev in self._store.get(user_id, []):
            if (
                ev.opportunity_id == opportunity_id
                and ev.profile_version == profile_version
                and ev.scoring_version == scoring_version
            ):
                return ev
        return None

    def create(self, evaluation: FitEvaluation) -> FitEvaluation:
        """Persist a new evaluation."""
        self._store.setdefault(evaluation.user_id, []).append(evaluation)
        return evaluation

    def list_for_user(self, user_id: str) -> list[FitEvaluation]:
        """Return all evaluations for a user, newest first."""
        evals = self._store.get(user_id, [])
        return sorted(evals, key=lambda e: e.created_at, reverse=True)

    def get_by_id(self, user_id: str, evaluation_id: str) -> FitEvaluation | None:
        """Return a single evaluation owned by the user."""
        for ev in self._store.get(user_id, []):
            if ev.id == evaluation_id:
                return ev
        return None
