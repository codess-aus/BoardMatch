"""Abstract file storage for BoardMatch.

Provides a pluggable storage layer via the StorageBackend protocol.
LocalStorageBackend is provided for development/testing.
AzureBlobStorageBackend is used in production when AZURE_STORAGE_ACCOUNT is
configured (see boardmatch.config.Settings).
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# The Azure SDKs are optional at import time so that environments without
# them installed can still use LocalStorageBackend. They are declared as
# module-level names (rather than imported lazily inside functions) so tests
# can patch them with unittest.mock.patch("boardmatch.storage.<name>", ...).
try:  # pragma: no cover - exercised indirectly via AzureBlobStorageBackend tests
    from azure.core.exceptions import ResourceNotFoundError
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient
except ImportError:  # pragma: no cover - azure-storage-blob not installed
    ResourceNotFoundError = None  # type: ignore[assignment,misc]
    DefaultAzureCredential = None  # type: ignore[assignment,misc]
    BlobServiceClient = None  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    from boardmatch.config import Settings


@runtime_checkable
class StorageBackend(Protocol):
    """Protocol that any file storage backend must satisfy."""

    def save(self, path: str, data: bytes) -> None:
        """Save data to the given path. Raises IOError on failure."""
        ...

    def delete(self, path: str) -> None:
        """Delete the file at the given path. Raises IOError on failure."""
        ...

    def exists(self, path: str) -> bool:
        """Return True if a file exists at the given path."""
        ...


class LocalStorageBackend:
    """Local filesystem storage backend for development.

    Stores files in a temporary directory that is cleaned up
    when the process exits.
    """

    def __init__(self, base_dir: str | None = None) -> None:
        if base_dir is None:
            self._base_dir = Path(tempfile.mkdtemp(prefix="boardmatch_storage_"))
        else:
            self._base_dir = Path(base_dir)
            self._base_dir.mkdir(parents=True, exist_ok=True)

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def save(self, path: str, data: bytes) -> None:
        full_path = self._base_dir / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(data)

    def delete(self, path: str) -> None:
        full_path = self._base_dir / path
        if full_path.exists():
            full_path.unlink()
        else:
            raise OSError(f"File not found: {path}")

    def exists(self, path: str) -> bool:
        return (self._base_dir / path).exists()


class AzureBlobStorageBackend:
    """Azure Blob Storage backend for production file storage.

    Authenticates using ``azure-identity``'s ``DefaultAzureCredential``, which
    uses a managed identity when running in Azure (App Service, AKS, Container
    Apps, etc.) and transparently falls back to ``az login`` CLI credentials
    or environment-variable-based service principal credentials for local
    development — no secrets are handled by this class directly.

    Encryption at rest: Azure Storage encrypts all data with Storage Service
    Encryption (SSE) by default using Microsoft-managed keys, satisfying the
    ``STORAGE_ENCRYPTION_REQUIRED`` expectation with no extra configuration.
    For customer-managed keys, configure encryption scopes or a Key Vault key
    on the storage account itself; this backend does not manage key material.

    Retries/timeouts: the Azure SDK's built-in retry policy is configured via
    ``retry_total`` (exponential backoff across transient failures) plus
    explicit connection/read timeouts so calls fail fast instead of hanging.
    """

    def __init__(
        self,
        account_url: str | None = None,
        *,
        account_name: str | None = None,
        container_name: str = "documents",
        credential: object | None = None,
        connection_timeout: int = 10,
        read_timeout: int = 60,
        retry_total: int = 3,
    ) -> None:
        if BlobServiceClient is None:  # pragma: no cover - import guard
            raise ImportError(
                "azure-storage-blob and azure-identity are required for "
                "AzureBlobStorageBackend. They are listed in requirements.txt."
            )

        if account_url is None:
            if not account_name:
                raise ValueError("Either account_url or account_name must be provided")
            account_url = f"https://{account_name}.blob.core.windows.net"

        self._credential = credential or DefaultAzureCredential()
        self._client = BlobServiceClient(
            account_url=account_url,
            credential=self._credential,
            connection_timeout=connection_timeout,
            read_timeout=read_timeout,
            retry_total=retry_total,
        )
        self._container_name = container_name
        self._container_client = self._client.get_container_client(container_name)
        self._ensure_container()

    def _ensure_container(self) -> None:
        """Best-effort container creation; ignored if it already exists."""
        try:
            self._container_client.create_container()
        except Exception:  # noqa: BLE001 - container may already exist
            logger.debug(
                "Container '%s' already exists or could not be created eagerly",
                self._container_name,
            )

    def save(self, path: str, data: bytes) -> None:
        try:
            self._container_client.upload_blob(name=path, data=data, overwrite=True)
        except Exception as exc:
            raise OSError(f"Failed to save blob '{path}': {exc}") from exc

    def delete(self, path: str) -> None:
        try:
            self._container_client.delete_blob(path)
        except Exception as exc:
            if ResourceNotFoundError is not None and isinstance(
                exc, ResourceNotFoundError
            ):
                raise OSError(f"File not found: {path}") from exc
            raise OSError(f"Failed to delete blob '{path}': {exc}") from exc

    def exists(self, path: str) -> bool:
        blob_client = self._container_client.get_blob_client(path)
        return bool(blob_client.exists())


def create_storage_backend(settings: Settings) -> StorageBackend:
    """Select a storage backend based on application settings.

    Uses Azure Blob Storage when ``AZURE_STORAGE_ACCOUNT`` is configured
    (managed identity in production, CLI/env credentials locally), otherwise
    falls back to local filesystem storage for development/testing.
    """
    if settings.azure_storage_account:
        container_name = getattr(settings, "azure_storage_container", "documents")
        return AzureBlobStorageBackend(
            account_name=settings.azure_storage_account,
            container_name=container_name,
        )

    if settings.storage_encryption_required:
        logger.warning(
            "STORAGE_ENCRYPTION_REQUIRED is set but AZURE_STORAGE_ACCOUNT is "
            "not configured; falling back to unencrypted local storage. "
            "This is only acceptable for local development."
        )
    return LocalStorageBackend()
