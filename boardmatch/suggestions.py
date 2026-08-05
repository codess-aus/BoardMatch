"""Profile suggestion model and in-memory store for BM-023.

Handles the lifecycle of profile suggestions extracted from documents:
pending → accepted/rejected. Accepted suggestions update the profile
and increment its version.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class SuggestionStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ProfileSuggestion(BaseModel):
    """A suggested profile field change from document processing."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    field_name: str
    suggested_value: str
    source: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: SuggestionStatus = SuggestionStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None


class SuggestionResponse(BaseModel):
    """API response model for a profile suggestion."""

    id: str
    user_id: str
    field_name: str
    suggested_value: str
    source: str
    confidence: float
    status: SuggestionStatus
    created_at: str
    resolved_at: str | None = None


class InMemorySuggestionStore:
    """In-memory store for profile suggestions."""

    def __init__(self) -> None:
        self._store: dict[str, ProfileSuggestion] = {}

    def add(self, suggestion: ProfileSuggestion) -> ProfileSuggestion:
        self._store[suggestion.id] = suggestion
        return suggestion

    def get_by_id(self, suggestion_id: str) -> ProfileSuggestion | None:
        return self._store.get(suggestion_id)

    def list_pending_for_user(self, user_id: str) -> list[ProfileSuggestion]:
        return [
            s
            for s in self._store.values()
            if s.user_id == user_id and s.status == SuggestionStatus.PENDING
        ]

    def list_for_user(self, user_id: str) -> list[ProfileSuggestion]:
        return [s for s in self._store.values() if s.user_id == user_id]
