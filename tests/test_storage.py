"""Tests for the storage backends (BM storage/AI integrations workstream).

AzureBlobStorageBackend tests mock the Azure SDK entirely — no real Azure
calls are made.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from boardmatch import storage as storage_module
from boardmatch.config import Settings
from boardmatch.storage import (
    AzureBlobStorageBackend,
    LocalStorageBackend,
    create_storage_backend,
)


class TestLocalStorageBackend:
    def test_save_and_exists(self, tmp_path):
        backend = LocalStorageBackend(str(tmp_path))
        backend.save("a/b.txt", b"hello")
        assert backend.exists("a/b.txt")

    def test_delete(self, tmp_path):
        backend = LocalStorageBackend(str(tmp_path))
        backend.save("a.txt", b"data")
        backend.delete("a.txt")
        assert not backend.exists("a.txt")

    def test_delete_missing_raises(self, tmp_path):
        backend = LocalStorageBackend(str(tmp_path))
        with pytest.raises(IOError):
            backend.delete("missing.txt")

    def test_exists_false_for_missing(self, tmp_path):
        backend = LocalStorageBackend(str(tmp_path))
        assert backend.exists("nope.txt") is False


@pytest.fixture
def mock_azure_sdk():
    """Patch the Azure SDK entry points used by AzureBlobStorageBackend."""
    mock_credential_cls = MagicMock()
    mock_blob_service_client_cls = MagicMock()
    mock_container_client = MagicMock()
    mock_blob_service_client_cls.return_value.get_container_client.return_value = (
        mock_container_client
    )

    with (
        patch.object(storage_module, "DefaultAzureCredential", mock_credential_cls),
        patch.object(storage_module, "BlobServiceClient", mock_blob_service_client_cls),
    ):
        yield {
            "credential_cls": mock_credential_cls,
            "client_cls": mock_blob_service_client_cls,
            "container_client": mock_container_client,
        }


class TestAzureBlobStorageBackend:
    def test_requires_account_url_or_name(self, mock_azure_sdk):
        with pytest.raises(ValueError):
            AzureBlobStorageBackend()

    def test_builds_account_url_from_account_name(self, mock_azure_sdk):
        AzureBlobStorageBackend(account_name="myacct", container_name="docs")
        _, kwargs = mock_azure_sdk["client_cls"].call_args
        assert kwargs["account_url"] == "https://myacct.blob.core.windows.net"

    def test_uses_default_azure_credential_when_none_supplied(self, mock_azure_sdk):
        AzureBlobStorageBackend(account_name="myacct")
        mock_azure_sdk["credential_cls"].assert_called_once()

    def test_custom_credential_is_used(self, mock_azure_sdk):
        custom_credential = MagicMock()
        AzureBlobStorageBackend(account_name="myacct", credential=custom_credential)
        mock_azure_sdk["credential_cls"].assert_not_called()
        _, kwargs = mock_azure_sdk["client_cls"].call_args
        assert kwargs["credential"] is custom_credential

    def test_configures_retry_and_timeouts(self, mock_azure_sdk):
        AzureBlobStorageBackend(
            account_name="myacct",
            connection_timeout=5,
            read_timeout=30,
            retry_total=7,
        )
        _, kwargs = mock_azure_sdk["client_cls"].call_args
        assert kwargs["connection_timeout"] == 5
        assert kwargs["read_timeout"] == 30
        assert kwargs["retry_total"] == 7

    def test_ensure_container_swallows_already_exists(self, mock_azure_sdk):
        mock_azure_sdk["container_client"].create_container.side_effect = Exception(
            "already exists"
        )
        # Should not raise even though create_container() fails.
        AzureBlobStorageBackend(account_name="myacct")

    def test_save_uploads_blob(self, mock_azure_sdk):
        backend = AzureBlobStorageBackend(account_name="myacct")
        backend.save("path/to/file.pdf", b"content")
        mock_azure_sdk["container_client"].upload_blob.assert_called_once_with(
            name="path/to/file.pdf", data=b"content", overwrite=True
        )

    def test_save_wraps_failures_as_ioerror(self, mock_azure_sdk):
        mock_azure_sdk["container_client"].upload_blob.side_effect = Exception("boom")
        backend = AzureBlobStorageBackend(account_name="myacct")
        with pytest.raises(IOError):
            backend.save("file.pdf", b"content")

    def test_delete_calls_delete_blob(self, mock_azure_sdk):
        backend = AzureBlobStorageBackend(account_name="myacct")
        backend.delete("file.pdf")
        mock_azure_sdk["container_client"].delete_blob.assert_called_once_with(
            "file.pdf"
        )

    def test_delete_wraps_failures_as_ioerror(self, mock_azure_sdk):
        mock_azure_sdk["container_client"].delete_blob.side_effect = Exception("boom")
        backend = AzureBlobStorageBackend(account_name="myacct")
        with pytest.raises(IOError):
            backend.delete("file.pdf")

    def test_exists_true(self, mock_azure_sdk):
        mock_blob_client = MagicMock()
        mock_blob_client.exists.return_value = True
        mock_azure_sdk[
            "container_client"
        ].get_blob_client.return_value = mock_blob_client
        backend = AzureBlobStorageBackend(account_name="myacct")
        assert backend.exists("file.pdf") is True

    def test_exists_false(self, mock_azure_sdk):
        mock_blob_client = MagicMock()
        mock_blob_client.exists.return_value = False
        mock_azure_sdk[
            "container_client"
        ].get_blob_client.return_value = mock_blob_client
        backend = AzureBlobStorageBackend(account_name="myacct")
        assert backend.exists("file.pdf") is False


class TestCreateStorageBackend:
    def test_local_when_no_account_configured(self):
        settings = Settings(azure_storage_account=None)
        backend = create_storage_backend(settings)
        assert isinstance(backend, LocalStorageBackend)

    def test_azure_when_account_configured(self, mock_azure_sdk):
        settings = Settings(azure_storage_account="myacct123")
        backend = create_storage_backend(settings)
        assert isinstance(backend, AzureBlobStorageBackend)

    def test_azure_uses_configured_container(self, mock_azure_sdk):
        settings = Settings(
            azure_storage_account="myacct123", azure_storage_container="cvs"
        )
        create_storage_backend(settings)
        mock_azure_sdk[
            "client_cls"
        ].return_value.get_container_client.assert_called_with("cvs")
