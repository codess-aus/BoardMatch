"""Consent and integration records for external providers (e.g. Microsoft Graph).

Provides models, repository protocol, in-memory implementation, and audit event
tracking for OAuth-based integrations.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol, runtime_checkable

import requests
from pydantic import BaseModel, Field, SecretStr

from .resilience import CircuitBreaker, with_resilience

logger = logging.getLogger(__name__)

GRAPH_API_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_HTTP_TIMEOUT_SECONDS = 15.0

# Circuit breaker shared across all Microsoft Graph calls made by this
# process, so an outage doesn't add retry latency to every request.
_GRAPH_BREAKER = CircuitBreaker(
    name="microsoft-graph", failure_threshold=5, recovery_timeout=30.0
)


class IntegrationStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class AuditEventType(StrEnum):
    CONSENT_GRANTED = "consent_granted"
    CONSENT_REVOKED = "consent_revoked"


class Integration(BaseModel):
    """A user's integration with an external provider."""

    user_id: str
    provider: str
    status: IntegrationStatus = IntegrationStatus.ACTIVE
    scopes: list[str] = Field(default_factory=list)
    granted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    revoked_at: datetime | None = None
    # Token stored as a hashed placeholder — never plain text
    token_hash: str | None = None
    # The live access token, held only in memory for the lifetime of this
    # process so real Microsoft Graph calls (e.g. network sync) can be made
    # on the user's behalf. Never included in API responses or audit events.
    # None when running in simulated/local mode (no real token exchange).
    access_token: SecretStr | None = Field(default=None, exclude=True, repr=False)


class AuditEvent(BaseModel):
    """Audit log entry for integration state changes."""

    user_id: str
    provider: str
    event_type: AuditEventType
    scopes: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def hash_token(token: str) -> str:
    """One-way hash a token for secure storage."""
    return hashlib.sha256(token.encode()).hexdigest()


@runtime_checkable
class IntegrationRepository(Protocol):
    """Protocol for integration persistence."""

    def list_by_user(self, user_id: str) -> list[Integration]: ...

    def get(self, user_id: str, provider: str) -> Integration | None: ...

    def save(self, integration: Integration) -> None: ...

    def get_audit_events(self, user_id: str) -> list[AuditEvent]: ...

    def add_audit_event(self, event: AuditEvent) -> None: ...


class InMemoryIntegrationRepository:
    """In-memory implementation of IntegrationRepository for testing/dev."""

    def __init__(self) -> None:
        self._integrations: dict[tuple[str, str], Integration] = {}
        self._audit_events: list[AuditEvent] = []

    def list_by_user(self, user_id: str) -> list[Integration]:
        return [i for i in self._integrations.values() if i.user_id == user_id]

    def get(self, user_id: str, provider: str) -> Integration | None:
        return self._integrations.get((user_id, provider))

    def save(self, integration: Integration) -> None:
        self._integrations[(integration.user_id, integration.provider)] = integration

    def get_audit_events(self, user_id: str) -> list[AuditEvent]:
        return [e for e in self._audit_events if e.user_id == user_id]

    def add_audit_event(self, event: AuditEvent) -> None:
        self._audit_events.append(event)


def generate_state_token() -> str:
    """Generate a cryptographically secure state token for OAuth flows."""
    return secrets.token_urlsafe(32)


class GraphTokenExchangeError(Exception):
    """Raised when exchanging an OAuth code for a Microsoft Graph token fails."""


@with_resilience(
    _GRAPH_BREAKER,
    max_attempts=3,
    min_wait_seconds=0.5,
    max_wait_seconds=8.0,
    retry_exceptions=(requests.exceptions.Timeout, requests.exceptions.ConnectionError),
)
def _post_token_exchange(token_url: str, data: dict, timeout: float):
    """POST the authorization-code grant. Wrapped in retry-with-backoff and
    a shared circuit breaker (see ``boardmatch.resilience``) since this is
    the real outbound network call."""
    response = requests.post(token_url, data=data, timeout=timeout)
    response.raise_for_status()
    return response


def exchange_code_for_token(
    *,
    token_url: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    scopes: list[str],
    timeout: float = 10.0,
) -> str:
    """Exchange an OAuth authorization code for a Microsoft Graph access token.

    Performs the real authorization-code grant against Microsoft identity
    platform's token endpoint. Raises ``GraphTokenExchangeError`` on any
    network/HTTP/response failure so callers can fall back gracefully.
    """
    try:
        response = _post_token_exchange(
            token_url,
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "scope": " ".join(scopes),
            },
            timeout,
        )
        payload = response.json()
        access_token = payload.get("access_token")
        if not access_token:
            raise GraphTokenExchangeError(
                "Token response did not include an access_token"
            )
        return access_token
    except GraphTokenExchangeError:
        raise
    except Exception as exc:
        raise GraphTokenExchangeError(
            f"Failed to exchange code for token: {exc}"
        ) from exc


class GraphApiError(Exception):
    """Raised when a Microsoft Graph API call fails."""


_GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


@with_resilience(
    _GRAPH_BREAKER,
    max_attempts=3,
    min_wait_seconds=0.5,
    max_wait_seconds=8.0,
    retry_exceptions=(requests.exceptions.Timeout, requests.exceptions.ConnectionError),
)
def _get_graph_people(access_token: str, top: int, timeout: float):
    """GET /me/people. Wrapped in retry-with-backoff and a shared circuit
    breaker (see ``boardmatch.resilience``) since this is the real outbound
    network call."""
    response = requests.get(
        f"{_GRAPH_BASE_URL}/me/people",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"$top": top},
        timeout=timeout,
    )
    response.raise_for_status()
    return response


def fetch_graph_people(
    access_token: str, *, top: int = 25, timeout: float = 10.0
) -> list[dict]:
    """Fetch the signed-in user's relevant people from Microsoft Graph.

    Calls ``GET /me/people``, which Graph ranks by relevance based on the
    user's communication and collaboration patterns — a reasonable proxy for
    "network connections" for warm-introduction path-finding. Raises
    ``GraphApiError`` on any HTTP/network failure so callers can fall back.
    """
    try:
        response = _get_graph_people(access_token, top, timeout)
        return response.json().get("value", [])
    except Exception as exc:
        raise GraphApiError(f"Failed to fetch Microsoft Graph people: {exc}") from exc
