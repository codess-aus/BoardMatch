"""Tests for the extended repository factory (memory vs DB backend)."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from boardmatch.audit import AuditLogger
from boardmatch.config import Settings
from boardmatch.documents import InMemoryDocumentRepository
from boardmatch.drafts import InMemoryDraftRepository
from boardmatch.infrastructure.db.orm import Base
from boardmatch.infrastructure.repositories import (
    extended_factory as extended_factory_module,
)
from boardmatch.infrastructure.repositories.extended_db import (
    DbAuditLogger,
    DbDocumentRepository,
    DbDraftRepository,
    DbNetworkConnectionRepository,
)
from boardmatch.infrastructure.repositories.extended_factory import (
    create_extended_repositories,
    create_network_connection_repo,
)
from boardmatch.integrations import InMemoryIntegrationRepository


def test_create_extended_repositories_returns_memory_for_sqlite() -> None:
    settings = Settings(database_url="sqlite:///:memory:")
    repos = create_extended_repositories(settings)
    assert isinstance(repos.draft_repo, InMemoryDraftRepository)
    assert isinstance(repos.document_repo, InMemoryDocumentRepository)
    assert isinstance(repos.integration_repo, InMemoryIntegrationRepository)
    assert isinstance(repos.audit_logger, AuditLogger)


def test_create_network_connection_repo_returns_memory_for_sqlite() -> None:
    from boardmatch.api.v1.network import InMemoryNetworkRepository

    settings = Settings(database_url="sqlite:///:memory:")
    repo = create_network_connection_repo(settings)
    assert isinstance(repo, InMemoryNetworkRepository)


def test_create_extended_repositories_returns_db_for_postgres_scheme(
    monkeypatch,
) -> None:
    """Verify the DB branch is wired up without requiring a real Postgres driver."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    fake_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(
        extended_factory_module,
        "get_session_factory",
        lambda database_url: fake_factory,
    )

    settings = Settings(database_url="postgresql+psycopg://user:pass@localhost/db")
    repos = create_extended_repositories(settings)
    assert isinstance(repos.draft_repo, DbDraftRepository)
    assert isinstance(repos.document_repo, DbDocumentRepository)
    assert isinstance(repos.audit_logger, DbAuditLogger)


def test_create_network_connection_repo_returns_db_for_postgres_scheme(
    monkeypatch,
) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    fake_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(
        extended_factory_module,
        "get_session_factory",
        lambda database_url: fake_factory,
    )

    settings = Settings(database_url="postgresql+psycopg://user:pass@localhost/db")
    repo = create_network_connection_repo(settings)
    assert isinstance(repo, DbNetworkConnectionRepository)
