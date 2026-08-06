"""Tests for the repository factory that selects memory vs DB backends."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from boardmatch.config import Settings
from boardmatch.infrastructure.db.orm import Base
from boardmatch.infrastructure.repositories import factory as factory_module
from boardmatch.infrastructure.repositories.db import DbCandidateRepository
from boardmatch.infrastructure.repositories.factory import (
    create_repositories,
    uses_database_backend,
)
from boardmatch.infrastructure.repositories.memory import InMemoryCandidateRepository


def test_sqlite_url_uses_memory_backend() -> None:
    assert uses_database_backend("sqlite:///./boardmatch.db") is False


def test_postgres_url_uses_database_backend() -> None:
    assert uses_database_backend("postgresql://user:pass@localhost/db") is True
    assert uses_database_backend("postgresql+psycopg://user:pass@localhost/db") is True


def test_create_repositories_returns_memory_for_sqlite() -> None:
    settings = Settings(database_url="sqlite:///:memory:")
    repos = create_repositories(settings)
    assert isinstance(repos.candidate_repo, InMemoryCandidateRepository)


def test_create_repositories_returns_db_for_postgres_scheme(monkeypatch) -> None:
    """Verify the DB branch is wired up without requiring a real Postgres driver.

    The ``psycopg`` binary wheel is unavailable on this sandbox's platform
    (see PR description), so this test substitutes an in-memory SQLite
    session factory to prove ``create_repositories`` dispatches to the
    DB-backed classes whenever the URL scheme is non-SQLite.
    """
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    fake_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(
        factory_module, "get_session_factory", lambda database_url: fake_factory
    )

    settings = Settings(database_url="postgresql+psycopg://user:pass@localhost/db")
    repos = create_repositories(settings)
    assert isinstance(repos.candidate_repo, DbCandidateRepository)
