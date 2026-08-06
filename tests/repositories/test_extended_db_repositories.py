"""Contract tests for the "extended" DB-backed repositories.

Mirrors ``tests/repositories/test_db_repositories.py``: runs the same
behavioral contract as the in-memory implementations against SQLAlchemy-backed
implementations, using an ephemeral in-memory SQLite database created from the
ORM metadata directly.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from boardmatch.audit import AuditAction
from boardmatch.documents import Document, DocumentStatus, compute_content_hash
from boardmatch.drafts import Draft, new_draft_id
from boardmatch.infrastructure.db.orm import Base
from boardmatch.infrastructure.repositories.extended_db import (
    DbAuditLogger,
    DbDocumentRepository,
    DbDraftRepository,
    DbExtractedTextRepository,
    DbIntegrationRepository,
    DbNetworkConnectionRepository,
    DbRetentionNetworkRepository,
)
from boardmatch.integrations import (
    AuditEvent as IntegrationAuditEvent,
)
from boardmatch.integrations import (
    AuditEventType,
    Integration,
    IntegrationStatus,
    hash_token,
)


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    yield factory
    engine.dispose()


@pytest.fixture
def draft_repo(session_factory) -> DbDraftRepository:
    return DbDraftRepository(session_factory)


@pytest.fixture
def document_repo(session_factory) -> DbDocumentRepository:
    return DbDocumentRepository(session_factory)


@pytest.fixture
def network_repo(session_factory) -> DbNetworkConnectionRepository:
    return DbNetworkConnectionRepository(session_factory)


@pytest.fixture
def integration_repo(session_factory) -> DbIntegrationRepository:
    return DbIntegrationRepository(session_factory)


@pytest.fixture
def audit_logger(session_factory) -> DbAuditLogger:
    return DbAuditLogger(session_factory, retention_days=90)


@pytest.fixture
def extracted_text_repo(session_factory) -> DbExtractedTextRepository:
    return DbExtractedTextRepository(session_factory)


@pytest.fixture
def retention_network_repo(session_factory) -> DbRetentionNetworkRepository:
    return DbRetentionNetworkRepository(session_factory)


def _make_draft(user_id: str = "user-1", opportunity_id: str | None = None) -> Draft:
    return Draft(
        id=new_draft_id(),
        user_id=user_id,
        draft_type="board_cv",
        content="Board CV content",
        engine="template",
        opportunity_id=opportunity_id,
    )


def _make_document(user_id: str = "user-1", content_hash: str = "hash-1") -> Document:
    return Document(
        user_id=user_id,
        filename="cv.pdf",
        content_type="application/pdf",
        size_bytes=1024,
        content_hash=content_hash,
        storage_path=f"{user_id}/{content_hash}/cv.pdf",
    )


class TestDbDraftRepository:
    def test_create_and_get(self, draft_repo) -> None:
        draft = _make_draft()
        draft_repo.create(draft)
        fetched = draft_repo.get_by_id(draft.id, "user-1")
        assert fetched is not None
        assert fetched.content == "Board CV content"
        assert fetched.engine == "template"

    def test_get_by_id_wrong_user_returns_none(self, draft_repo) -> None:
        draft = _make_draft()
        draft_repo.create(draft)
        assert draft_repo.get_by_id(draft.id, "other-user") is None

    def test_list_for_user_newest_first(self, draft_repo) -> None:
        first = _make_draft()
        draft_repo.create(first)
        second = Draft(
            id=new_draft_id(),
            user_id="user-1",
            draft_type="outreach",
            content="Second",
            engine="template",
            created_at=first.created_at + timedelta(seconds=1),
        )
        draft_repo.create(second)
        drafts = draft_repo.list_for_user("user-1")
        assert len(drafts) == 2
        assert drafts[0].id == second.id

    def test_delete(self, draft_repo) -> None:
        draft = _make_draft()
        draft_repo.create(draft)
        assert draft_repo.delete(draft.id, "user-1") is True
        assert draft_repo.get_by_id(draft.id, "user-1") is None
        assert draft_repo.delete(draft.id, "user-1") is False

    def test_delete_wrong_user_fails(self, draft_repo) -> None:
        draft = _make_draft()
        draft_repo.create(draft)
        assert draft_repo.delete(draft.id, "other-user") is False


class TestDbDocumentRepository:
    def test_save_and_get(self, document_repo) -> None:
        doc = _make_document()
        document_repo.save(doc)
        fetched = document_repo.get_by_id(doc.id)
        assert fetched is not None
        assert fetched.filename == "cv.pdf"
        assert fetched.status == DocumentStatus.PENDING

    def test_list_by_user(self, document_repo) -> None:
        document_repo.save(_make_document(user_id="user-1", content_hash="h1"))
        document_repo.save(_make_document(user_id="user-1", content_hash="h2"))
        document_repo.save(_make_document(user_id="user-2", content_hash="h3"))
        assert len(document_repo.list_by_user("user-1")) == 2

    def test_delete(self, document_repo) -> None:
        doc = _make_document()
        document_repo.save(doc)
        assert document_repo.delete(doc.id) is True
        assert document_repo.get_by_id(doc.id) is None
        assert document_repo.delete(doc.id) is False

    def test_find_by_hash(self, document_repo) -> None:
        content = b"hello world"
        content_hash = compute_content_hash(content)
        doc = _make_document(content_hash=content_hash)
        document_repo.save(doc)
        found = document_repo.find_by_hash("user-1", content_hash)
        assert found is not None
        assert found.id == doc.id
        assert document_repo.find_by_hash("user-1", "nonexistent") is None


class TestDbNetworkConnectionRepository:
    def test_save_and_list(self, network_repo) -> None:
        from boardmatch.api.v1.network import NetworkConnection

        conn = NetworkConnection(
            id=str(uuid.uuid4()),
            user_id="user-1",
            name="Sarah Chen",
            relationship="Former colleague",
            organisations=["TechCorp"],
            board_seats=["ASX FinTech Ltd"],
        )
        network_repo.save(conn)
        connections = network_repo.list_by_user("user-1")
        assert len(connections) == 1
        assert connections[0].name == "Sarah Chen"
        assert connections[0].organisations == ["TechCorp"]

    def test_get(self, network_repo) -> None:
        from boardmatch.api.v1.network import NetworkConnection

        conn = NetworkConnection(
            id=str(uuid.uuid4()), user_id="user-1", name="X", relationship="peer"
        )
        network_repo.save(conn)
        fetched = network_repo.get(conn.id)
        assert fetched is not None
        assert fetched.id == conn.id
        assert network_repo.get("nonexistent") is None

    def test_delete_is_soft_delete(self, network_repo) -> None:
        from boardmatch.api.v1.network import NetworkConnection

        conn = NetworkConnection(
            id=str(uuid.uuid4()), user_id="user-1", name="X", relationship="peer"
        )
        network_repo.save(conn)
        assert network_repo.delete(conn.id) is True
        assert network_repo.get(conn.id) is None
        assert network_repo.list_by_user("user-1") == []
        # Deleting again returns False (already deleted).
        assert network_repo.delete(conn.id) is False

    def test_update_via_save(self, network_repo) -> None:
        from boardmatch.api.v1.network import NetworkConnection

        conn = NetworkConnection(
            id=str(uuid.uuid4()), user_id="user-1", name="X", relationship="peer"
        )
        network_repo.save(conn)
        conn.approved = True
        conn.strength = 9
        network_repo.save(conn)
        fetched = network_repo.get(conn.id)
        assert fetched.approved is True
        assert fetched.strength == 9


class TestDbIntegrationRepository:
    def test_save_never_persists_access_token(self, integration_repo) -> None:
        """Security invariant: real access tokens must never reach the DB."""
        integration = Integration(
            user_id="user-1",
            provider="microsoft",
            status=IntegrationStatus.ACTIVE,
            scopes=["User.Read"],
            token_hash=hash_token("super-secret-real-token"),
            access_token=SecretStr("super-secret-real-token"),
        )
        integration_repo.save(integration)

        fetched = integration_repo.get("user-1", "microsoft")
        assert fetched is not None
        assert fetched.access_token is None
        assert fetched.token_hash == integration.token_hash

    def test_list_by_user(self, integration_repo) -> None:
        integration_repo.save(
            Integration(user_id="user-1", provider="microsoft", scopes=["User.Read"])
        )
        integration_repo.save(
            Integration(user_id="user-1", provider="google", scopes=["profile"])
        )
        integrations = integration_repo.list_by_user("user-1")
        assert len(integrations) == 2

    def test_save_overwrites_existing(self, integration_repo) -> None:
        integration_repo.save(
            Integration(
                user_id="user-1", provider="microsoft", status=IntegrationStatus.ACTIVE
            )
        )
        integration_repo.save(
            Integration(
                user_id="user-1", provider="microsoft", status=IntegrationStatus.REVOKED
            )
        )
        fetched = integration_repo.get("user-1", "microsoft")
        assert fetched.status == IntegrationStatus.REVOKED

    def test_audit_events_round_trip(self, integration_repo) -> None:
        event = IntegrationAuditEvent(
            user_id="user-1",
            provider="microsoft",
            event_type=AuditEventType.CONSENT_GRANTED,
            scopes=["User.Read"],
        )
        integration_repo.add_audit_event(event)
        events = integration_repo.get_audit_events("user-1")
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.CONSENT_GRANTED
        assert events[0].scopes == ["User.Read"]


class TestDbAuditLogger:
    def test_log_and_get_events(self, audit_logger) -> None:
        import time

        audit_logger.log("user-1", AuditAction.LOGIN)
        time.sleep(0.01)
        audit_logger.log("user-1", AuditAction.PROFILE_UPDATED, "profile")
        events = audit_logger.get_events("user-1")
        assert len(events) == 2
        # Most recent first.
        assert events[0].action == AuditAction.PROFILE_UPDATED

    def test_get_events_scoped_to_user(self, audit_logger) -> None:
        audit_logger.log("user-1", AuditAction.LOGIN)
        audit_logger.log("user-2", AuditAction.LOGIN)
        assert len(audit_logger.get_events("user-1")) == 1

    def test_purge_expired(self, session_factory) -> None:
        logger = DbAuditLogger(session_factory, retention_days=30)
        logger.log("user-1", AuditAction.LOGIN)
        # Directly age the row past the retention window.
        with session_factory() as session:
            from boardmatch.infrastructure.db.orm import AuditEventRow

            row = session.query(AuditEventRow).one()
            row.timestamp = datetime.now(timezone.utc) - timedelta(days=31)
            session.commit()

        purged = logger.purge_expired()
        assert purged == 1
        assert logger.get_events("user-1") == []

    def test_clear(self, audit_logger) -> None:
        audit_logger.log("user-1", AuditAction.LOGIN)
        audit_logger.clear()
        assert audit_logger.get_events("user-1") == []


class TestDbExtractedTextRepository:
    def test_save_and_get(self, extracted_text_repo) -> None:
        extracted_text_repo.save("doc-1", "Extracted CV text", "user-1")
        assert extracted_text_repo.get("doc-1") == "Extracted CV text"

    def test_delete(self, extracted_text_repo) -> None:
        extracted_text_repo.save("doc-1", "text", "user-1")
        assert extracted_text_repo.delete("doc-1") is True
        assert extracted_text_repo.get("doc-1") is None
        assert extracted_text_repo.delete("doc-1") is False

    def test_list_by_user(self, extracted_text_repo) -> None:
        extracted_text_repo.save("doc-1", "text", "user-1")
        extracted_text_repo.save("doc-2", "text", "user-1")
        extracted_text_repo.save("doc-3", "text", "user-2")
        assert sorted(extracted_text_repo.list_by_user("user-1")) == ["doc-1", "doc-2"]

    def test_get_creation_time(self, extracted_text_repo) -> None:
        extracted_text_repo.save("doc-1", "text", "user-1")
        created = extracted_text_repo.get_creation_time("doc-1")
        assert created is not None
        assert extracted_text_repo.get_creation_time("nonexistent") is None


class TestDbRetentionNetworkRepository:
    def test_save_and_list(self, retention_network_repo) -> None:
        retention_network_repo.save("conn-1", "user-1", {"name": "Sarah"})
        records = retention_network_repo.list_by_user("user-1")
        assert len(records) == 1
        assert records[0]["name"] == "Sarah"

    def test_delete_all_for_user(self, retention_network_repo) -> None:
        retention_network_repo.save("conn-1", "user-1", {"name": "Sarah"})
        retention_network_repo.save("conn-2", "user-1", {"name": "James"})
        retention_network_repo.save("conn-3", "user-2", {"name": "Priya"})

        deleted = retention_network_repo.delete_all_for_user("user-1")
        assert deleted == 2
        assert retention_network_repo.list_by_user("user-1") == []
        assert len(retention_network_repo.list_by_user("user-2")) == 1
