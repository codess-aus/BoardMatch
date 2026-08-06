"""Database (SQLAlchemy) repository implementations for the "extended" stores.

These mirror the pattern established in
``boardmatch.infrastructure.repositories.db`` for the core repositories, but
cover the modules that previously had only module-local in-memory storage:
drafts, documents, network connections, integrations (+ audit events),
account-level audit events, and retention state (extracted text + network
deletion tracking).

Every class implements the exact same method signatures as its in-memory
counterpart so it can be swapped in via
``boardmatch.infrastructure.repositories.extended_factory.create_extended_repositories``
without any change to calling code.

Security note: ``IntegrationRow`` intentionally has no column for the live
OAuth access token. ``DbIntegrationRepository`` never persists
``Integration.access_token`` — only the one-way ``token_hash`` — preserving
the invariant enforced by the in-memory implementation in
``boardmatch.integrations``.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from boardmatch.audit import AuditAction
from boardmatch.audit import AuditEvent as AccountAuditEvent
from boardmatch.documents import Document, DocumentStatus
from boardmatch.drafts import Draft
from boardmatch.infrastructure.db.orm import (
    AuditEventRow,
    DocumentRow,
    DraftRow,
    ExtractedTextRow,
    IntegrationAuditEventRow,
    IntegrationRow,
    NetworkConnectionRow,
    RetentionNetworkRecordRow,
)
from boardmatch.integrations import (
    AuditEvent as IntegrationAuditEvent,
)
from boardmatch.integrations import (
    AuditEventType,
    Integration,
    IntegrationStatus,
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


# --- Drafts -----------------------------------------------------------------


class DbDraftRepository:
    """SQLAlchemy-backed store for coaching drafts."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(self, draft: Draft) -> Draft:
        """Persist a new draft and return it."""
        with _timed_session(self._session_factory) as session:
            row = DraftRow(
                id=draft.id,
                user_id=draft.user_id,
                draft_type=draft.draft_type,
                content=draft.content,
                engine=draft.engine,
                model_name=draft.model_name,
                prompt_version=draft.prompt_version,
                profile_version=draft.profile_version,
                opportunity_id=draft.opportunity_id,
                created_at=draft.created_at,
            )
            session.add(row)
        return draft

    def get_by_id(self, draft_id: str, user_id: str) -> Draft | None:
        """Return a draft by ID, scoped to the owning user."""
        with _timed_session(self._session_factory) as session:
            row = session.get(DraftRow, draft_id)
            if row is None or row.user_id != user_id:
                return None
            return _draft_from_row(row)

    def list_for_user(self, user_id: str) -> list[Draft]:
        """Return all drafts belonging to a user, newest first."""
        with _timed_session(self._session_factory) as session:
            rows = (
                session.execute(
                    select(DraftRow)
                    .where(DraftRow.user_id == user_id)
                    .order_by(DraftRow.created_at.desc())
                )
                .scalars()
                .all()
            )
            return [_draft_from_row(row) for row in rows]

    def delete(self, draft_id: str, user_id: str) -> bool:
        """Delete a draft. Returns True if deleted, False if not found."""
        with _timed_session(self._session_factory) as session:
            row = session.get(DraftRow, draft_id)
            if row is None or row.user_id != user_id:
                return False
            session.delete(row)
            return True


def _draft_from_row(row: DraftRow) -> Draft:
    return Draft(
        id=row.id,
        user_id=row.user_id,
        draft_type=row.draft_type,
        content=row.content,
        engine=row.engine,
        model_name=row.model_name,
        prompt_version=row.prompt_version,
        profile_version=row.profile_version,
        opportunity_id=row.opportunity_id,
        created_at=row.created_at,
    )


# --- Documents ----------------------------------------------------------


class DbDocumentRepository:
    """SQLAlchemy-backed store for document metadata."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save(self, document: Document) -> Document:
        with _timed_session(self._session_factory) as session:
            row = session.get(DocumentRow, document.id)
            if row is None:
                row = DocumentRow(id=document.id)
                session.add(row)
            _apply_document_to_row(row, document)
        return document

    def get_by_id(self, document_id: str) -> Document | None:
        with _timed_session(self._session_factory) as session:
            row = session.get(DocumentRow, document_id)
            return _document_from_row(row) if row else None

    def list_by_user(self, user_id: str) -> list[Document]:
        with _timed_session(self._session_factory) as session:
            rows = (
                session.execute(
                    select(DocumentRow).where(DocumentRow.user_id == user_id)
                )
                .scalars()
                .all()
            )
            return [_document_from_row(row) for row in rows]

    def delete(self, document_id: str) -> bool:
        with _timed_session(self._session_factory) as session:
            row = session.get(DocumentRow, document_id)
            if row is None:
                return False
            session.delete(row)
            return True

    def find_by_hash(self, user_id: str, content_hash: str) -> Document | None:
        with _timed_session(self._session_factory) as session:
            row = (
                session.execute(
                    select(DocumentRow).where(
                        DocumentRow.user_id == user_id,
                        DocumentRow.content_hash == content_hash,
                    )
                )
                .scalars()
                .first()
            )
            return _document_from_row(row) if row else None


def _document_from_row(row: DocumentRow) -> Document:
    return Document(
        id=row.id,
        user_id=row.user_id,
        filename=row.filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        content_hash=row.content_hash,
        storage_path=row.storage_path,
        status=DocumentStatus(row.status),
        uploaded_at=row.uploaded_at,
    )


def _apply_document_to_row(row: DocumentRow, document: Document) -> None:
    row.user_id = document.user_id
    row.filename = document.filename
    row.content_type = document.content_type
    row.size_bytes = document.size_bytes
    row.content_hash = document.content_hash
    row.storage_path = document.storage_path
    row.status = document.status.value
    row.uploaded_at = document.uploaded_at


# --- Network connections -----------------------------------------------


class DbNetworkConnectionRepository:
    """SQLAlchemy-backed store for network connections.

    Mirrors ``boardmatch.api.v1.network.InMemoryNetworkRepository``. The
    ``NetworkConnection`` pydantic model is imported lazily inside each
    method to avoid a circular import between this module and
    ``boardmatch.api.v1.network``.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_by_user(self, user_id: str) -> list:
        from boardmatch.api.v1.network import NetworkConnection

        with _timed_session(self._session_factory) as session:
            rows = (
                session.execute(
                    select(NetworkConnectionRow).where(
                        NetworkConnectionRow.user_id == user_id,
                        NetworkConnectionRow.deleted.is_(False),
                    )
                )
                .scalars()
                .all()
            )
            return [_connection_from_row(row, NetworkConnection) for row in rows]

    def get(self, connection_id: str):
        from boardmatch.api.v1.network import NetworkConnection

        with _timed_session(self._session_factory) as session:
            row = session.get(NetworkConnectionRow, connection_id)
            if row is None or row.deleted:
                return None
            return _connection_from_row(row, NetworkConnection)

    def save(self, connection) -> None:
        with _timed_session(self._session_factory) as session:
            row = session.get(NetworkConnectionRow, connection.id)
            if row is None:
                row = NetworkConnectionRow(id=connection.id)
                session.add(row)
            row.user_id = connection.user_id
            row.name = connection.name
            row.relationship_ = connection.relationship
            row.organisations = list(connection.organisations)
            row.board_seats = list(connection.board_seats)
            row.approved = connection.approved
            row.strength = connection.strength
            row.source = connection.source
            row.deleted = connection.deleted

    def delete(self, connection_id: str) -> bool:
        with _timed_session(self._session_factory) as session:
            row = session.get(NetworkConnectionRow, connection_id)
            if row is None or row.deleted:
                return False
            row.deleted = True
            return True


def _connection_from_row(row: NetworkConnectionRow, connection_cls):
    return connection_cls(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        relationship=row.relationship_,
        organisations=list(row.organisations or []),
        board_seats=list(row.board_seats or []),
        approved=row.approved,
        strength=row.strength,
        source=row.source,
        deleted=row.deleted,
    )


# --- Integrations ---------------------------------------------------------


class DbIntegrationRepository:
    """SQLAlchemy-backed store for integrations and their audit events.

    Never persists ``Integration.access_token``: only ``token_hash`` and
    metadata are written to the database. Rows loaded from the database
    always come back with ``access_token=None``.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_by_user(self, user_id: str) -> list[Integration]:
        with _timed_session(self._session_factory) as session:
            rows = (
                session.execute(
                    select(IntegrationRow).where(IntegrationRow.user_id == user_id)
                )
                .scalars()
                .all()
            )
            return [_integration_from_row(row) for row in rows]

    def get(self, user_id: str, provider: str) -> Integration | None:
        with _timed_session(self._session_factory) as session:
            row = session.get(IntegrationRow, (user_id, provider))
            return _integration_from_row(row) if row else None

    def save(self, integration: Integration) -> None:
        with _timed_session(self._session_factory) as session:
            row = session.get(
                IntegrationRow, (integration.user_id, integration.provider)
            )
            if row is None:
                row = IntegrationRow(
                    user_id=integration.user_id, provider=integration.provider
                )
                session.add(row)
            row.status = integration.status.value
            row.scopes = list(integration.scopes)
            row.granted_at = integration.granted_at
            row.revoked_at = integration.revoked_at
            # Deliberately NOT persisting integration.access_token.
            row.token_hash = integration.token_hash

    def get_audit_events(self, user_id: str) -> list[IntegrationAuditEvent]:
        with _timed_session(self._session_factory) as session:
            rows = (
                session.execute(
                    select(IntegrationAuditEventRow)
                    .where(IntegrationAuditEventRow.user_id == user_id)
                    .order_by(IntegrationAuditEventRow.timestamp)
                )
                .scalars()
                .all()
            )
            return [_integration_audit_event_from_row(row) for row in rows]

    def add_audit_event(self, event: IntegrationAuditEvent) -> None:
        with _timed_session(self._session_factory) as session:
            row = IntegrationAuditEventRow(
                user_id=event.user_id,
                provider=event.provider,
                event_type=event.event_type.value,
                scopes=list(event.scopes),
                timestamp=event.timestamp,
            )
            session.add(row)


def _integration_from_row(row: IntegrationRow) -> Integration:
    return Integration(
        user_id=row.user_id,
        provider=row.provider,
        status=IntegrationStatus(row.status),
        scopes=list(row.scopes or []),
        granted_at=row.granted_at,
        revoked_at=row.revoked_at,
        token_hash=row.token_hash,
        access_token=None,
    )


def _integration_audit_event_from_row(
    row: IntegrationAuditEventRow,
) -> IntegrationAuditEvent:
    return IntegrationAuditEvent(
        user_id=row.user_id,
        provider=row.provider,
        event_type=AuditEventType(row.event_type),
        scopes=list(row.scopes or []),
        timestamp=row.timestamp,
    )


# --- Account-level audit log ------------------------------------------------


class DbAuditLogger:
    """SQLAlchemy-backed audit logger, matching boardmatch.audit.AuditLogger."""

    def __init__(
        self, session_factory: sessionmaker[Session], retention_days: int = 90
    ) -> None:
        self._session_factory = session_factory
        self._retention_days = retention_days

    def log(
        self,
        user_id: str,
        action: AuditAction,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict | None = None,
    ) -> AccountAuditEvent:
        """Record an audit event and return it."""
        event = AccountAuditEvent(
            id=str(uuid.uuid4()),
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
        )
        with _timed_session(self._session_factory) as session:
            row = AuditEventRow(
                id=event.id,
                user_id=event.user_id,
                action=event.action.value,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                timestamp=event.timestamp,
                details=event.details,
            )
            session.add(row)
        return event

    def get_events(self, user_id: str) -> list[AccountAuditEvent]:
        """Return audit events for a user, most recent first, respecting retention."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        with _timed_session(self._session_factory) as session:
            rows = (
                session.execute(
                    select(AuditEventRow)
                    .where(
                        AuditEventRow.user_id == user_id,
                        AuditEventRow.timestamp >= cutoff,
                    )
                    .order_by(AuditEventRow.timestamp.desc())
                )
                .scalars()
                .all()
            )
            return [_audit_event_from_row(row) for row in rows]

    def purge_expired(self) -> int:
        """Remove events older than retention period. Returns count purged."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        with _timed_session(self._session_factory) as session:
            result = session.execute(
                delete(AuditEventRow).where(AuditEventRow.timestamp < cutoff)
            )
            return result.rowcount or 0

    def clear(self) -> None:
        """Remove all events (for testing)."""
        with _timed_session(self._session_factory) as session:
            session.execute(delete(AuditEventRow))


def _audit_event_from_row(row: AuditEventRow) -> AccountAuditEvent:
    return AccountAuditEvent(
        id=row.id,
        user_id=row.user_id,
        action=AuditAction(row.action),
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        timestamp=row.timestamp,
        details=row.details,
    )


# --- Retention: extracted text ----------------------------------------------


class DbExtractedTextRepository:
    """SQLAlchemy-backed store for extracted CV text with retention support."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save(self, document_id: str, text: str, user_id: str) -> None:
        with _timed_session(self._session_factory) as session:
            row = session.get(ExtractedTextRow, document_id)
            if row is None:
                row = ExtractedTextRow(document_id=document_id)
                session.add(row)
            row.user_id = user_id
            row.text = text
            row.created_at = datetime.now(timezone.utc)

    def get(self, document_id: str) -> str | None:
        with _timed_session(self._session_factory) as session:
            row = session.get(ExtractedTextRow, document_id)
            return row.text if row else None

    def delete(self, document_id: str) -> bool:
        with _timed_session(self._session_factory) as session:
            row = session.get(ExtractedTextRow, document_id)
            if row is None:
                return False
            session.delete(row)
            return True

    def list_by_user(self, user_id: str) -> list[str]:
        with _timed_session(self._session_factory) as session:
            rows = (
                session.execute(
                    select(ExtractedTextRow).where(ExtractedTextRow.user_id == user_id)
                )
                .scalars()
                .all()
            )
            return [row.document_id for row in rows]

    def get_creation_time(self, document_id: str) -> datetime | None:
        with _timed_session(self._session_factory) as session:
            row = session.get(ExtractedTextRow, document_id)
            return row.created_at if row else None


# --- Retention: network data deletion tracking ------------------------------


class DbRetentionNetworkRepository:
    """SQLAlchemy-backed store for retention's bulk-deletable network data.

    Mirrors ``boardmatch.retention.InMemoryNetworkRepository``. This is a
    separate table from ``network_connections`` (used by
    ``boardmatch.api.v1.network``) — see README for the pre-existing
    duplication this preserves.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save(self, connection_id: str, user_id: str, data: dict) -> None:
        with _timed_session(self._session_factory) as session:
            row = session.get(RetentionNetworkRecordRow, connection_id)
            if row is None:
                row = RetentionNetworkRecordRow(connection_id=connection_id)
                session.add(row)
            row.user_id = user_id
            row.data = dict(data)

    def list_by_user(self, user_id: str) -> list[dict]:
        with _timed_session(self._session_factory) as session:
            rows = (
                session.execute(
                    select(RetentionNetworkRecordRow).where(
                        RetentionNetworkRecordRow.user_id == user_id
                    )
                )
                .scalars()
                .all()
            )
            return [{"user_id": row.user_id, **row.data} for row in rows]

    def delete_all_for_user(self, user_id: str) -> int:
        """Delete all network data for a user. Returns count of deleted records."""
        with _timed_session(self._session_factory) as session:
            result = session.execute(
                delete(RetentionNetworkRecordRow).where(
                    RetentionNetworkRecordRow.user_id == user_id
                )
            )
            return result.rowcount or 0
