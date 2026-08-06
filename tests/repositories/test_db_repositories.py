"""Contract tests for DB-backed repositories.

Runs the same behavioral contract as the in-memory repositories against
SQLAlchemy-backed implementations. By default this uses an ephemeral
in-memory SQLite database created from the ORM metadata directly (not via
Alembic, to keep these tests fast and independent of a running database).

To additionally validate against a real Postgres database (exercising
Postgres-specific behavior such as the statement-timeout connection hook,
which is skipped for SQLite engines by design), set the
``TEST_DATABASE_URL`` environment variable to a Postgres connection string
(e.g. ``postgresql+psycopg://user:pass@localhost:5432/db``) before running
this test module. CI runs this suite twice: once with the SQLite default,
and once with ``TEST_DATABASE_URL`` pointed at a real ``postgres:16``
service container.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from boardmatch.domain.repositories import CandidateRepository, OpportunityRepository
from boardmatch.infrastructure.db.orm import Base
from boardmatch.infrastructure.repositories.db import (
    DbApplicationRepository,
    DbCandidateRepository,
    DbFitEvaluationRepository,
    DbOpportunityRepository,
)
from boardmatch.models import (
    Application,
    ApplicationEvent,
    ApplicationStage,
    Candidate,
    FitEvaluation,
    Opportunity,
    Remuneration,
)


@pytest.fixture
def session_factory():
    database_url = os.environ.get("TEST_DATABASE_URL", "sqlite:///:memory:")
    engine = create_engine(database_url, future=True)
    # Drop first so re-runs against a persistent (non-in-memory) database
    # such as a CI Postgres service start from a clean slate.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def candidate_repo(session_factory) -> DbCandidateRepository:
    return DbCandidateRepository(session_factory)


@pytest.fixture
def opportunity_repo(session_factory) -> DbOpportunityRepository:
    return DbOpportunityRepository(session_factory)


@pytest.fixture
def application_repo(session_factory) -> DbApplicationRepository:
    return DbApplicationRepository(session_factory)


@pytest.fixture
def fit_evaluation_repo(session_factory) -> DbFitEvaluationRepository:
    return DbFitEvaluationRepository(session_factory)


def _make_candidate(name: str = "Alice Smith") -> Candidate:
    return Candidate(
        name=name,
        headline="CFO turned board director",
        years_experience=15,
        skills=["Finance", "Governance"],
        sectors=["Healthcare"],
    )


def _make_opportunity(
    id: str = "opp-1",
    sector: str = "Healthcare",
    location: str = "Sydney",
    remuneration: Remuneration = Remuneration.PAID,
    fee_aud: int | None = 50_000,
) -> Opportunity:
    return Opportunity(
        id=id,
        title="Non-Executive Director",
        organisation="HealthCorp",
        sector=sector,
        location=location,
        source="aicd",
        url="https://example.com/opp",
        remuneration=remuneration,
        fee_aud=fee_aud,
    )


class TestProtocolConformance:
    """Verify DB-backed implementations satisfy Protocol structural typing."""

    def test_candidate_repo_satisfies_protocol(self, candidate_repo) -> None:
        repo: CandidateRepository = candidate_repo
        assert hasattr(repo, "get_for_user")
        assert hasattr(repo, "save_for_user")

    def test_opportunity_repo_satisfies_protocol(self, opportunity_repo) -> None:
        repo: OpportunityRepository = opportunity_repo
        assert hasattr(repo, "get_by_id")
        assert hasattr(repo, "search")


class TestDbCandidateRepository:
    def test_get_for_user_returns_none_when_empty(self, candidate_repo) -> None:
        assert candidate_repo.get_for_user("user-1") is None

    def test_save_and_retrieve(self, candidate_repo) -> None:
        candidate = _make_candidate()
        saved = candidate_repo.save_for_user("user-1", candidate)
        assert saved is candidate
        fetched = candidate_repo.get_for_user("user-1")
        assert fetched is not None
        assert fetched.name == candidate.name
        assert fetched.skills == candidate.skills

    def test_save_overwrites_existing(self, candidate_repo) -> None:
        candidate_repo.save_for_user("user-1", _make_candidate("First"))
        candidate_repo.save_for_user("user-1", _make_candidate("Second"))
        fetched = candidate_repo.get_for_user("user-1")
        assert fetched.name == "Second"

    def test_user_isolation(self, candidate_repo) -> None:
        candidate_repo.save_for_user("user-alice", _make_candidate("Alice"))
        candidate_repo.save_for_user("user-bob", _make_candidate("Bob"))

        assert candidate_repo.get_for_user("user-alice").name == "Alice"
        assert candidate_repo.get_for_user("user-bob").name == "Bob"
        assert candidate_repo.get_for_user("user-unknown") is None


class TestDbOpportunityRepository:
    def test_get_by_id_returns_none_when_empty(self, opportunity_repo) -> None:
        assert opportunity_repo.get_by_id("nonexistent") is None

    def test_get_by_id_returns_opportunity(self, opportunity_repo) -> None:
        opp = _make_opportunity()
        opportunity_repo.add(opp)
        fetched = opportunity_repo.get_by_id("opp-1")
        assert fetched is not None
        assert fetched.id == "opp-1"
        assert fetched.remuneration == Remuneration.PAID

    def test_search_returns_all_when_no_filters(self, opportunity_repo) -> None:
        opportunity_repo.add(_make_opportunity(id="opp-1"))
        opportunity_repo.add(_make_opportunity(id="opp-2", sector="Finance"))
        assert len(opportunity_repo.search()) == 2

    def test_search_filter_by_sector_case_insensitive(self, opportunity_repo) -> None:
        opportunity_repo.add(_make_opportunity(id="opp-1", sector="Healthcare"))
        results = opportunity_repo.search(sector="healthcare")
        assert len(results) == 1

    def test_search_filter_by_min_fee(self, opportunity_repo) -> None:
        opportunity_repo.add(_make_opportunity(id="opp-1", fee_aud=80_000))
        opportunity_repo.add(_make_opportunity(id="opp-2", fee_aud=20_000))
        opportunity_repo.add(_make_opportunity(id="opp-3", fee_aud=None))

        results = opportunity_repo.search(min_fee=50_000)
        assert len(results) == 1
        assert results[0].id == "opp-1"

    def test_search_paginated(self, opportunity_repo) -> None:
        for i in range(5):
            opportunity_repo.add(_make_opportunity(id=f"opp-{i}", fee_aud=10_000 * i))

        page = opportunity_repo.search_paginated(page=1, page_size=2)
        assert page.total == 5
        assert len(page.items) == 2

    def test_constructor_with_initial_opportunities(self, session_factory) -> None:
        opps = [_make_opportunity(id="a"), _make_opportunity(id="b")]
        repo = DbOpportunityRepository(session_factory, opportunities=opps)
        assert repo.get_by_id("a") is not None
        assert repo.get_by_id("b") is not None


class TestDbApplicationRepository:
    def test_create_and_list(self, application_repo) -> None:
        app = Application(opportunity_id="opp-1")
        created = application_repo.create("user-1", app)
        assert created.id
        assert application_repo.list_for_user("user-1")[0].id == created.id

    def test_get_by_id_wrong_user_returns_none(self, application_repo) -> None:
        app = application_repo.create("user-1", Application(opportunity_id="opp-1"))
        assert application_repo.get_by_id("user-2", app.id) is None
        assert application_repo.get_by_id("user-1", app.id) is not None

    def test_update_stage_and_notes(self, application_repo) -> None:
        app = application_repo.create("user-1", Application(opportunity_id="opp-1"))
        updated = application_repo.update(
            "user-1", app.id, stage=ApplicationStage.APPLIED, notes="Updated"
        )
        assert updated.stage == ApplicationStage.APPLIED
        assert updated.notes == "Updated"

    def test_delete(self, application_repo) -> None:
        app = application_repo.create("user-1", Application(opportunity_id="opp-1"))
        assert application_repo.delete("user-1", app.id) is True
        assert application_repo.get_by_id("user-1", app.id) is None
        assert application_repo.delete("user-1", app.id) is False

    def test_events_round_trip(self, application_repo) -> None:
        app = application_repo.create("user-1", Application(opportunity_id="opp-1"))
        import uuid
        from datetime import datetime, timezone

        event = ApplicationEvent(
            id=str(uuid.uuid4()),
            application_id=app.id,
            previous_stage=ApplicationStage.RESEARCHING,
            new_stage=ApplicationStage.APPLIED,
            timestamp=datetime.now(timezone.utc),
        )
        application_repo.add_event("user-1", event)
        events = application_repo.list_events("user-1", app.id)
        assert len(events) == 1
        assert events[0].new_stage == ApplicationStage.APPLIED


class TestDbFitEvaluationRepository:
    def test_create_and_list(self, fit_evaluation_repo) -> None:
        import uuid
        from datetime import datetime, timezone

        evaluation = FitEvaluation(
            id=str(uuid.uuid4()),
            user_id="user-1",
            opportunity_id="opp-1",
            profile_version=1,
            scoring_version="1.0.0",
            score=80,
            band="Strong fit",
            matched_skills=("Finance",),
            missing_skills=(),
            rationale=("Good match",),
            gap_actions=(),
            created_at=datetime.now(timezone.utc),
        )
        fit_evaluation_repo.create(evaluation)
        results = fit_evaluation_repo.list_for_user("user-1")
        assert len(results) == 1
        assert results[0].score == 80

    def test_find_existing(self, fit_evaluation_repo) -> None:
        import uuid
        from datetime import datetime, timezone

        evaluation = FitEvaluation(
            id=str(uuid.uuid4()),
            user_id="user-1",
            opportunity_id="opp-1",
            profile_version=1,
            scoring_version="1.0.0",
            score=80,
            band="Strong fit",
            matched_skills=(),
            missing_skills=(),
            rationale=(),
            gap_actions=(),
            created_at=datetime.now(timezone.utc),
        )
        fit_evaluation_repo.create(evaluation)
        found = fit_evaluation_repo.find_existing("user-1", "opp-1", 1, "1.0.0")
        assert found is not None
        assert fit_evaluation_repo.find_existing("user-1", "opp-1", 2, "1.0.0") is None
