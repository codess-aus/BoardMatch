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
    Boolean,
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

    events: Mapped[list[ApplicationEventRow]] = relationship(
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


# ---------------------------------------------------------------------------
# Extended stores: drafts, documents, network connections, integrations,
# account-level audit events, and retention state. These provide durable
# parity for the modules previously implemented only in-memory in
# ``boardmatch.drafts``, ``boardmatch.documents``, ``boardmatch.api.v1.network``,
# ``boardmatch.integrations``, ``boardmatch.audit``, and ``boardmatch.retention``.
# ---------------------------------------------------------------------------


class DraftRow(Base):
    """A persisted coaching draft, matching boardmatch.drafts.Draft."""

    __tablename__ = "drafts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    draft_type: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    engine: Mapped[str] = mapped_column(String, nullable=False)
    model_name: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_version: Mapped[str] = mapped_column(String, nullable=False, default="1.0")
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    opportunity_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class DocumentRow(Base):
    """A document metadata record, matching boardmatch.documents.Document."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class NetworkConnectionRow(Base):
    """A user's network connection, matching api.v1.network.NetworkConnection."""

    __tablename__ = "network_connections"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    relationship_: Mapped[str] = mapped_column("relationship", String, nullable=False)
    organisations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    board_seats: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    strength: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    source: Mapped[str] = mapped_column(String, nullable=False, default="manual")
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class IntegrationRow(Base):
    """A user's integration with an external provider.

    Deliberately has NO column for the live OAuth access token — only the
    one-way ``token_hash`` is persisted, matching the security property in
    ``boardmatch.integrations`` where real access tokens are held only in
    process memory and never written to durable storage.
    """

    __tablename__ = "integrations"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    token_hash: Mapped[str | None] = mapped_column(String, nullable=True)


class IntegrationAuditEventRow(Base):
    """An audit log entry for integration consent state changes."""

    __tablename__ = "integration_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class AuditEventRow(Base):
    """An account-level audit log entry, matching boardmatch.audit.AuditEvent."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ExtractedTextRow(Base):
    """Extracted CV text with a creation timestamp, for retention tracking."""

    __tablename__ = "extracted_texts"

    document_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class RetentionNetworkRecordRow(Base):
    """Generic network-data record tracked by the retention module for bulk
    deletion (separate from ``network_connections``, which is the primary
    store used by ``boardmatch.api.v1.network`` — see README for the
    pre-existing duplication this preserves)."""

    __tablename__ = "retention_network_records"

    connection_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
