"""Audit logging for sensitive user actions.

Provides an in-memory audit store with structured events covering profile
changes, application activity, draft generation, data exports and account
deletion.  Events are retained per a configurable policy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Optional


class AuditAction(StrEnum):
    """Enumeration of auditable actions."""

    LOGIN = "login"
    PROFILE_UPDATED = "profile_updated"
    APPLICATION_CREATED = "application_created"
    APPLICATION_UPDATED = "application_updated"
    DRAFT_GENERATED = "draft_generated"
    EXPORT_REQUESTED = "export_requested"
    ACCOUNT_DELETED = "account_deleted"


@dataclass(frozen=True)
class AuditEvent:
    """Immutable record of an auditable action."""

    id: str
    user_id: str
    action: AuditAction
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    details: Optional[dict] = None


DEFAULT_RETENTION_DAYS = 90


class AuditLogger:
    """In-memory audit logger with retention policy."""

    def __init__(self, retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
        self._events: list[AuditEvent] = []
        self._retention_days = retention_days

    def log(
        self,
        user_id: str,
        action: AuditAction,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> AuditEvent:
        """Record an audit event and return it."""
        event = AuditEvent(
            id=str(uuid.uuid4()),
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
        )
        self._events.append(event)
        return event

    def get_events(self, user_id: str) -> list[AuditEvent]:
        """Return audit events for a user, most recent first, respecting retention."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        return sorted(
            [e for e in self._events if e.user_id == user_id and e.timestamp >= cutoff],
            key=lambda e: e.timestamp,
            reverse=True,
        )

    def purge_expired(self) -> int:
        """Remove events older than retention period. Returns count purged."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        before = len(self._events)
        self._events = [e for e in self._events if e.timestamp >= cutoff]
        return before - len(self._events)

    def clear(self) -> None:
        """Remove all events (for testing)."""
        self._events.clear()
