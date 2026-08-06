"""Repository factory for the "extended" stores — selects memory vs DB-backed.

Mirrors ``boardmatch.infrastructure.repositories.factory`` but covers the
modules that previously had only module-local in-memory storage: drafts,
documents, network connections, integrations, account-level audit events,
and retention state (extracted text + network deletion tracking).

Local and test environments (SQLite ``DATABASE_URL``) continue to use the
in-memory repositories to avoid changing existing test behavior. Any other
scheme is treated as a durable database and wired to the SQLAlchemy-backed
repositories.

Note: network-connection repository selection lives in
``boardmatch.api.v1.network._create_network_repo`` rather than in
``create_extended_repositories`` below, to avoid a circular import — the
in-memory network connection store (``NetworkConnection``,
``InMemoryNetworkRepository``) is defined in that router module itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from boardmatch.audit import AuditLogger
from boardmatch.config import Settings
from boardmatch.documents import InMemoryDocumentRepository
from boardmatch.drafts import InMemoryDraftRepository
from boardmatch.infrastructure.db.engine import get_session_factory
from boardmatch.infrastructure.repositories.extended_db import (
    DbAuditLogger,
    DbDocumentRepository,
    DbDraftRepository,
    DbExtractedTextRepository,
    DbIntegrationRepository,
    DbRetentionNetworkRepository,
)
from boardmatch.infrastructure.repositories.factory import uses_database_backend
from boardmatch.integrations import InMemoryIntegrationRepository
from boardmatch.retention import (
    InMemoryExtractedTextRepository,
)
from boardmatch.retention import (
    InMemoryNetworkRepository as InMemoryRetentionNetworkRepository,
)

DraftRepo = InMemoryDraftRepository | DbDraftRepository
DocumentRepo = InMemoryDocumentRepository | DbDocumentRepository
IntegrationRepo = InMemoryIntegrationRepository | DbIntegrationRepository
AuditLoggerRepo = AuditLogger | DbAuditLogger
ExtractedTextRepo = InMemoryExtractedTextRepository | DbExtractedTextRepository
RetentionNetworkRepo = InMemoryRetentionNetworkRepository | DbRetentionNetworkRepository


@dataclass(frozen=True)
class ExtendedRepositories:
    """Bundle of repository instances for the "extended" (non-core) stores."""

    draft_repo: DraftRepo
    document_repo: DocumentRepo
    integration_repo: IntegrationRepo
    audit_logger: AuditLoggerRepo
    extracted_text_repo: ExtractedTextRepo
    retention_network_repo: RetentionNetworkRepo


# Process-wide cache of in-memory extended repository bundles, keyed by
# ``Settings.database_url``. See the equivalent cache in
# ``boardmatch.infrastructure.repositories.factory`` for the rationale: each
# router module calls ``create_extended_repositories`` independently at
# import time, and without caching every module would get its own isolated
# ``InMemory*`` instances instead of sharing state the way the DB-backed
# repositories already do.
_extended_repositories_cache: dict[str, ExtendedRepositories] = {}


def create_extended_repositories(
    settings: Settings, *, audit_retention_days: int | None = None
) -> ExtendedRepositories:
    """Build the extended repository bundle for ``settings.database_url``."""
    retention_days = (
        audit_retention_days
        if audit_retention_days is not None
        else settings.audit_log_retention_days
    )

    if uses_database_backend(settings.database_url):
        session_factory = get_session_factory(settings.database_url)
        return ExtendedRepositories(
            draft_repo=DbDraftRepository(session_factory),
            document_repo=DbDocumentRepository(session_factory),
            integration_repo=DbIntegrationRepository(session_factory),
            audit_logger=DbAuditLogger(session_factory, retention_days=retention_days),
            extracted_text_repo=DbExtractedTextRepository(session_factory),
            retention_network_repo=DbRetentionNetworkRepository(session_factory),
        )

    cached = _extended_repositories_cache.get(settings.database_url)
    if cached is None:
        cached = ExtendedRepositories(
            draft_repo=InMemoryDraftRepository(),
            document_repo=InMemoryDocumentRepository(),
            integration_repo=InMemoryIntegrationRepository(),
            audit_logger=AuditLogger(retention_days=retention_days),
            extracted_text_repo=InMemoryExtractedTextRepository(),
            retention_network_repo=InMemoryRetentionNetworkRepository(),
        )
        _extended_repositories_cache[settings.database_url] = cached

    return cached


def reset_extended_repositories_cache() -> None:
    """Clear the cached in-memory extended repository bundles (tests only)."""
    _extended_repositories_cache.clear()


_network_connection_repo_cache: dict[str, object] = {}


def create_network_connection_repo(settings: Settings):
    """Build the network-connection repository (memory or DB).

    Kept separate from :func:`create_extended_repositories` because the
    in-memory implementation lives in ``boardmatch.api.v1.network`` — a
    router module that itself imports other router modules, so importing it
    from here at module scope would create a circular import.

    The in-memory instance is cached per ``database_url`` for the same
    reason as :func:`create_extended_repositories` — so any future caller
    beyond ``boardmatch.api.v1.network`` shares the same store instead of
    getting an isolated one.
    """
    from boardmatch.infrastructure.repositories.extended_db import (
        DbNetworkConnectionRepository,
    )

    if uses_database_backend(settings.database_url):
        session_factory = get_session_factory(settings.database_url)
        return DbNetworkConnectionRepository(session_factory)

    cached = _network_connection_repo_cache.get(settings.database_url)
    if cached is None:
        from boardmatch.api.v1.network import InMemoryNetworkRepository

        cached = InMemoryNetworkRepository()
        _network_connection_repo_cache[settings.database_url] = cached

    return cached


def reset_network_connection_repo_cache() -> None:
    """Clear the cached in-memory network-connection repo (tests only)."""
    _network_connection_repo_cache.clear()
