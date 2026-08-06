"""Tests for privacy and retention controls (BM-031)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from boardmatch.config import AppEnvironment, Settings
from boardmatch.documents import Document, InMemoryDocumentRepository
from boardmatch.integrations import (
    InMemoryIntegrationRepository,
    Integration,
    IntegrationStatus,
    hash_token,
)
from boardmatch.retention import (
    DEFAULT_RETENTION_POLICY,
    InMemoryExtractedTextRepository,
    InMemoryNetworkRepository,
    RedactingLogFilter,
    RetentionPolicy,
    RetentionService,
    delete_network_data,
    export_after_deletion,
    redact_sensitive_data,
    revoke_integration_token,
    validate_storage_encryption,
)
from boardmatch.storage import LocalStorageBackend


class TestRetentionPolicyConfiguration:
    """Retention periods are configurable."""

    def test_default_retention_policy(self):
        policy = DEFAULT_RETENTION_POLICY
        assert policy.document_retention_days == 365
        assert policy.extracted_text_retention_days == 90
        assert policy.audit_log_retention_days == 90
        assert policy.network_data_retention_days == 365

    def test_custom_retention_policy(self):
        policy = RetentionPolicy(
            document_retention_days=180,
            extracted_text_retention_days=30,
            audit_log_retention_days=60,
            network_data_retention_days=90,
        )
        assert policy.document_retention_days == 180
        assert policy.extracted_text_retention_days == 30
        assert policy.audit_log_retention_days == 60
        assert policy.network_data_retention_days == 90

    def test_config_retention_settings(self):
        settings = Settings(
            document_retention_days=180,
            extracted_text_retention_days=30,
            audit_log_retention_days=60,
            network_data_retention_days=90,
        )
        assert settings.document_retention_days == 180
        assert settings.extracted_text_retention_days == 30
        assert settings.audit_log_retention_days == 60
        assert settings.network_data_retention_days == 90


class TestRetentionCleanupSelection:
    """Retention cleanup correctly identifies expired data."""

    def test_selects_expired_documents(self):
        doc_repo = InMemoryDocumentRepository()
        storage = LocalStorageBackend()
        policy = RetentionPolicy(document_retention_days=30)
        old_doc = Document(
            id="old-doc",
            user_id="user-1",
            filename="old.pdf",
            content_type="application/pdf",
            size_bytes=1024,
            content_hash="abc123",
            storage_path="docs/old.pdf",
            uploaded_at=datetime.now(timezone.utc) - timedelta(days=60),
        )
        doc_repo.save(old_doc)
        recent_doc = Document(
            id="recent-doc",
            user_id="user-1",
            filename="recent.pdf",
            content_type="application/pdf",
            size_bytes=1024,
            content_hash="def456",
            storage_path="docs/recent.pdf",
            uploaded_at=datetime.now(timezone.utc) - timedelta(days=10),
        )
        doc_repo.save(recent_doc)
        service = RetentionService(
            policy=policy, document_repo=doc_repo, storage_backend=storage
        )
        expired = service.get_expired_documents("user-1")
        assert len(expired) == 1
        assert expired[0].id == "old-doc"

    def test_selects_expired_extracted_texts(self):
        text_repo = InMemoryExtractedTextRepository()
        policy = RetentionPolicy(extracted_text_retention_days=30)
        text_repo.save("doc-old", "Old CV text content", "user-1")
        text_repo._store["doc-old"].created_at = datetime.now(timezone.utc) - timedelta(
            days=60
        )
        text_repo.save("doc-recent", "Recent CV text", "user-1")
        service = RetentionService(policy=policy, extracted_text_repo=text_repo)
        expired = service.get_expired_extracted_texts("user-1")
        assert len(expired) == 1
        assert expired[0] == "doc-old"

    def test_no_expired_when_within_retention(self):
        doc_repo = InMemoryDocumentRepository()
        policy = RetentionPolicy(document_retention_days=365)
        doc = Document(
            id="fresh-doc",
            user_id="user-1",
            filename="fresh.pdf",
            content_type="application/pdf",
            size_bytes=1024,
            content_hash="abc",
            storage_path="docs/fresh.pdf",
            uploaded_at=datetime.now(timezone.utc) - timedelta(days=10),
        )
        doc_repo.save(doc)
        service = RetentionService(policy=policy, document_repo=doc_repo)
        expired = service.get_expired_documents("user-1")
        assert len(expired) == 0


class TestExpiredDocumentDeletion:
    """Expired documents are properly deleted."""

    def test_deletes_expired_document_and_storage(self):
        doc_repo = InMemoryDocumentRepository()
        storage = LocalStorageBackend()
        policy = RetentionPolicy(document_retention_days=30)
        storage.save("docs/old.pdf", b"old document content")
        old_doc = Document(
            id="old-doc",
            user_id="user-1",
            filename="old.pdf",
            content_type="application/pdf",
            size_bytes=1024,
            content_hash="abc123",
            storage_path="docs/old.pdf",
            uploaded_at=datetime.now(timezone.utc) - timedelta(days=60),
        )
        doc_repo.save(old_doc)
        service = RetentionService(
            policy=policy, document_repo=doc_repo, storage_backend=storage
        )
        deleted = service.cleanup_expired_documents("user-1")
        assert deleted == 1
        assert doc_repo.get_by_id("old-doc") is None
        assert not storage.exists("docs/old.pdf")

    def test_deletes_expired_extracted_text(self):
        text_repo = InMemoryExtractedTextRepository()
        policy = RetentionPolicy(extracted_text_retention_days=30)
        text_repo.save("doc-old", "Extracted CV content here", "user-1")
        text_repo._store["doc-old"].created_at = datetime.now(timezone.utc) - timedelta(
            days=60
        )
        service = RetentionService(policy=policy, extracted_text_repo=text_repo)
        deleted = service.cleanup_expired_texts("user-1")
        assert deleted == 1
        assert text_repo.get("doc-old") is None

    def test_full_cleanup_run(self):
        doc_repo = InMemoryDocumentRepository()
        text_repo = InMemoryExtractedTextRepository()
        storage = LocalStorageBackend()
        policy = RetentionPolicy(
            document_retention_days=30, extracted_text_retention_days=30
        )
        storage.save("docs/old.pdf", b"content")
        doc_repo.save(
            Document(
                id="old-doc",
                user_id="user-1",
                filename="old.pdf",
                content_type="application/pdf",
                size_bytes=1024,
                content_hash="hash1",
                storage_path="docs/old.pdf",
                uploaded_at=datetime.now(timezone.utc) - timedelta(days=60),
            )
        )
        text_repo.save("old-text-doc", "text", "user-1")
        text_repo._store["old-text-doc"].created_at = datetime.now(
            timezone.utc
        ) - timedelta(days=60)
        service = RetentionService(
            policy=policy,
            document_repo=doc_repo,
            storage_backend=storage,
            extracted_text_repo=text_repo,
        )
        result = service.run_cleanup("user-1")
        assert result.documents_deleted == 1
        assert result.extracted_texts_deleted == 1


class TestNetworkDataDeletion:
    """Network data deletion is supported."""

    def test_delete_all_network_data(self):
        repo = InMemoryNetworkRepository()
        repo.save("conn-1", "user-1", {"name": "Alice", "relationship": "colleague"})
        repo.save("conn-2", "user-1", {"name": "Bob", "relationship": "friend"})
        repo.save("conn-3", "user-2", {"name": "Charlie", "relationship": "mentor"})
        deleted = delete_network_data("user-1", repo)
        assert deleted == 2
        assert repo.list_by_user("user-1") == []
        assert len(repo.list_by_user("user-2")) == 1

    def test_delete_empty_network(self):
        repo = InMemoryNetworkRepository()
        deleted = delete_network_data("user-1", repo)
        assert deleted == 0


class TestTokenRevocation:
    """Integration tokens are revocable."""

    def test_revoke_active_token(self):
        repo = InMemoryIntegrationRepository()
        integration = Integration(
            user_id="user-1",
            provider="microsoft",
            status=IntegrationStatus.ACTIVE,
            scopes=["User.Read"],
            token_hash=hash_token("some-token"),
        )
        repo.save(integration)
        result = revoke_integration_token("user-1", "microsoft", repo)
        assert result is True
        updated = repo.get("user-1", "microsoft")
        assert updated is not None
        assert updated.status == IntegrationStatus.REVOKED
        assert updated.token_hash is None
        assert updated.revoked_at is not None

    def test_revoke_creates_audit_event(self):
        repo = InMemoryIntegrationRepository()
        integration = Integration(
            user_id="user-1",
            provider="microsoft",
            status=IntegrationStatus.ACTIVE,
            scopes=["User.Read", "Mail.Read"],
            token_hash=hash_token("token-1"),
        )
        repo.save(integration)
        revoke_integration_token("user-1", "microsoft", repo)
        events = repo.get_audit_events("user-1")
        assert len(events) == 1
        assert events[0].event_type == "consent_revoked"
        assert events[0].provider == "microsoft"

    def test_revoke_already_revoked(self):
        repo = InMemoryIntegrationRepository()
        integration = Integration(
            user_id="user-1",
            provider="microsoft",
            status=IntegrationStatus.REVOKED,
            revoked_at=datetime.now(timezone.utc),
            token_hash=None,
        )
        repo.save(integration)
        result = revoke_integration_token("user-1", "microsoft", repo)
        assert result is False

    def test_revoke_nonexistent(self):
        repo = InMemoryIntegrationRepository()
        result = revoke_integration_token("user-1", "microsoft", repo)
        assert result is False


class TestLogRedaction:
    """Privacy-sensitive fields are excluded from logs."""

    def test_redacts_email(self):
        text = "User email is alice@example.com and she logged in"
        result = redact_sensitive_data(text)
        assert "alice@example.com" not in result
        assert "[EMAIL_REDACTED]" in result

    def test_redacts_phone_number(self):
        text = "Contact phone: 555-123-4567"
        result = redact_sensitive_data(text)
        assert "555-123-4567" not in result
        assert "[PHONE_REDACTED]" in result

    def test_redacts_token(self):
        text = "token_hash: abc123def456"
        result = redact_sensitive_data(text)
        assert "abc123def456" not in result
        assert "[TOKEN_REDACTED]" in result

    def test_preserves_non_sensitive_data(self):
        text = "User profile was updated successfully"
        result = redact_sensitive_data(text)
        assert result == text

    def test_redacting_log_filter(self):
        log_filter = RedactingLogFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="User alice@example.com logged in",
            args=None,
            exc_info=None,
        )
        log_filter.filter(record)
        assert "alice@example.com" not in record.msg
        assert "[EMAIL_REDACTED]" in record.msg

    def test_redacting_log_filter_with_args(self):
        log_filter = RedactingLogFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="User %s logged in",
            args=("alice@example.com",),
            exc_info=None,
        )
        log_filter.filter(record)
        assert "alice@example.com" not in str(record.args)


class TestStorageEncryption:
    """Production storage encryption is required."""

    def test_production_requires_encryption(self):
        result = validate_storage_encryption(
            Settings(
                app_env=AppEnvironment.PRODUCTION,
                database_url="postgresql://db:5432/bm",
                auth_issuer="https://login.microsoftonline.com/tenant/v2.0",
                auth_audience="api://boardmatch",
                azure_openai_endpoint="https://openai.azure.com",
                azure_openai_api_key="secret-key",
                azure_openai_deployment="gpt-4",
                azure_storage_account="boardmatchstorage",
            )
        )
        assert result is True

    def test_local_does_not_require_encryption(self):
        settings = Settings(app_env=AppEnvironment.LOCAL)
        assert validate_storage_encryption(settings) is True

    def test_config_production_storage_encryption_validation(self):
        with pytest.raises(ValueError, match="AZURE_STORAGE_ACCOUNT"):
            Settings(
                app_env=AppEnvironment.PRODUCTION,
                database_url="postgresql://db:5432/bm",
                auth_issuer="https://login.microsoftonline.com/tenant/v2.0",
                auth_audience="api://boardmatch",
                azure_openai_endpoint="https://openai.azure.com",
                azure_openai_api_key="secret-key",
                azure_openai_deployment="gpt-4",
                azure_storage_account=None,
                storage_encryption_required=True,
            )


class TestExportAfterDeletion:
    """Export after deletion request returns appropriate response."""

    def test_export_after_full_deletion(self):
        doc_repo = InMemoryDocumentRepository()
        result = export_after_deletion("user-1", doc_repo)
        assert result["user_id"] == "user-1"
        assert result["status"] == "deletion_completed"
        assert result["remaining_documents"] == 0

    def test_export_shows_remaining_if_partial(self):
        doc_repo = InMemoryDocumentRepository()
        doc_repo.save(
            Document(
                id="doc-1",
                user_id="user-1",
                filename="remaining.pdf",
                content_type="application/pdf",
                size_bytes=512,
                content_hash="hash",
                storage_path="docs/remaining.pdf",
            )
        )
        result = export_after_deletion("user-1", doc_repo)
        assert result["remaining_documents"] == 1

    def test_delete_all_user_data(self):
        doc_repo = InMemoryDocumentRepository()
        text_repo = InMemoryExtractedTextRepository()
        storage = LocalStorageBackend()
        storage.save("docs/a.pdf", b"content a")
        storage.save("docs/b.pdf", b"content b")
        doc_repo.save(
            Document(
                id="doc-a",
                user_id="user-1",
                filename="a.pdf",
                content_type="application/pdf",
                size_bytes=100,
                content_hash="h1",
                storage_path="docs/a.pdf",
            )
        )
        doc_repo.save(
            Document(
                id="doc-b",
                user_id="user-1",
                filename="b.pdf",
                content_type="application/pdf",
                size_bytes=200,
                content_hash="h2",
                storage_path="docs/b.pdf",
            )
        )
        text_repo.save("doc-a", "CV text A", "user-1")
        text_repo.save("doc-b", "CV text B", "user-1")
        service = RetentionService(
            document_repo=doc_repo,
            storage_backend=storage,
            extracted_text_repo=text_repo,
        )
        result = service.delete_all_user_data("user-1")
        assert result.documents_deleted == 2
        assert result.extracted_texts_deleted == 2
        assert doc_repo.list_by_user("user-1") == []
        assert text_repo.list_by_user("user-1") == []


class TestExtractedTextRetention:
    """Extracted CV text has a defined retention period."""

    def test_text_saved_with_timestamp(self):
        repo = InMemoryExtractedTextRepository()
        repo.save("doc-1", "Skills: governance, finance", "user-1")
        created = repo.get_creation_time("doc-1")
        assert created is not None
        assert (datetime.now(timezone.utc) - created).total_seconds() < 5

    def test_text_retrievable_within_retention(self):
        repo = InMemoryExtractedTextRepository()
        repo.save("doc-1", "Some CV content", "user-1")
        text = repo.get("doc-1")
        assert text == "Some CV content"

    def test_text_deleted_after_expiry(self):
        repo = InMemoryExtractedTextRepository()
        policy = RetentionPolicy(extracted_text_retention_days=7)
        repo.save("doc-1", "Old text", "user-1")
        repo._store["doc-1"].created_at = datetime.now(timezone.utc) - timedelta(
            days=14
        )
        service = RetentionService(policy=policy, extracted_text_repo=repo)
        deleted = service.cleanup_expired_texts("user-1")
        assert deleted == 1
        assert repo.get("doc-1") is None
