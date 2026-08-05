"""v1 Integration routes for OAuth consent management."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from boardmatch.auth import CurrentUser, get_required_user
from boardmatch.integrations import (
    AuditEvent,
    AuditEventType,
    InMemoryIntegrationRepository,
    Integration,
    IntegrationRepository,
    IntegrationStatus,
    generate_state_token,
    hash_token,
)

router = APIRouter(prefix="/integrations", tags=["integrations"])

# Module-level repository instance (replaced via dependency override in tests)
_repository: IntegrationRepository = InMemoryIntegrationRepository()

# In-memory state store for OAuth CSRF protection
_pending_states: dict[str, str] = {}  # state_token -> user_id

# Microsoft OAuth configuration (would come from settings in production)
_MS_AUTH_BASE = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
_MS_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
_MS_CLIENT_ID = "placeholder-client-id"
_MS_REDIRECT_URI = "http://localhost:8000/api/v1/integrations/microsoft/callback"
_MS_DEFAULT_SCOPES = ["User.Read", "Calendars.Read", "Mail.Read"]


def get_repository() -> IntegrationRepository:
    """Dependency for the integration repository."""
    return _repository


# --- Response Models ---


class IntegrationResponse(BaseModel):
    user_id: str
    provider: str
    status: str
    scopes: list[str]
    granted_at: datetime
    revoked_at: datetime | None = None


class IntegrationListResponse(BaseModel):
    integrations: list[IntegrationResponse]


class AuthorizeResponse(BaseModel):
    authorize_url: str
    state: str


class CallbackResponse(BaseModel):
    status: str
    provider: str
    scopes: list[str]


class RevokeResponse(BaseModel):
    status: str
    provider: str
    revoked_at: datetime


# --- Routes ---


@router.get("", response_model=IntegrationListResponse)
def list_integrations(
    user: CurrentUser = Depends(get_required_user),
    repo: IntegrationRepository = Depends(get_repository),
) -> IntegrationListResponse:
    """List the current user's integrations."""
    integrations = repo.list_by_user(user.user_id)
    return IntegrationListResponse(
        integrations=[
            IntegrationResponse(
                user_id=i.user_id,
                provider=i.provider,
                status=i.status,
                scopes=i.scopes,
                granted_at=i.granted_at,
                revoked_at=i.revoked_at,
            )
            for i in integrations
        ]
    )


@router.post("/microsoft/authorize", response_model=AuthorizeResponse)
def authorize_microsoft(
    user: CurrentUser = Depends(get_required_user),
    repo: IntegrationRepository = Depends(get_repository),
) -> AuthorizeResponse:
    """Initiate Microsoft OAuth consent flow. Returns the authorization URL."""
    state = generate_state_token()
    _pending_states[state] = user.user_id

    params = {
        "client_id": _MS_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": _MS_REDIRECT_URI,
        "scope": " ".join(_MS_DEFAULT_SCOPES),
        "state": state,
        "response_mode": "query",
    }
    authorize_url = f"{_MS_AUTH_BASE}?{urlencode(params)}"
    return AuthorizeResponse(authorize_url=authorize_url, state=state)


@router.get("/microsoft/callback", response_model=CallbackResponse)
def callback_microsoft(
    code: str,
    state: str,
    repo: IntegrationRepository = Depends(get_repository),
) -> CallbackResponse:
    """Handle OAuth callback from Microsoft. Exchanges code for token and stores consent."""
    user_id = _pending_states.pop(state, None)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired state parameter",
        )

    # In production, exchange `code` for tokens via _MS_TOKEN_URL.
    # Here we simulate a successful token exchange.
    simulated_token = f"access_token_for_{code}"

    integration = Integration(
        user_id=user_id,
        provider="microsoft",
        status=IntegrationStatus.ACTIVE,
        scopes=_MS_DEFAULT_SCOPES,
        granted_at=datetime.now(timezone.utc),
        token_hash=hash_token(simulated_token),
    )
    repo.save(integration)

    repo.add_audit_event(
        AuditEvent(
            user_id=user_id,
            provider="microsoft",
            event_type=AuditEventType.CONSENT_GRANTED,
            scopes=_MS_DEFAULT_SCOPES,
        )
    )

    return CallbackResponse(
        status="active",
        provider="microsoft",
        scopes=_MS_DEFAULT_SCOPES,
    )


@router.delete("/microsoft", response_model=RevokeResponse)
def revoke_microsoft(
    user: CurrentUser = Depends(get_required_user),
    repo: IntegrationRepository = Depends(get_repository),
) -> RevokeResponse:
    """Revoke the Microsoft integration for the current user."""
    integration = repo.get(user.user_id, "microsoft")
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Microsoft integration found",
        )
    if integration.status == IntegrationStatus.REVOKED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Integration already revoked",
        )

    revoked_at = datetime.now(timezone.utc)
    revoked = integration.model_copy(
        update={
            "status": IntegrationStatus.REVOKED,
            "revoked_at": revoked_at,
            "token_hash": None,
        }
    )
    repo.save(revoked)

    repo.add_audit_event(
        AuditEvent(
            user_id=user.user_id,
            provider="microsoft",
            event_type=AuditEventType.CONSENT_REVOKED,
            scopes=integration.scopes,
        )
    )

    return RevokeResponse(
        status="revoked",
        provider="microsoft",
        revoked_at=revoked_at,
    )
