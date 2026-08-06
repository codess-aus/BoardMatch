"""SQLAlchemy engine and session management.

Centralises connection pooling, statement-timeout configuration, and
Session creation so DB-backed repositories share a single configured
engine per process.
"""

from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

# Default pool sizing — conservative values suitable for a small API
# service; override via environment-specific settings if needed.
DEFAULT_POOL_SIZE = 5
DEFAULT_MAX_OVERFLOW = 10
DEFAULT_POOL_TIMEOUT_SECONDS = 30
DEFAULT_STATEMENT_TIMEOUT_MS = 5_000

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_engine_url: str | None = None


def is_sqlite(database_url: str) -> bool:
    """Return True when the URL targets SQLite."""
    return urlparse(database_url).scheme.startswith("sqlite")


def build_engine(
    database_url: str,
    *,
    pool_size: int = DEFAULT_POOL_SIZE,
    max_overflow: int = DEFAULT_MAX_OVERFLOW,
    pool_timeout: int = DEFAULT_POOL_TIMEOUT_SECONDS,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
) -> Engine:
    """Create a new SQLAlchemy engine with pooling and a statement timeout.

    SQLite engines (used for local/dev/test fallbacks) do not support the
    same pooling or timeout knobs as Postgres, so those options are only
    applied for non-SQLite URLs.
    """
    if is_sqlite(database_url):
        engine = create_engine(database_url, future=True)
        return engine

    engine = create_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_pre_ping=True,
        future=True,
    )

    if statement_timeout_ms:

        @event.listens_for(engine, "connect")
        def _set_statement_timeout(dbapi_connection, connection_record) -> None:  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(f"SET statement_timeout = {statement_timeout_ms}")
            finally:
                cursor.close()

    return engine


def get_engine(database_url: str) -> Engine:
    """Return a process-wide cached engine for the given URL.

    Rebuilds the engine if the URL changes (e.g. between test runs that
    reconfigure Settings).
    """
    global _engine, _engine_url
    if _engine is None or _engine_url != database_url:
        if _engine is not None:
            _engine.dispose()
        _engine = build_engine(database_url)
        _engine_url = database_url
    return _engine


def get_session_factory(database_url: str) -> sessionmaker[Session]:
    """Return a process-wide cached session factory for the given URL."""
    global _session_factory
    engine = get_engine(database_url)
    if _session_factory is None or _session_factory.kw.get("bind") is not engine:
        _session_factory = sessionmaker(
            bind=engine, expire_on_commit=False, future=True
        )
    return _session_factory


def reset_engine_cache() -> None:
    """Dispose and clear cached engine/session factory (primarily for tests)."""
    global _engine, _session_factory, _engine_url
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
    _engine_url = None
