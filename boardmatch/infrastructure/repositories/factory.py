"""Repository factory — selects memory vs DB-backed repositories.

Local and test environments (SQLite ``DATABASE_URL``) continue to use the
in-memory repositories to avoid changing existing test behavior. Any other
scheme (e.g. ``postgresql://``, ``postgresql+psycopg://``) is treated as a
durable database and wired to the SQLAlchemy-backed repositories.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from boardmatch.config import Settings
from boardmatch.infrastructure.db.engine import get_session_factory
from boardmatch.infrastructure.repositories.db import (
    DbApplicationRepository,
    DbCandidateRepository,
    DbFitEvaluationRepository,
    DbOpportunityRepository,
)
from boardmatch.infrastructure.repositories.memory import (
    InMemoryApplicationRepository,
    InMemoryCandidateRepository,
    InMemoryFitEvaluationRepository,
    InMemoryOpportunityRepository,
)
from boardmatch.models import Opportunity

CandidateRepo = InMemoryCandidateRepository | DbCandidateRepository
OpportunityRepo = InMemoryOpportunityRepository | DbOpportunityRepository
ApplicationRepo = InMemoryApplicationRepository | DbApplicationRepository
FitEvaluationRepo = InMemoryFitEvaluationRepository | DbFitEvaluationRepository


@dataclass(frozen=True)
class Repositories:
    """Bundle of repository instances sharing a persistence backend."""

    candidate_repo: CandidateRepo
    opportunity_repo: OpportunityRepo
    application_repo: ApplicationRepo
    fit_evaluation_repo: FitEvaluationRepo


# Process-wide cache of in-memory repository bundles, keyed by
# ``Settings.database_url``. Every router module calls ``create_repositories``
# independently at import time; without this cache each module would get its
# own throwaway ``InMemory*Repository`` instances and data created via one
# router (e.g. an opportunity) would be invisible to another router (e.g.
# fit evaluations), even though they're conceptually the same store. DB-backed
# repositories don't need this because they already share state through the
# cached engine/session factory in ``boardmatch.infrastructure.db.engine``.
_repositories_cache: dict[str, Repositories] = {}


def uses_database_backend(database_url: str) -> bool:
    """Return True when the URL should be served by DB-backed repositories.

    SQLite URLs keep using the in-memory repositories (matching current
    local/test behavior); every other scheme is treated as a real database.
    """
    scheme = urlparse(database_url).scheme
    return not scheme.startswith("sqlite")


def create_repositories(
    settings: Settings, *, seed_opportunities: list[Opportunity] | None = None
) -> Repositories:
    """Build the repository bundle appropriate for ``settings.database_url``.

    In-memory bundles are cached per ``database_url`` so every caller (each
    router module) shares the same repository instances — matching how the
    DB-backed repositories already share state via a single connection.
    """
    if uses_database_backend(settings.database_url):
        session_factory = get_session_factory(settings.database_url)
        return Repositories(
            candidate_repo=DbCandidateRepository(session_factory),
            opportunity_repo=DbOpportunityRepository(
                session_factory, opportunities=seed_opportunities
            ),
            application_repo=DbApplicationRepository(session_factory),
            fit_evaluation_repo=DbFitEvaluationRepository(session_factory),
        )

    cached = _repositories_cache.get(settings.database_url)
    if cached is None:
        cached = Repositories(
            candidate_repo=InMemoryCandidateRepository(),
            opportunity_repo=InMemoryOpportunityRepository(seed_opportunities),
            application_repo=InMemoryApplicationRepository(),
            fit_evaluation_repo=InMemoryFitEvaluationRepository(),
        )
        _repositories_cache[settings.database_url] = cached
    elif seed_opportunities:
        # A different caller already created the bundle (module import order
        # isn't guaranteed) — merge any seed opportunities into the existing
        # shared repo instead of silently dropping them.
        for opportunity in seed_opportunities:
            if cached.opportunity_repo.get_by_id(opportunity.id) is None:
                cached.opportunity_repo.add(opportunity)

    return cached


def reset_repositories_cache() -> None:
    """Clear the cached in-memory repository bundles (primarily for tests)."""
    _repositories_cache.clear()
