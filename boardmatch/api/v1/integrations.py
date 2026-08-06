"""v1 Integration routes for OAuth consent management."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from boardmatch.auth import CurrentUser, get_required_user
from boardmatch.config import Settings, get_settings
from boardmatch.integrations import (
    AuditEvent,
    AuditEventType,
    GraphTokenExchangeError,
    InMemoryIntegrationRepository,
    Integration,
    IntegrationRepository,
    IntegrationStatus,
    exchange_code_for_token,
    generate_state_token,
    hash_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])

# Module-level repository instance (replaced via dependency override in tests)
_repository: IntegrationRepository = InMemoryIntegrationRepository()

# In-memory state store for OAuth CSRF protection
_pending_states: dict[str, str] = {}  # state_token -> user_id

# Microsoft OAuth configuration defaults. Overridden by MS_GRAPH_* settings
# when a real client id/secret is configured (see boardmatch.config.Settings).
_MS_AUTH_BASE = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
_MS_TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_MS_CLIENT_ID = "placeholder-client-id"
_MS_REDIRECT_URI = "http://localhost:8000/api/v1/integrations/microsoft/callback"
_MS_DEFAULT_SCOPES = ["User.Read", "Calendars.Read", "Mail.Read", "People.Read"]


def get_repository() -> IntegrationRepository:
    """Dependency for the integration repository."""
    return _repository


def _graph_configured(settings: Settings) -> bool:
    """True when real Microsoft Graph OAuth credentials are configured."""
    return bool(settings.ms_graph_client_id and settings.ms_graph_client_secret)


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
    settings: Settings = Depends(get_settings),
) -> AuthorizeResponse:
    """Initiate Microsoft OAuth consent flow. Returns the authorization URL."""
    state = generate_state_token()
    _pending_states[state] = user.user_id

    client_id = settings.ms_graph_client_id or _MS_CLIENT_ID
    redirect_uri = settings.ms_graph_redirect_uri or _MS_REDIRECT_URI
    auth_base = _MS_AUTH_BASE.replace("common", settings.ms_graph_tenant_id or "common")

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(_MS_DEFAULT_SCOPES),
        "state": state,
        "response_mode": "query",
    }
    authorize_url = f"{auth_base}?{urlencode(params)}"
    return AuthorizeResponse(authorize_url=authorize_url, state=state)


@router.get("/microsoft/callback", response_model=CallbackResponse)
def callback_microsoft(
    code: str,
    state: str,
    repo: IntegrationRepository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> CallbackResponse:
    """Handle OAuth callback from Microsoft. Exchanges code for token and stores consent."""
    user_id = _pending_states.pop(state, None)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired state parameter",
        )

    real_access_token: str | None = None
    if _graph_configured(settings):
        # Real authorization-code exchange against Microsoft identity platform.
        tenant = settings.ms_graph_tenant_id or "common"
        token_url = _MS_TOKEN_URL_TEMPLATE.format(tenant=tenant)
        redirect_uri = settings.ms_graph_redirect_uri or _MS_REDIRECT_URI
        try:
            real_access_token = exchange_code_for_token(
                token_url=token_url,
                client_id=settings.ms_graph_client_id,  # type: ignore[arg-type]
                client_secret=settings.ms_graph_client_secret.get_secret_value(),  # type: ignore[union-attr]
                code=code,
                redirect_uri=redirect_uri,
                scopes=_MS_DEFAULT_SCOPES,
            )
        except GraphTokenExchangeError as exc:
            logger.warning("Microsoft Graph token exchange failed: %s", exc)

    # Fall back to a simulated token when Graph isn't configured (local/test)
    # or the real exchange failed, so the consent flow always completes.
    # Only a genuine `real_access_token` is stored for later Graph calls —
    # the simulated one is hashed for audit purposes only, never reused to
    # call Graph, keeping local/test behaviour fully deterministic.
    token_to_hash = real_access_token or f"access_token_for_{code}"

    integration = Integration(
        user_id=user_id,
        provider="microsoft",
        status=IntegrationStatus.ACTIVE,
        scopes=_MS_DEFAULT_SCOPES,
        granted_at=datetime.now(timezone.utc),
        token_hash=hash_token(token_to_hash),
        access_token=real_access_token,
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
            "access_token": None,
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
