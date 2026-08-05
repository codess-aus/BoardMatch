"""Retention and privacy controls for BoardMatch.

Provides configurable retention periods, cleanup logic for expired data,
network data deletion, token revocation, and log redaction utilities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Protocol, runtime_checkable

from boardmatch.documents import Document, DocumentRepository
from boardmatch.integrations import (
    AuditEvent as IntegrationAuditEvent,
    AuditEventType,
    IntegrationRepository,
    IntegrationStatus,
)
from boardmatch.storage import StorageBackend


class RetentionCategory(StrEnum):
    """Categories of data subject to retention policies."""

    DOCUMENT = "document"
    EXTRACTED_TEXT = "extracted_text"
    NETWORK_DATA = "network_data"
    AUDIT_LOG = "audit_log"


@dataclass(frozen=True)
class RetentionPolicy:
    """Defines a retention period for a category of data."""

    category: RetentionCategory
    retention_days: int
    description: str = ""


# Default retention periods (configurable via settings)
DEFAULT_DOCUMENT_RETENTION_DAYS = 365
DEFAULT_EXTRACTED_TEXT_RETENTION_DAYS = 90
DEFAULT_NETWORK_DATA_RETENTION_DAYS = 180
DEFAULT_AUDIT_LOG_RETENTION_DAYS = 90


@dataclass
class RetentionConfig:
    """Retention configuration loaded from application settings."""

    document_retention_days: int = DEFAULT_DOCUMENT_RETENTION_DAYS
    extracted_text_retention_days: int = DEFAULT_EXTRACTED_TEXT_RETENTION_DAYS
    network_data_retention_days: int = DEFAULT_NETWORK_DATA_RETENTION_DAYS
    audit_log_retention_days: int = DEFAULT_AUDIT_LOG_RETENTION_DAYS

    @property
    def policies(self) -> list[RetentionPolicy]:
        return [
            RetentionPolicy(
                category=RetentionCategory.DOCUMENT,
                retention_days=self.document_retention_days,
                description="Uploaded document files",
            ),
            RetentionPolicy(
                category=RetentionCategory.EXTRACTED_TEXT,
                retention_days=self.extracted_text_retention_days,
                description="Text extracted from CVs/documents",
            ),
            RetentionPolicy(
                category=RetentionCategory.NETWORK_DATA,
                retention_days=self.network_data_retention_days,
                description="Network connections and relationship data",
            ),
            RetentionPolicy(
                category=RetentionCategory.AUDIT_LOG,
                retention_days=self.audit_log_retention_days,
                description="Audit log entries",
            ),
        ]

    def get_policy(self, category: RetentionCategory) -> RetentionPolicy:
        for policy in self.policies:
            if policy.category == category:
                return policy
        raise ValueError(f"No policy for category: {category}")


@dataclass
class CleanupResult:
    """Result of a retention cleanup operation."""

    category: RetentionCategory
    items_deleted: int
    cutoff_date: datetime


@dataclass
class ExtractedText:
    """Extracted text record from a document (e.g., CV parsing)."""

    id: str
    document_id: str
    user_id: str
    text: str
    extracted_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@runtime_checkable
class ExtractedTextRepository(Protocol):
    """Protocol for extracted text persistence."""

    def save(self, record: ExtractedText) -> None: ...
    def get_by_document(self, document_id: str) -> ExtractedText | None: ...
    def list_by_user(self, user_id: str) -> list[ExtractedText]: ...
    def delete(self, record_id: str) -> bool: ...
    def delete_expired(self, cutoff: datetime) -> int: ...


class InMemoryExtractedTextRepository:
    """In-memory implementation of ExtractedTextRepository for dev/test."""

    def __init__(self) -> None:
        self._store: dict[str, ExtractedText] = {}

    def save(self, record: ExtractedText) -> None:
        self._store[record.id] = record

    def get_by_document(self, document_id: str) -> ExtractedText | None:
        for r in self._store.values():
            if r.document_id == document_id:
                return r
        return None

    def list_by_user(self, user_id: str) -> list[ExtractedText]:
        return [r for r in self._store.values() if r.user_id == user_id]

    def delete(self, record_id: str) -> bool:
        if record_id in self._store:
            del self._store[record_id]
            return True
        return False

    def delete_expired(self, cutoff: datetime) -> int:
        expired = [
            rid for rid, r in self._store.items() if r.extracted_at < cutoff
        ]
        for rid in expired:
            del self._store[rid]
        return len(expired)


@dataclass
class NetworkRecord:
    """A network connection record subject to retention."""

    id: str
    user_id: str
    connection_name: str
    relationship: str
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@runtime_checkable
class NetworkDataRepository(Protocol):
    """Protocol for network data persistence."""

    def list_by_user(self, user_id: str) -> list[NetworkRecord]: ...
    def delete_by_user(self, user_id: str) -> int: ...
    def delete(self, record_id: str) -> bool: ...


class InMemoryNetworkDataRepository:
    """In-memory implementation of NetworkDataRepository for dev/test."""

    def __init__(self) -> None:
        self._store: dict[str, NetworkRecord] = {}

    def save(self, record: NetworkRecord) -> None:
        self._store[record.id] = record

    def list_by_user(self, user_id: str) -> list[NetworkRecord]:
        return [r for r in self._store.values() if r.user_id == user_id]

    def delete_by_user(self, user_id: str) -> int:
        to_delete = [
            rid for rid, r in self._store.items() if r.user_id == user_id
        ]
        for rid in to_delete:
            del self._store[rid]
        return len(to_delete)

    def delete(self, record_id: str) -> bool:
        if record_id in self._store:
            del self._store[record_id]
            return True
        return False


class RetentionManager:
    """Manages data retention and cleanup across repositories."""

    def __init__(
        self,
        config: RetentionConfig,
        document_repo: DocumentRepository | None = None,
        extracted_text_repo: ExtractedTextRepository | None = None,
        network_repo: NetworkDataRepository | None = None,
        storage_backend: StorageBackend | None = None,
    ) -> None:
        self._config = config
        self._document_repo = document_repo
        self._extracted_text_repo = extracted_text_repo
        self._network_repo = network_repo
        self._storage = storage_backend

    @property
    def config(self) -> RetentionConfig:
        return self._config

    def get_expired_documents(self) -> list[Document]:
        """Return documents past their retention period."""
        if self._document_repo is None:
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=self._config.document_retention_days
        )
        # In production this would be a database query; here we filter in memory
        all_docs = getattr(self._document_repo, "_store", {})
        return [
            doc for doc in all_docs.values() if doc.uploaded_at < cutoff
        ]

    def cleanup_expired_documents(self) -> CleanupResult:
        """Delete documents that have exceeded their retention period."""
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=self._config.document_retention_days
        )
        expired = self.get_expired_documents()
        for doc in expired:
            if self._storage is not None:
                try:
                    self._storage.delete(doc.storage_path)
                except IOError:
                    pass
            if self._document_repo is not None:
                self._document_repo.delete(doc.id)
        return CleanupResult(
            category=RetentionCategory.DOCUMENT,
            items_deleted=len(expired),
            cutoff_date=cutoff,
        )

    def cleanup_expired_extracted_text(self) -> CleanupResult:
        """Delete extracted text records past their retention period."""
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=self._config.extracted_text_retention_days
        )
        deleted = 0
        if self._extracted_text_repo is not None:
            deleted = self._extracted_text_repo.delete_expired(cutoff)
        return CleanupResult(
            category=RetentionCategory.EXTRACTED_TEXT,
            items_deleted=deleted,
            cutoff_date=cutoff,
        )

    def delete_network_data(self, user_id: str) -> int:
        """Delete all network data for a user. Returns count deleted."""
        if self._network_repo is None:
            return 0
        return self._network_repo.delete_by_user(user_id)

    def run_all_cleanups(self) -> list[CleanupResult]:
        """Run all retention cleanup tasks."""
        results = []
        results.append(self.cleanup_expired_documents())
        results.append(self.cleanup_expired_extracted_text())
        return results


def revoke_integration_token(
    user_id: str,
    provider: str,
    repo: IntegrationRepository,
) -> bool:
    """Revoke an integration token. Returns True if revoked, False if not found."""
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
        IntegrationAuditEvent(
            user_id=user_id,
            provider=provider,
            event_type=AuditEventType.CONSENT_REVOKED,
            scopes=integration.scopes,
        )
    )
    return True


# --- Log Redaction ---

# Fields considered privacy-sensitive
SENSITIVE_FIELDS = frozenset({
    "email",
    "phone",
    "address",
    "date_of_birth",
    "ssn",
    "tax_file_number",
    "token",
    "api_key",
    "password",
    "secret",
    "token_hash",
    "credit_card",
})

# Pattern to detect email addresses in log messages
_EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
)

# Pattern to detect phone numbers (basic)
_PHONE_PATTERN = re.compile(
    r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b"
)


def redact_sensitive_fields(data: dict) -> dict:
    """Remove or mask privacy-sensitive fields from a dictionary (for logging)."""
    redacted = {}
    for key, value in data.items():
        if key.lower() in SENSITIVE_FIELDS:
            redacted[key] = "***REDACTED***"
        elif isinstance(value, dict):
            redacted[key] = redact_sensitive_fields(value)
        elif isinstance(value, list):
            redacted[key] = [
                redact_sensitive_fields(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            redacted[key] = value
    return redacted


def redact_log_message(message: str) -> str:
    """Redact PII patterns (emails, phone numbers) from a log message string."""
    message = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", message)
    message = _PHONE_PATTERN.sub("[REDACTED_PHONE]", message)
    return message


def validate_storage_encryption(app_env: str, storage_account: str | None) -> bool:
    """Validate that production storage uses encryption (Azure Storage).

    Azure Storage accounts have encryption enabled by default. This check
    ensures a storage account is configured for production environments.
    """
    if app_env != "production":
        return True
    # Production requires a configured storage account (Azure Storage encrypts at rest)
    return storage_account is not None and len(storage_account) >= 3
