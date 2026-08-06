"""v1 Account routes - data export, deletion, and audit events."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from boardmatch.audit import AuditAction, AuditLogger
from boardmatch.auth import CurrentUser, get_required_user
from boardmatch.config import get_settings
from boardmatch.drafts import InMemoryDraftRepository
from boardmatch.infrastructure.repositories.factory import create_repositories
from boardmatch.infrastructure.repositories.memory import (
    InMemoryApplicationRepository,
    InMemoryCandidateRepository,
)
from boardmatch.integrations import (
    AuditEvent as IntegrationAuditEvent,
    AuditEventType,
    InMemoryIntegrationRepository,
    IntegrationStatus,
)

router = APIRouter(prefix="/api/v1/account", tags=["account"])

_audit_logger = AuditLogger()
_repos = create_repositories(get_settings())
_candidate_repo = _repos.candidate_repo
_application_repo = _repos.application_repo
_draft_repo = InMemoryDraftRepository()
_integration_repo = InMemoryIntegrationRepository()


def get_audit_logger() -> AuditLogger:
    return _audit_logger


def get_candidate_repo() -> InMemoryCandidateRepository:
    return _candidate_repo


def get_application_repo() -> InMemoryApplicationRepository:
    return _application_repo


def get_draft_repo() -> InMemoryDraftRepository:
    return _draft_repo


def get_integration_repo() -> InMemoryIntegrationRepository:
    return _integration_repo


class AuditEventResponse(BaseModel):
    id: str
    user_id: str
    action: str
    resource_type: str | None = None
    resource_id: str | None = None
    timestamp: datetime
    details: dict | None = None


class AuditEventListResponse(BaseModel):
    events: list[AuditEventResponse]


class ExportResponse(BaseModel):
    exported_at: datetime
    user_id: str
    profile: dict | None = None
    applications: list[dict]
    drafts: list[dict]
    consents: list[dict]


class AccountDeletedResponse(BaseModel):
    status: str
    deleted_at: datetime


@router.get("/audit-events", response_model=AuditEventListResponse)
def list_audit_events(
    user: CurrentUser = Depends(get_required_user),
    audit: AuditLogger = Depends(get_audit_logger),
) -> AuditEventListResponse:
    """List audit events for the authenticated user."""
    events = audit.get_events(user.user_id)
    return AuditEventListResponse(
        events=[
            AuditEventResponse(
                id=e.id,
                user_id=e.user_id,
                action=e.action,
                resource_type=e.resource_type,
                resource_id=e.resource_id,
                timestamp=e.timestamp,
                details=e.details,
            )
            for e in events
        ]
    )


@router.post("/export", response_model=ExportResponse)
def request_export(
    user: CurrentUser = Depends(get_required_user),
    audit: AuditLogger = Depends(get_audit_logger),
    candidate_repo: InMemoryCandidateRepository = Depends(get_candidate_repo),
    app_repo: InMemoryApplicationRepository = Depends(get_application_repo),
    draft_repo: InMemoryDraftRepository = Depends(get_draft_repo),
    integration_repo: InMemoryIntegrationRepository = Depends(get_integration_repo),
) -> ExportResponse:
    """Export all user data (profile, applications, drafts, consents)."""
    candidate = candidate_repo.get_for_user(user.user_id)
    profile_data: dict | None = None
    if candidate is not None:
        profile_data = {
            "name": candidate.name,
            "headline": candidate.headline,
            "years_experience": candidate.years_experience,
            "skills": candidate.skills,
            "sectors": candidate.sectors,
            "credentials": candidate.credentials,
            "board_experience": candidate.board_experience,
            "achievements": candidate.achievements,
            "locations": candidate.locations,
        }

    applications = app_repo.list_for_user(user.user_id)
    applications_data = [
        {
            "id": a.id,
            "opportunity_id": a.opportunity_id,
            "stage": a.stage.value,
            "notes": a.notes,
        }
        for a in applications
    ]

    drafts = draft_repo.list_for_user(user.user_id)
    drafts_data = [
        {
            "id": d.id,
            "draft_type": d.draft_type,
            "content": d.content,
            "engine": d.engine,
            "created_at": d.created_at.isoformat(),
        }
        for d in drafts
    ]

    integrations = integration_repo.list_by_user(user.user_id)
    consents_data = [
        {
            "provider": i.provider,
            "status": i.status,
            "scopes": i.scopes,
            "granted_at": i.granted_at.isoformat(),
            "revoked_at": i.revoked_at.isoformat() if i.revoked_at else None,
        }
        for i in integrations
    ]

    audit.log(
        user_id=user.user_id,
        action=AuditAction.EXPORT_REQUESTED,
        resource_type="account",
        details={"sections": ["profile", "applications", "drafts", "consents"]},
    )

    return ExportResponse(
        exported_at=datetime.now(timezone.utc),
        user_id=user.user_id,
        profile=profile_data,
        applications=applications_data,
        drafts=drafts_data,
        consents=consents_data,
    )


@router.delete("", response_model=AccountDeletedResponse)
def delete_account(
    user: CurrentUser = Depends(get_required_user),
    audit: AuditLogger = Depends(get_audit_logger),
    candidate_repo: InMemoryCandidateRepository = Depends(get_candidate_repo),
    app_repo: InMemoryApplicationRepository = Depends(get_application_repo),
    draft_repo: InMemoryDraftRepository = Depends(get_draft_repo),
    integration_repo: InMemoryIntegrationRepository = Depends(get_integration_repo),
) -> AccountDeletedResponse:
    """Delete account: revoke integrations and anonymise personal data."""
    integrations = integration_repo.list_by_user(user.user_id)
    for integration in integrations:
        if integration.status == IntegrationStatus.ACTIVE:
            revoked = integration.model_copy(
                update={
                    "status": IntegrationStatus.REVOKED,
                    "revoked_at": datetime.now(timezone.utc),
                    "token_hash": None,
                }
            )
            integration_repo.save(revoked)
            integration_repo.add_audit_event(
                IntegrationAuditEvent(
                    user_id=user.user_id,
                    provider=integration.provider,
                    event_type=AuditEventType.CONSENT_REVOKED,
                    scopes=integration.scopes,
                )
            )

    candidate_repo._store.pop(user.user_id, None)

    applications = app_repo.list_for_user(user.user_id)
    for application in applications:
        app_repo.delete(user.user_id, application.id)

    drafts = draft_repo.list_for_user(user.user_id)
    for draft in drafts:
        draft_repo.delete(draft.id, user.user_id)

    audit.log(
        user_id=user.user_id,
        action=AuditAction.ACCOUNT_DELETED,
        resource_type="account",
        details={"anonymised": True},
    )

    return AccountDeletedResponse(
        status="deleted",
        deleted_at=datetime.now(timezone.utc),
    )
