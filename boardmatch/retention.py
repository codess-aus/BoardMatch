"""Privacy and retention controls for BoardMatch (BM-031).

Provides configurable document retention policies, CV text retention,
network data deletion, integration token revocation, and log redaction.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

from boardmatch.config import AppEnvironment, Settings
from boardmatch.documents import Document, DocumentRepository
from boardmatch.integrations import (
    AuditEvent,
    AuditEventType,
    IntegrationRepository,
    IntegrationStatus,
)
from boardmatch.storage import StorageBackend

logger = logging.getLogger(__name__)


# --- Retention Configuration ---


@dataclass(frozen=True)
class RetentionPolicy:
    """Configurable retention periods for different data types."""

    document_retention_days: int = 365
    extracted_text_retention_days: int = 90
    audit_log_retention_days: int = 90
    network_data_retention_days: int = 365


DEFAULT_RETENTION_POLICY = RetentionPolicy()


# --- Log Redaction ---

_SENSITIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
        "[EMAIL_REDACTED]",
    ),
    (
        re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"),
        "[PHONE_REDACTED]",
    ),
    (
        re.compile(
            r'\b(?:token_hash|token|access_token|refresh_token)["\']?\s*[:=]\s*["\']?[\w\-./+=]+',
            re.IGNORECASE,
        ),
        "[TOKEN_REDACTED]",
    ),
    (
        re.compile(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b"),
        "[SSN_REDACTED]",
    ),
]


def redact_sensitive_data(text: str) -> str:
    """Remove privacy-sensitive information from log messages.

    Redacts email addresses, phone numbers, tokens, and other PII.
    """
    result = text
    for pattern, replacement in _SENSITIVE_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


class RedactingLogFilter(logging.Filter):
    """Logging filter that redacts sensitive data from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_sensitive_data(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: redact_sensitive_data(str(v)) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_sensitive_data(str(a)) if isinstance(a, str) else a
                    for a in record.args
                )
        return True


# --- Storage Encryption Validation ---


def validate_storage_encryption(settings: Settings) -> bool:
    """Validate that production storage is configured with encryption.

    In production, an Azure Storage account must be configured (which provides
    encryption at rest by default). Non-production environments skip this check.
    """
    if settings.app_env != AppEnvironment.PRODUCTION:
        return True
    return settings.azure_storage_account is not None


class StorageEncryptionError(Exception):
    """Raised when production storage lacks required encryption configuration."""


# --- Retention Service ---


@runtime_checkable
class ExtractedTextRepository(Protocol):
    """Protocol for extracted CV text persistence with retention support."""

    def save(self, document_id: str, text: str, user_id: str) -> None: ...
    def get(self, document_id: str) -> str | None: ...
    def delete(self, document_id: str) -> bool: ...
    def list_by_user(self, user_id: str) -> list[str]: ...
    def get_creation_time(self, document_id: str) -> datetime | None: ...


@dataclass
class ExtractedTextRecord:
    """Record of extracted CV text with creation timestamp."""

    document_id: str
    user_id: str
    text: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InMemoryExtractedTextRepository:
    """In-memory implementation of ExtractedTextRepository for dev/test."""

    def __init__(self) -> None:
        self._store: dict[str, ExtractedTextRecord] = {}

    def save(self, document_id: str, text: str, user_id: str) -> None:
        self._store[document_id] = ExtractedTextRecord(
            document_id=document_id, user_id=user_id, text=text
        )

    def get(self, document_id: str) -> str | None:
        record = self._store.get(document_id)
        return record.text if record else None

    def delete(self, document_id: str) -> bool:
        if document_id in self._store:
            del self._store[document_id]
            return True
        return False

    def list_by_user(self, user_id: str) -> list[str]:
        return [
            doc_id
            for doc_id, record in self._store.items()
            if record.user_id == user_id
        ]

    def get_creation_time(self, document_id: str) -> datetime | None:
        record = self._store.get(document_id)
        return record.created_at if record else None


@runtime_checkable
class NetworkRepository(Protocol):
    """Protocol for network data with deletion support."""

    def delete_all_for_user(self, user_id: str) -> int: ...


class InMemoryNetworkRepository:
    """In-memory network repository with bulk deletion support."""

    def __init__(self) -> None:
        self._connections: dict[str, dict] = {}

    def save(self, connection_id: str, user_id: str, data: dict) -> None:
        self._connections[connection_id] = {"user_id": user_id, **data}

    def list_by_user(self, user_id: str) -> list[dict]:
        return [
            conn for conn in self._connections.values()
            if conn["user_id"] == user_id
        ]

    def delete_all_for_user(self, user_id: str) -> int:
        """Delete all network data for a user. Returns count of deleted records."""
        to_delete = [
            cid for cid, conn in self._connections.items()
            if conn["user_id"] == user_id
        ]
        for cid in to_delete:
            del self._connections[cid]
        return len(to_delete)


@dataclass
class RetentionResult:
    """Result of a retention cleanup operation."""

    documents_deleted: int = 0
    extracted_texts_deleted: int = 0
    audit_events_purged: int = 0


class RetentionService:
    """Manages data retention policies and cleanup operations."""

    def __init__(
        self,
        policy: RetentionPolicy | None = None,
        document_repo: DocumentRepository | None = None,
        storage_backend: StorageBackend | None = None,
        extracted_text_repo: ExtractedTextRepository | None = None,
    ) -> None:
        self.policy = policy or DEFAULT_RETENTION_POLICY
        self._document_repo = document_repo
        self._storage_backend = storage_backend
        self._extracted_text_repo = extracted_text_repo

    def get_expired_documents(self, user_id: str) -> list[Document]:
        """Identify documents past retention period for a user."""
        if self._document_repo is None:
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=self.policy.document_retention_days
        )
        documents = self._document_repo.list_by_user(user_id)
        return [doc for doc in documents if doc.uploaded_at < cutoff]

    def get_expired_extracted_texts(self, user_id: str) -> list[str]:
        """Identify extracted texts past retention period for a user."""
        if self._extracted_text_repo is None:
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=self.policy.extracted_text_retention_days
        )
        doc_ids = self._extracted_text_repo.list_by_user(user_id)
        expired = []
        for doc_id in doc_ids:
            created = self._extracted_text_repo.get_creation_time(doc_id)
            if created is not None and created < cutoff:
                expired.append(doc_id)
        return expired

    def cleanup_expired_documents(self, user_id: str) -> int:
        """Delete documents and their storage files past retention period."""
        expired = self.get_expired_documents(user_id)
        deleted = 0
        for doc in expired:
            if self._storage_backend and self._storage_backend.exists(doc.storage_path):
                self._storage_backend.delete(doc.storage_path)
            if self._document_repo:
                self._document_repo.delete(doc.id)
            deleted += 1
        return deleted

    def cleanup_expired_texts(self, user_id: str) -> int:
        """Delete extracted CV texts past retention period."""
        expired = self.get_expired_extracted_texts(user_id)
        deleted = 0
        for doc_id in expired:
            if self._extracted_text_repo:
                self._extracted_text_repo.delete(doc_id)
                deleted += 1
        return deleted

    def run_cleanup(self, user_id: str) -> RetentionResult:
        """Run full retention cleanup for a user."""
        return RetentionResult(
            documents_deleted=self.cleanup_expired_documents(user_id),
            extracted_texts_deleted=self.cleanup_expired_texts(user_id),
        )

    def delete_all_user_data(self, user_id: str) -> RetentionResult:
        """Delete all user data immediately (for deletion requests)."""
        result = RetentionResult()

        if self._document_repo:
            docs = self._document_repo.list_by_user(user_id)
            for doc in docs:
                if self._storage_backend and self._storage_backend.exists(
                    doc.storage_path
                ):
                    self._storage_backend.delete(doc.storage_path)
                self._document_repo.delete(doc.id)
                result.documents_deleted += 1

        if self._extracted_text_repo:
            doc_ids = self._extracted_text_repo.list_by_user(user_id)
            for doc_id in doc_ids:
                self._extracted_text_repo.delete(doc_id)
                result.extracted_texts_deleted += 1

        return result


def revoke_integration_token(
    user_id: str, provider: str, repo: IntegrationRepository
) -> bool:
    """Revoke an integration token, clearing the stored hash.

    Returns True if token was revoked, False if no active integration found.
    """
    integration = repo.get(user_id, provider)
    if integration is None:
        return False
    if integration.status == IntegrationStatus.REVOKED:
        return False

    revoked = integration.model_copy(
        update={
            "status": IntegrationStatus.REVOKED,
            "revoked_at": datetime.now(timezone.utc),
            "token_hash": None,
        }
    )
    repo.save(revoked)

    repo.add_audit_event(
        AuditEvent(
            user_id=user_id,
            provider=provider,
            event_type=AuditEventType.CONSENT_REVOKED,
            scopes=integration.scopes,
        )
    )
    return True


def delete_network_data(user_id: str, repo: NetworkRepository) -> int:
    """Delete all network data for a user. Returns count of deleted records."""
    return repo.delete_all_for_user(user_id)


def export_after_deletion(
    user_id: str, document_repo: DocumentRepository | None = None
) -> dict:
    """Generate an export response after a deletion request.

    Returns minimal confirmation since data has been removed.
    """
    remaining_docs: list[Document] = []
    if document_repo:
        remaining_docs = document_repo.list_by_user(user_id)

    return {
        "user_id": user_id,
        "status": "deletion_completed",
        "remaining_documents": len(remaining_docs),
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }
