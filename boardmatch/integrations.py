"""Consent and integration records for external providers (e.g. Microsoft Graph).

Provides models, repository protocol, in-memory implementation, and audit event
tracking for OAuth-based integrations.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


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
        return [
            i for i in self._integrations.values() if i.user_id == user_id
        ]

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
