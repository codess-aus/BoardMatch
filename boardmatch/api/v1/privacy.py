"""v1 Privacy routes - retention policies, data deletion, and export controls."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from boardmatch.auth import CurrentUser, get_required_user
from boardmatch.retention import (
    CleanupResult,
    InMemoryExtractedTextRepository,
    InMemoryNetworkDataRepository,
    RetentionCategory,
    RetentionConfig,
    RetentionManager,
    RetentionPolicy,
    revoke_integration_token,
)
from boardmatch.documents import InMemoryDocumentRepository
from boardmatch.integrations import InMemoryIntegrationRepository
from boardmatch.storage import LocalStorageBackend

router = APIRouter(prefix="/privacy", tags=["privacy"])

# Module-level instances (replaced via dependency override in tests)
_retention_config = RetentionConfig()
_document_repo = InMemoryDocumentRepository()
_extracted_text_repo = InMemoryExtractedTextRepository()
_network_repo = InMemoryNetworkDataRepository()
_storage_backend = LocalStorageBackend()
_integration_repo = InMemoryIntegrationRepository()


def get_retention_config() -> RetentionConfig:
    return _retention_config


def get_document_repo() -> InMemoryDocumentRepository:
    return _document_repo


def get_extracted_text_repo() -> InMemoryExtractedTextRepository:
    return _extracted_text_repo


def get_network_repo() -> InMemoryNetworkDataRepository:
    return _network_repo


def get_storage_backend() -> LocalStorageBackend:
    return _storage_backend


def get_integration_repo() -> InMemoryIntegrationRepository:
    return _integration_repo


# --- Response Models ---


class RetentionPolicyResponse(BaseModel):
    category: str
    retention_days: int
    description: str


class RetentionPoliciesResponse(BaseModel):
    policies: list[RetentionPolicyResponse]


class CleanupResultResponse(BaseModel):
    category: str
    items_deleted: int
    cutoff_date: datetime


class CleanupResponse(BaseModel):
    results: list[CleanupResultResponse]


class NetworkDeletionResponse(BaseModel):
    status: str
    records_deleted: int
    deleted_at: datetime


class TokenRevocationResponse(BaseModel):
    status: str
    provider: str
    revoked_at: datetime


class DeletionRequestResponse(BaseModel):
    status: str
    user_id: str
    documents_deleted: int
    extracted_text_deleted: int
    network_records_deleted: int
    tokens_revoked: int
    requested_at: datetime


# --- Routes ---


@router.get("/retention-policies", response_model=RetentionPoliciesResponse)
def list_retention_policies(
    user: CurrentUser = Depends(get_required_user),
    config: RetentionConfig = Depends(get_retention_config),
) -> RetentionPoliciesResponse:
    """List all configured data retention policies."""
    return RetentionPoliciesResponse(
        policies=[
            RetentionPolicyResponse(
                category=p.category.value,
                retention_days=p.retention_days,
                description=p.description,
            )
            for p in config.policies
        ]
    )


@router.post("/cleanup", response_model=CleanupResponse)
def run_cleanup(
    user: CurrentUser = Depends(get_required_user),
    config: RetentionConfig = Depends(get_retention_config),
    doc_repo: InMemoryDocumentRepository = Depends(get_document_repo),
    text_repo: InMemoryExtractedTextRepository = Depends(get_extracted_text_repo),
    storage: LocalStorageBackend = Depends(get_storage_backend),
) -> CleanupResponse:
    """Run retention cleanup (admin-only in production)."""
    manager = RetentionManager(
        config=config,
        document_repo=doc_repo,
        extracted_text_repo=text_repo,
        storage_backend=storage,
    )
    results = manager.run_all_cleanups()
    return CleanupResponse(
        results=[
            CleanupResultResponse(
                category=r.category.value,
                items_deleted=r.items_deleted,
                cutoff_date=r.cutoff_date,
            )
            for r in results
        ]
    )


@router.delete("/network-data", response_model=NetworkDeletionResponse)
def delete_network_data(
    user: CurrentUser = Depends(get_required_user),
    config: RetentionConfig = Depends(get_retention_config),
    network_repo: InMemoryNetworkDataRepository = Depends(get_network_repo),
) -> NetworkDeletionResponse:
    """Delete all network/connection data for the current user."""
    manager = RetentionManager(
        config=config,
        network_repo=network_repo,
    )
    deleted = manager.delete_network_data(user.user_id)
    return NetworkDeletionResponse(
        status="deleted",
        records_deleted=deleted,
        deleted_at=datetime.now(timezone.utc),
    )


@router.post("/revoke-token/{provider}", response_model=TokenRevocationResponse)
def revoke_token(
    provider: str,
    user: CurrentUser = Depends(get_required_user),
    integration_repo: InMemoryIntegrationRepository = Depends(get_integration_repo),
) -> TokenRevocationResponse:
    """Revoke an integration token for the given provider."""
    success = revoke_integration_token(user.user_id, provider, integration_repo)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active integration found for provider: {provider}",
        )
    return TokenRevocationResponse(
        status="revoked",
        provider=provider,
        revoked_at=datetime.now(timezone.utc),
    )


@router.delete("/all-data", response_model=DeletionRequestResponse)
def delete_all_user_data(
    user: CurrentUser = Depends(get_required_user),
    config: RetentionConfig = Depends(get_retention_config),
    doc_repo: InMemoryDocumentRepository = Depends(get_document_repo),
    text_repo: InMemoryExtractedTextRepository = Depends(get_extracted_text_repo),
    network_repo: InMemoryNetworkDataRepository = Depends(get_network_repo),
    storage: LocalStorageBackend = Depends(get_storage_backend),
    integration_repo: InMemoryIntegrationRepository = Depends(get_integration_repo),
) -> DeletionRequestResponse:
    """Delete all user data (GDPR right to erasure / right to be forgotten)."""
    # Delete documents
    docs = doc_repo.list_by_user(user.user_id)
    for doc in docs:
        try:
            storage.delete(doc.storage_path)
        except IOError:
            pass
        doc_repo.delete(doc.id)

    # Delete extracted text
    texts = text_repo.list_by_user(user.user_id)
    for t in texts:
        text_repo.delete(t.id)

    # Delete network data
    network_deleted = network_repo.delete_by_user(user.user_id)

    # Revoke all tokens
    tokens_revoked = 0
    integrations = integration_repo.list_by_user(user.user_id)
    for integration in integrations:
        if revoke_integration_token(user.user_id, integration.provider, integration_repo):
            tokens_revoked += 1

    return DeletionRequestResponse(
        status="deleted",
        user_id=user.user_id,
        documents_deleted=len(docs),
        extracted_text_deleted=len(texts),
        network_records_deleted=network_deleted,
        tokens_revoked=tokens_revoked,
        requested_at=datetime.now(timezone.utc),
    )
