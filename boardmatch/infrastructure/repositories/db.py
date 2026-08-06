"""Database (SQLAlchemy) repository implementations.

These classes implement the exact same method signatures as their
in-memory counterparts in ``boardmatch.infrastructure.repositories.memory``,
so they can be swapped in via
``boardmatch.infrastructure.repositories.factory.create_repositories``
without any change to calling code.

Every database round-trip is timed and reported through
``boardmatch.monitoring.record_database_latency`` for observability.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from boardmatch.domain.repositories import PaginatedResult
from boardmatch.infrastructure.db.orm import (
    ApplicationEventRow,
    ApplicationRow,
    BoardOpportunityRow,
    CandidateRow,
    FitEvaluationRow,
)
from boardmatch.infrastructure.repositories.opportunity_filters import (
    apply_filters,
    sort_deterministic,
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
from boardmatch.monitoring import record_database_latency


@contextmanager
def _timed_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Open a session, committing on success, timing the whole operation."""
    started = time.perf_counter()
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        record_database_latency((time.perf_counter() - started) * 1000)


class DbCandidateRepository:
    """SQLAlchemy-backed store for candidate profiles."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_for_user(self, user_id: str) -> Candidate | None:
        """Return the candidate profile owned by the specified user."""
        with _timed_session(self._session_factory) as session:
            row = session.get(CandidateRow, user_id)
            return _candidate_from_row(row) if row else None

    def save_for_user(self, user_id: str, candidate: Candidate) -> Candidate:
        """Save and return the updated candidate profile."""
        with _timed_session(self._session_factory) as session:
            row = session.get(CandidateRow, user_id)
            if row is None:
                row = CandidateRow(user_id=user_id)
                session.add(row)
            _apply_candidate_to_row(row, candidate)
        return candidate


def _candidate_from_row(row: CandidateRow) -> Candidate:
    return Candidate(
        name=row.name,
        headline=row.headline,
        years_experience=row.years_experience,
        skills=list(row.skills or []),
        sectors=list(row.sectors or []),
        credentials=list(row.credentials or []),
        board_experience=list(row.board_experience or []),
        achievements=list(row.achievements or []),
        locations=list(row.locations or []),
        connections=[],
    )


def _apply_candidate_to_row(row: CandidateRow, candidate: Candidate) -> None:
    row.name = candidate.name
    row.headline = candidate.headline
    row.years_experience = candidate.years_experience
    row.skills = list(candidate.skills)
    row.sectors = list(candidate.sectors)
    row.credentials = list(candidate.credentials)
    row.board_experience = list(candidate.board_experience)
    row.achievements = list(candidate.achievements)
    row.locations = list(candidate.locations)


class DbOpportunityRepository:
    """SQLAlchemy-backed store for board opportunities."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        opportunities: list[Opportunity] | None = None,
    ) -> None:
        self._session_factory = session_factory
        if opportunities:
            for opp in opportunities:
                self.add(opp)

    def add(self, opportunity: Opportunity) -> None:
        """Insert or replace an opportunity."""
        with _timed_session(self._session_factory) as session:
            row = session.get(BoardOpportunityRow, opportunity.id)
            if row is None:
                row = BoardOpportunityRow(id=opportunity.id)
                session.add(row)
            _apply_opportunity_to_row(row, opportunity)

    def get_by_id(self, opportunity_id: str) -> Opportunity | None:
        """Return one opportunity or None when not found."""
        with _timed_session(self._session_factory) as session:
            row = session.get(BoardOpportunityRow, opportunity_id)
            return _opportunity_from_row(row) if row else None

    def _all(self, session: Session) -> list[Opportunity]:
        rows = session.execute(select(BoardOpportunityRow)).scalars().all()
        return [_opportunity_from_row(row) for row in rows]

    def search(self, **filters: object) -> list[Opportunity]:
        """Return opportunities matching the requested filters."""
        with _timed_session(self._session_factory) as session:
            results = self._all(session)
        results = apply_filters(results, filters)
        return sort_deterministic(results)

    def search_paginated(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        **filters: object,
    ) -> PaginatedResult:
        """Return a paginated slice of opportunities matching filters."""
        with _timed_session(self._session_factory) as session:
            results = self._all(session)
        results = apply_filters(results, filters)
        results = sort_deterministic(results)

        total = len(results)
        start = (page - 1) * page_size
        end = start + page_size
        return PaginatedResult(items=results[start:end], total=total)


def _opportunity_from_row(row: BoardOpportunityRow) -> Opportunity:
    return Opportunity(
        id=row.id,
        title=row.title,
        organisation=row.organisation,
        sector=row.sector,
        location=row.location,
        source=row.source,
        url=row.url,
        remuneration=Remuneration(row.remuneration),
        fee_aud=row.fee_aud,
        closes_on=row.closes_on,
        summary=row.summary,
        required_skills=tuple(row.required_skills or []),
        desirable_skills=tuple(row.desirable_skills or []),
    )


def _apply_opportunity_to_row(
    row: BoardOpportunityRow, opportunity: Opportunity
) -> None:
    row.title = opportunity.title
    row.organisation = opportunity.organisation
    row.sector = opportunity.sector
    row.location = opportunity.location
    row.source = opportunity.source
    row.url = opportunity.url
    row.remuneration = opportunity.remuneration.value
    row.fee_aud = opportunity.fee_aud
    row.closes_on = opportunity.closes_on
    row.summary = opportunity.summary
    row.required_skills = list(opportunity.required_skills)
    row.desirable_skills = list(opportunity.desirable_skills)


class DbApplicationRepository:
    """SQLAlchemy-backed store for board applications and their events."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_for_user(self, user_id: str) -> list[Application]:
        """Return all applications for a user."""
        with _timed_session(self._session_factory) as session:
            rows = (
                session.execute(
                    select(ApplicationRow).where(ApplicationRow.user_id == user_id)
                )
                .scalars()
                .all()
            )
            return [_application_from_row(row) for row in rows]

    def create(self, user_id: str, application: Application) -> Application:
        """Persist a new application, assigning an id."""
        if not application.id:
            application.id = str(uuid.uuid4())
        with _timed_session(self._session_factory) as session:
            row = ApplicationRow(
                id=application.id,
                user_id=user_id,
                opportunity_id=application.opportunity_id,
                stage=application.stage.value,
                notes=application.notes,
            )
            session.add(row)
        return application

    def get_by_id(self, user_id: str, application_id: str) -> Application | None:
        """Return a single application owned by the user."""
        with _timed_session(self._session_factory) as session:
            row = session.get(ApplicationRow, application_id)
            if row is None or row.user_id != user_id:
                return None
            return _application_from_row(row)

    def update(
        self,
        user_id: str,
        application_id: str,
        *,
        stage: ApplicationStage | None = None,
        notes: str | None = None,
    ) -> Application | None:
        """Update fields on an existing application."""
        with _timed_session(self._session_factory) as session:
            row = session.get(ApplicationRow, application_id)
            if row is None or row.user_id != user_id:
                return None
            if stage is not None:
                row.stage = stage.value
            if notes is not None:
                row.notes = notes
            return _application_from_row(row)

    def delete(self, user_id: str, application_id: str) -> bool:
        """Delete an application. Returns True if it existed."""
        with _timed_session(self._session_factory) as session:
            row = session.get(ApplicationRow, application_id)
            if row is None or row.user_id != user_id:
                return False
            session.delete(row)
            return True

    # --- Event methods ---

    def add_event(self, user_id: str, event: ApplicationEvent) -> ApplicationEvent:
        """Store an immutable event for an application."""
        with _timed_session(self._session_factory) as session:
            row = ApplicationEventRow(
                id=event.id,
                user_id=user_id,
                application_id=event.application_id,
                previous_stage=event.previous_stage.value,
                new_stage=event.new_stage.value,
                timestamp=event.timestamp,
                notes=event.notes,
            )
            session.add(row)
        return event

    def list_events(self, user_id: str, application_id: str) -> list[ApplicationEvent]:
        """Return events for an application in chronological order."""
        with _timed_session(self._session_factory) as session:
            rows = (
                session.execute(
                    select(ApplicationEventRow)
                    .where(
                        ApplicationEventRow.user_id == user_id,
                        ApplicationEventRow.application_id == application_id,
                    )
                    .order_by(ApplicationEventRow.timestamp)
                )
                .scalars()
                .all()
            )
            return [_event_from_row(row) for row in rows]


def _application_from_row(row: ApplicationRow) -> Application:
    return Application(
        id=row.id,
        opportunity_id=row.opportunity_id,
        stage=ApplicationStage(row.stage),
        notes=row.notes,
    )


def _event_from_row(row: ApplicationEventRow) -> ApplicationEvent:
    return ApplicationEvent(
        id=row.id,
        application_id=row.application_id,
        previous_stage=ApplicationStage(row.previous_stage),
        new_stage=ApplicationStage(row.new_stage),
        timestamp=row.timestamp,
        notes=row.notes,
    )


class DbFitEvaluationRepository:
    """SQLAlchemy-backed store for fit evaluations."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def find_existing(
        self,
        user_id: str,
        opportunity_id: str,
        profile_version: int,
        scoring_version: str,
    ) -> FitEvaluation | None:
        """Find an existing evaluation matching the exact version tuple."""
        with _timed_session(self._session_factory) as session:
            row = session.execute(
                select(FitEvaluationRow).where(
                    FitEvaluationRow.user_id == user_id,
                    FitEvaluationRow.opportunity_id == opportunity_id,
                    FitEvaluationRow.profile_version == profile_version,
                    FitEvaluationRow.scoring_version == scoring_version,
                )
            ).scalar_one_or_none()
            return _evaluation_from_row(row) if row else None

    def create(self, evaluation: FitEvaluation) -> FitEvaluation:
        """Persist a new evaluation."""
        with _timed_session(self._session_factory) as session:
            row = FitEvaluationRow(
                id=evaluation.id,
                user_id=evaluation.user_id,
                opportunity_id=evaluation.opportunity_id,
                profile_version=evaluation.profile_version,
                scoring_version=evaluation.scoring_version,
                score=evaluation.score,
                band=evaluation.band,
                matched_skills=list(evaluation.matched_skills),
                missing_skills=list(evaluation.missing_skills),
                rationale=list(evaluation.rationale),
                gap_actions=list(evaluation.gap_actions),
                created_at=evaluation.created_at,
            )
            session.add(row)
        return evaluation

    def list_for_user(self, user_id: str) -> list[FitEvaluation]:
        """Return all evaluations for a user, newest first."""
        with _timed_session(self._session_factory) as session:
            rows = (
                session.execute(
                    select(FitEvaluationRow)
                    .where(FitEvaluationRow.user_id == user_id)
                    .order_by(FitEvaluationRow.created_at.desc())
                )
                .scalars()
                .all()
            )
            return [_evaluation_from_row(row) for row in rows]

    def get_by_id(self, user_id: str, evaluation_id: str) -> FitEvaluation | None:
        """Return a single evaluation owned by the user."""
        with _timed_session(self._session_factory) as session:
            row = session.get(FitEvaluationRow, evaluation_id)
            if row is None or row.user_id != user_id:
                return None
            return _evaluation_from_row(row)


def _evaluation_from_row(row: FitEvaluationRow) -> FitEvaluation:
    return FitEvaluation(
        id=row.id,
        user_id=row.user_id,
        opportunity_id=row.opportunity_id,
        profile_version=row.profile_version,
        scoring_version=row.scoring_version,
        score=row.score,
        band=row.band,
        matched_skills=tuple(row.matched_skills or []),
        missing_skills=tuple(row.missing_skills or []),
        rationale=tuple(row.rationale or []),
        gap_actions=tuple(row.gap_actions or []),
        created_at=row.created_at,
    )
