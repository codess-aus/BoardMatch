"""Tests for privacy and retention controls (BM-031)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from boardmatch.retention import (
    CleanupResult,
    DEFAULT_DOCUMENT_RETENTION_DAYS,
    DEFAULT_EXTRACTED_TEXT_RETENTION_DAYS,
    ExtractedText,
    InMemoryExtractedTextRepository,
    InMemoryNetworkDataRepository,
    NetworkRecord,
    RetentionCategory,
    RetentionConfig,
    RetentionManager,
    redact_log_message,
    redact_sensitive_fields,
    revoke_integration_token,
    validate_storage_encryption,
    SENSITIVE_FIELDS,
)
from boardmatch.documents import Document, DocumentStatus, InMemoryDocumentRepository
from boardmatch.integrations import (
    InMemoryIntegrationRepository,
    Integration,
    IntegrationStatus,
)
from boardmatch.storage import LocalStorageBackend


class TestRetentionConfig:
    """Test retention configuration."""

    def test_default_retention_periods(self):
        config = RetentionConfig()
        assert config.document_retention_days == 365
        assert config.extracted_text_retention_days == 90
        assert config.network_data_retention_days == 180
        assert config.audit_log_retention_days == 90

    def test_custom_retention_periods(self):
        config = RetentionConfig(
            document_retention_days=30,
            extracted_text_retention_days=7,
        )
        assert config.document_retention_days == 30
        assert config.extracted_text_retention_days == 7

    def test_policies_list(self):
        config = RetentionConfig()
        policies = config.policies
        assert len(policies) == 4
        categories = {p.category for p in policies}
        assert RetentionCategory.DOCUMENT in categories
        assert RetentionCategory.EXTRACTED_TEXT in categories
        assert RetentionCategory.NETWORK_DATA in categories
        assert RetentionCategory.AUDIT_LOG in categories

    def test_get_policy_by_category(self):
        config = RetentionConfig(document_retention_days=60)
        policy = config.get_policy(RetentionCategory.DOCUMENT)
        assert policy.retention_days == 60


class TestRetentionCleanupSelection:
    """Test that cleanup correctly selects expired items."""

    def test_expired_documents_selected(self):
        """Documents past retention period are selected for cleanup."""
        doc_repo = InMemoryDocumentRepository()
        config = RetentionConfig(document_retention_days=30)

        # Add an expired document (uploaded 60 days ago)
        expired_doc = Document(
            id="doc-expired",
            user_id="user1",
            filename="old.pdf",
            content_type="application/pdf",
            size_bytes=1000,
            content_hash="abc123",
            storage_path="user1/old.pdf",
            uploaded_at=datetime.now(timezone.utc) - timedelta(days=60),
        )
        doc_repo.save(expired_doc)

        # Add a current document (uploaded today)
        current_doc = Document(
            id="doc-current",
            user_id="user1",
            filename="new.pdf",
            content_type="application/pdf",
            size_bytes=2000,
            content_hash="def456",
            storage_path="user1/new.pdf",
            uploaded_at=datetime.now(timezone.utc),
        )
        doc_repo.save(current_doc)

        manager = RetentionManager(config=config, document_repo=doc_repo)
        expired = manager.get_expired_documents()

        assert len(expired) == 1
        assert expired[0].id == "doc-expired"

    def test_non_expired_documents_not_selected(self):
        """Documents within retention period are not selected."""
        doc_repo = InMemoryDocumentRepository()
        config = RetentionConfig(document_retention_days=30)

        current_doc = Document(
            id="doc-current",
            user_id="user1",
            filename="new.pdf",
            content_type="application/pdf",
            size_bytes=2000,
            content_hash="def456",
            storage_path="user1/new.pdf",
            uploaded_at=datetime.now(timezone.utc) - timedelta(days=10),
        )
        doc_repo.save(current_doc)

        manager = RetentionManager(config=config, document_repo=doc_repo)
        expired = manager.get_expired_documents()
        assert len(expired) == 0


class TestExpiredDocumentDeletion:
    """Test that expired documents are properly deleted."""

    def test_cleanup_deletes_expired_documents(self):
        """Expired documents are deleted from both repo and storage."""
        doc_repo = InMemoryDocumentRepository()
        storage = MagicMock(spec=LocalStorageBackend)
        config = RetentionConfig(document_retention_days=30)

        expired_doc = Document(
            id="doc-expired",
            user_id="user1",
            filename="old.pdf",
            content_type="application/pdf",
            size_bytes=1000,
            content_hash="abc123",
            storage_path="user1/old.pdf",
            uploaded_at=datetime.now(timezone.utc) - timedelta(days=60),
        )
        doc_repo.save(expired_doc)

        manager = RetentionManager(
            config=config,
            document_repo=doc_repo,
            storage_backend=storage,
        )
        result = manager.cleanup_expired_documents()

        assert result.items_deleted == 1
        assert result.category == RetentionCategory.DOCUMENT
        storage.delete.assert_called_once_with("user1/old.pdf")
        assert doc_repo.get_by_id("doc-expired") is None

    def test_cleanup_keeps_current_documents(self):
        """Current documents survive cleanup."""
        doc_repo = InMemoryDocumentRepository()
        storage = MagicMock(spec=LocalStorageBackend)
        config = RetentionConfig(document_retention_days=30)

        current_doc = Document(
            id="doc-current",
            user_id="user1",
            filename="new.pdf",
            content_type="application/pdf",
            size_bytes=2000,
            content_hash="def456",
            storage_path="user1/new.pdf",
            uploaded_at=datetime.now(timezone.utc),
        )
        doc_repo.save(current_doc)

        manager = RetentionManager(
            config=config,
            document_repo=doc_repo,
            storage_backend=storage,
        )
        result = manager.cleanup_expired_documents()

        assert result.items_deleted == 0
        assert doc_repo.get_by_id("doc-current") is not None
        storage.delete.assert_not_called()


class TestExtractedTextRetention:
    """Test extracted text retention cleanup."""

    def test_expired_extracted_text_deleted(self):
        """Extracted text past retention is deleted."""
        text_repo = InMemoryExtractedTextRepository()
        config = RetentionConfig(extracted_text_retention_days=30)

        # Expired record
        old_text = ExtractedText(
            id="text-old",
            document_id="doc1",
            user_id="user1",
            text="Old CV content",
            extracted_at=datetime.now(timezone.utc) - timedelta(days=60),
        )
        text_repo.save(old_text)

        # Current record
        new_text = ExtractedText(
            id="text-new",
            document_id="doc2",
            user_id="user1",
            text="New CV content",
            extracted_at=datetime.now(timezone.utc),
        )
        text_repo.save(new_text)

        manager = RetentionManager(
            config=config,
            extracted_text_repo=text_repo,
        )
        result = manager.cleanup_expired_extracted_text()

        assert result.items_deleted == 1
        assert result.category == RetentionCategory.EXTRACTED_TEXT
        assert text_repo.get_by_document("doc1") is None
        assert text_repo.get_by_document("doc2") is not None


class TestNetworkDataDeletion:
    """Test network data deletion support."""

    def test_delete_network_data_for_user(self):
        """All network data for a user is deleted on request."""
        network_repo = InMemoryNetworkDataRepository()
        config = RetentionConfig()

        # Add records for two users
        network_repo.save(NetworkRecord(
            id="net1", user_id="user1", connection_name="Alice",
            relationship="colleague",
        ))
        network_repo.save(NetworkRecord(
            id="net2", user_id="user1", connection_name="Bob",
            relationship="mentor",
        ))
        network_repo.save(NetworkRecord(
            id="net3", user_id="user2", connection_name="Charlie",
            relationship="friend",
        ))

        manager = RetentionManager(config=config, network_repo=network_repo)
        deleted = manager.delete_network_data("user1")

        assert deleted == 2
        assert network_repo.list_by_user("user1") == []
        assert len(network_repo.list_by_user("user2")) == 1

    def test_delete_network_data_empty(self):
        """Deleting for a user with no data returns 0."""
        network_repo = InMemoryNetworkDataRepository()
        config = RetentionConfig()

        manager = RetentionManager(config=config, network_repo=network_repo)
        deleted = manager.delete_network_data("nonexistent")
        assert deleted == 0


class TestTokenRevocation:
    """Test integration token revocation."""

    def test_revoke_active_token(self):
        """Active integration tokens can be revoked."""
        repo = InMemoryIntegrationRepository()
        integration = Integration(
            user_id="user1",
            provider="microsoft",
            status=IntegrationStatus.ACTIVE,
            scopes=["User.Read"],
            token_hash="hashed_token_value",
        )
        repo.save(integration)

        result = revoke_integration_token("user1", "microsoft", repo)

        assert result is True
        revoked = repo.get("user1", "microsoft")
        assert revoked is not None
        assert revoked.status == IntegrationStatus.REVOKED
        assert revoked.token_hash is None
        assert revoked.revoked_at is not None

    def test_revoke_nonexistent_token(self):
        """Revoking a non-existent integration returns False."""
        repo = InMemoryIntegrationRepository()
        result = revoke_integration_token("user1", "microsoft", repo)
        assert result is False

    def test_revoke_already_revoked_token(self):
        """Revoking an already-revoked integration returns False."""
        repo = InMemoryIntegrationRepository()
        integration = Integration(
            user_id="user1",
            provider="microsoft",
            status=IntegrationStatus.REVOKED,
            scopes=["User.Read"],
            token_hash=None,
            revoked_at=datetime.now(timezone.utc),
        )
        repo.save(integration)

        result = revoke_integration_token("user1", "microsoft", repo)
        assert result is False

    def test_revoke_creates_audit_event(self):
        """Token revocation creates an audit trail."""
        repo = InMemoryIntegrationRepository()
        integration = Integration(
            user_id="user1",
            provider="microsoft",
            status=IntegrationStatus.ACTIVE,
            scopes=["User.Read", "Mail.Read"],
            token_hash="hashed_token",
        )
        repo.save(integration)

        revoke_integration_token("user1", "microsoft", repo)

        events = repo.get_audit_events("user1")
        assert len(events) == 1
        assert events[0].event_type.value == "consent_revoked"
        assert events[0].provider == "microsoft"


class TestLogRedaction:
    """Test privacy-sensitive log redaction."""

    def test_redact_sensitive_dict_fields(self):
        """Sensitive fields in dictionaries are masked."""
        data = {
            "user_id": "user1",
            "email": "john@example.com",
            "phone": "+61412345678",
            "name": "John Doe",
            "token": "secret-token-value",
            "password": "hunter2",
        }
        redacted = redact_sensitive_fields(data)

        assert redacted["user_id"] == "user1"
        assert redacted["name"] == "John Doe"
        assert redacted["email"] == "***REDACTED***"
        assert redacted["phone"] == "***REDACTED***"
        assert redacted["token"] == "***REDACTED***"
        assert redacted["password"] == "***REDACTED***"

    def test_redact_nested_dict(self):
        """Nested dictionaries are recursively redacted."""
        data = {
            "user": {
                "name": "Jane",
                "email": "jane@example.com",
            },
            "action": "login",
        }
        redacted = redact_sensitive_fields(data)

        assert redacted["user"]["name"] == "Jane"
        assert redacted["user"]["email"] == "***REDACTED***"
        assert redacted["action"] == "login"

    def test_redact_email_in_log_message(self):
        """Email addresses in log strings are redacted."""
        msg = "User john@example.com logged in from 1.2.3.4"
        redacted = redact_log_message(msg)
        assert "john@example.com" not in redacted
        assert "[REDACTED_EMAIL]" in redacted

    def test_redact_phone_in_log_message(self):
        """Phone numbers in log strings are redacted."""
        msg = "Contact number: +61 412 345 678"
        redacted = redact_log_message(msg)
        assert "+61 412 345 678" not in redacted
        assert "[REDACTED_PHONE]" in redacted

    def test_no_redaction_for_safe_message(self):
        """Messages without PII pass through unchanged."""
        msg = "Application created for opportunity opp-123"
        redacted = redact_log_message(msg)
        assert redacted == msg

    def test_redact_list_with_dicts(self):
        """Lists containing dicts are redacted recursively."""
        data = {
            "users": [
                {"name": "Alice", "email": "alice@example.com"},
                {"name": "Bob", "email": "bob@example.com"},
            ]
        }
        redacted = redact_sensitive_fields(data)
        assert redacted["users"][0]["email"] == "***REDACTED***"
        assert redacted["users"][1]["email"] == "***REDACTED***"
        assert redacted["users"][0]["name"] == "Alice"


class TestExportAfterDeletion:
    """Test that data export returns empty after deletion request."""

    def test_export_after_network_deletion(self):
        """After network data is deleted, user sees no network records."""
        network_repo = InMemoryNetworkDataRepository()
        config = RetentionConfig()

        network_repo.save(NetworkRecord(
            id="net1", user_id="user1", connection_name="Alice",
            relationship="colleague",
        ))

        manager = RetentionManager(config=config, network_repo=network_repo)
        manager.delete_network_data("user1")

        # After deletion, listing returns empty
        records = network_repo.list_by_user("user1")
        assert records == []

    def test_export_after_document_deletion(self):
        """After document cleanup, expired docs are gone from exports."""
        doc_repo = InMemoryDocumentRepository()
        storage = MagicMock(spec=LocalStorageBackend)
        config = RetentionConfig(document_retention_days=30)

        expired_doc = Document(
            id="doc-expired",
            user_id="user1",
            filename="old.pdf",
            content_type="application/pdf",
            size_bytes=1000,
            content_hash="abc123",
            storage_path="user1/old.pdf",
            uploaded_at=datetime.now(timezone.utc) - timedelta(days=60),
        )
        doc_repo.save(expired_doc)

        manager = RetentionManager(
            config=config,
            document_repo=doc_repo,
            storage_backend=storage,
        )
        manager.cleanup_expired_documents()

        # Export for user should not contain deleted doc
        remaining = doc_repo.list_by_user("user1")
        assert len(remaining) == 0


class TestStorageEncryption:
    """Test production storage encryption validation."""

    def test_production_requires_storage_account(self):
        """Production environment requires a storage account for encryption."""
        assert validate_storage_encryption("production", "boardmatchdata") is True
        assert validate_storage_encryption("production", None) is False
        assert validate_storage_encryption("production", "") is False

    def test_non_production_always_valid(self):
        """Non-production environments pass encryption check."""
        assert validate_storage_encryption("local", None) is True
        assert validate_storage_encryption("test", None) is True


class TestRunAllCleanups:
    """Test the combined cleanup operation."""

    def test_run_all_cleanups(self):
        """Running all cleanups returns results for each category."""
        doc_repo = InMemoryDocumentRepository()
        text_repo = InMemoryExtractedTextRepository()
        config = RetentionConfig(
            document_retention_days=30,
            extracted_text_retention_days=30,
        )

        # Add expired items
        doc_repo.save(Document(
            id="doc-old",
            user_id="user1",
            filename="old.pdf",
            content_type="application/pdf",
            size_bytes=1000,
            content_hash="abc",
            storage_path="user1/old.pdf",
            uploaded_at=datetime.now(timezone.utc) - timedelta(days=60),
        ))
        text_repo.save(ExtractedText(
            id="text-old",
            document_id="doc-old",
            user_id="user1",
            text="Old text",
            extracted_at=datetime.now(timezone.utc) - timedelta(days=60),
        ))

        storage = MagicMock(spec=LocalStorageBackend)
        manager = RetentionManager(
            config=config,
            document_repo=doc_repo,
            extracted_text_repo=text_repo,
            storage_backend=storage,
        )
        results = manager.run_all_cleanups()

        assert len(results) == 2
        assert results[0].category == RetentionCategory.DOCUMENT
        assert results[0].items_deleted == 1
        assert results[1].category == RetentionCategory.EXTRACTED_TEXT
        assert results[1].items_deleted == 1
