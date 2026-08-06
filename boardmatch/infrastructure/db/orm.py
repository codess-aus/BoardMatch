"""SQLAlchemy ORM models for DB-backed repositories.

These tables provide durable, Postgres-compatible parity for the four
repositories implemented in-memory in
``boardmatch.infrastructure.repositories.memory``: candidates, board
opportunities, applications (with their events), and fit evaluations.

The table names are prefixed/distinct from the legacy raw-SQL schema in
``migrations/0001_opportunity_source_schema.sql`` (which models the
ingestion/source-tracking pipeline) to avoid any naming collision — see
``alembic/versions`` for how both schemas coexist.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CandidateRow(Base):
    """A user's candidate profile (1:1 with user_id)."""

    __tablename__ = "candidates"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    headline: Mapped[str] = mapped_column(String, nullable=False, default="")
    years_experience: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skills: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    sectors: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    credentials: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    board_experience: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    achievements: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    locations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    connections: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class BoardOpportunityRow(Base):
    """A board / NED vacancy, matching boardmatch.models.Opportunity."""

    __tablename__ = "board_opportunities"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    organisation: Mapped[str] = mapped_column(String, nullable=False)
    sector: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    remuneration: Mapped[str] = mapped_column(String, nullable=False)
    fee_aud: Mapped[int | None] = mapped_column(Integer, nullable=True)
    closes_on: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    required_skills: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    desirable_skills: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class ApplicationRow(Base):
    """A board application in a user's pipeline."""

    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    opportunity_id: Mapped[str] = mapped_column(String, nullable=False)
    stage: Mapped[str] = mapped_column(String, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    events: Mapped[list["ApplicationEventRow"]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )


class ApplicationEventRow(Base):
    """An immutable stage-transition event for an application."""

    __tablename__ = "application_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    application_id: Mapped[str] = mapped_column(
        String, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    previous_stage: Mapped[str] = mapped_column(String, nullable=False)
    new_stage: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    application: Mapped[ApplicationRow] = relationship(back_populates="events")


class FitEvaluationRow(Base):
    """A persisted fit evaluation with versioning for audit."""

    __tablename__ = "fit_evaluations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    opportunity_id: Mapped[str] = mapped_column(String, nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scoring_version: Mapped[str] = mapped_column(String, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    band: Mapped[str] = mapped_column(String, nullable=False)
    matched_skills: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    missing_skills: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    rationale: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    gap_actions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
