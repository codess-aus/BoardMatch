"""v1 Privacy routes - retention policies, data deletion, and export controls."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from boardmatch.auth import CurrentUser, get_required_user
from boardmatch.config import get_settings
from boardmatch.documents import DocumentRepository
from boardmatch.infrastructure.repositories.extended_factory import (
    create_extended_repositories,
)
from boardmatch.integrations import IntegrationRepository
from boardmatch.retention import (
    ExtractedTextRepository,
    NetworkRepository,
    RetentionPolicy,
    RetentionService,
    delete_network_data,
    revoke_integration_token,
)
from boardmatch.storage import LocalStorageBackend

router = APIRouter(prefix="/privacy", tags=["privacy"])

# Module-level instances (replaced via dependency override in tests)
_extended_repos = create_extended_repositories(get_settings())
_document_repo = _extended_repos.document_repo
_extracted_text_repo = _extended_repos.extracted_text_repo
_network_repo = _extended_repos.retention_network_repo
_storage_backend = LocalStorageBackend()
_integration_repo = _extended_repos.integration_repo


def get_document_repo() -> DocumentRepository:
    return _document_repo


def get_extracted_text_repo() -> ExtractedTextRepository:
    return _extracted_text_repo


def get_network_repo() -> NetworkRepository:
    return _network_repo


def get_storage_backend() -> LocalStorageBackend:
    return _storage_backend


def get_integration_repo() -> IntegrationRepository:
    return _integration_repo


# --- Response Models ---


class RetentionPolicyResponse(BaseModel):
    category: str
    retention_days: int


class RetentionPoliciesResponse(BaseModel):
    policies: list[RetentionPolicyResponse]


class CleanupResultResponse(BaseModel):
    documents_deleted: int
    texts_deleted: int


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
) -> RetentionPoliciesResponse:
    """List all configured data retention policies."""
    settings = get_settings()
    return RetentionPoliciesResponse(
        policies=[
            RetentionPolicyResponse(
                category="documents", retention_days=settings.document_retention_days
            ),
            RetentionPolicyResponse(
                category="extracted_text",
                retention_days=settings.extracted_text_retention_days,
            ),
            RetentionPolicyResponse(
                category="audit_logs", retention_days=settings.audit_log_retention_days
            ),
        ]
    )


@router.post("/cleanup", response_model=CleanupResultResponse)
def run_cleanup(
    user: CurrentUser = Depends(get_required_user),
    doc_repo: DocumentRepository = Depends(get_document_repo),
    text_repo: ExtractedTextRepository = Depends(get_extracted_text_repo),
    storage: LocalStorageBackend = Depends(get_storage_backend),
) -> CleanupResultResponse:
    """Run retention cleanup for the current user."""
    settings = get_settings()
    policy = RetentionPolicy(
        document_retention_days=settings.document_retention_days,
        extracted_text_retention_days=settings.extracted_text_retention_days,
    )
    service = RetentionService(
        policy=policy,
        document_repo=doc_repo,
        extracted_text_repo=text_repo,
        storage_backend=storage,
    )
    result = service.run_cleanup(user.user_id)
    return CleanupResultResponse(
        documents_deleted=result.documents_deleted,
        texts_deleted=result.texts_deleted,
    )


@router.delete("/network-data", response_model=NetworkDeletionResponse)
def delete_user_network_data(
    user: CurrentUser = Depends(get_required_user),
    network_repo: NetworkRepository = Depends(get_network_repo),
) -> NetworkDeletionResponse:
    """Delete all network/connection data for the current user."""
    deleted = delete_network_data(user.user_id, network_repo)
    return NetworkDeletionResponse(
        status="deleted",
        records_deleted=deleted,
        deleted_at=datetime.now(timezone.utc),
    )


@router.post("/revoke-token/{provider}", response_model=TokenRevocationResponse)
def revoke_token(
    provider: str,
    user: CurrentUser = Depends(get_required_user),
    integration_repo: IntegrationRepository = Depends(get_integration_repo),
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
    doc_repo: DocumentRepository = Depends(get_document_repo),
    text_repo: ExtractedTextRepository = Depends(get_extracted_text_repo),
    network_repo: NetworkRepository = Depends(get_network_repo),
    storage: LocalStorageBackend = Depends(get_storage_backend),
    integration_repo: IntegrationRepository = Depends(get_integration_repo),
) -> DeletionRequestResponse:
    """Delete all user data (GDPR right to erasure)."""
    settings = get_settings()
    policy = RetentionPolicy(
        document_retention_days=settings.document_retention_days,
        extracted_text_retention_days=settings.extracted_text_retention_days,
    )
    service = RetentionService(
        policy=policy,
        document_repo=doc_repo,
        extracted_text_repo=text_repo,
        storage_backend=storage,
    )
    result = service.delete_all_user_data(user.user_id)

    # Delete network data
    network_deleted = delete_network_data(user.user_id, network_repo)

    # Revoke all tokens
    tokens_revoked = 0
    integrations = integration_repo.list_by_user(user.user_id)
    for integration in integrations:
        if revoke_integration_token(
            user.user_id, integration.provider, integration_repo
        ):
            tokens_revoked += 1

    return DeletionRequestResponse(
        status="deleted",
        user_id=user.user_id,
        documents_deleted=result.documents_deleted,
        extracted_text_deleted=result.texts_deleted,
        network_records_deleted=network_deleted,
        tokens_revoked=tokens_revoked,
        requested_at=datetime.now(timezone.utc),
    )
