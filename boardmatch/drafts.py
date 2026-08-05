"""Draft persistence for coaching artefacts.

Stores generated board CVs, director bios and outreach messages with full
generation metadata so users can retrieve, compare and delete drafts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol


@dataclass(frozen=True)
class Draft:
    """A persisted coaching draft with generation metadata."""

    id: str
    user_id: str
    draft_type: str  # board_cv | director_bio | outreach
    content: str
    engine: str  # template | azure_openai
    model_name: Optional[str] = None
    prompt_version: str = "1.0"
    profile_version: int = 1
    opportunity_id: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class DraftRepository(Protocol):
    """Stable contract for draft persistence."""

    def create(self, draft: Draft) -> Draft:
        """Persist a new draft and return it."""
        ...

    def get_by_id(self, draft_id: str, user_id: str) -> Optional[Draft]:
        """Return a draft by ID, scoped to the owning user."""
        ...

    def list_for_user(self, user_id: str) -> list[Draft]:
        """Return all drafts belonging to a user, newest first."""
        ...

    def delete(self, draft_id: str, user_id: str) -> bool:
        """Delete a draft. Returns True if deleted, False if not found."""
        ...


class InMemoryDraftRepository:
    """In-memory implementation for testing and development."""

    def __init__(self) -> None:
        self._store: dict[str, Draft] = {}

    def create(self, draft: Draft) -> Draft:
        self._store[draft.id] = draft
        return draft

    def get_by_id(self, draft_id: str, user_id: str) -> Optional[Draft]:
        draft = self._store.get(draft_id)
        if draft and draft.user_id == user_id:
            return draft
        return None

    def list_for_user(self, user_id: str) -> list[Draft]:
        drafts = [d for d in self._store.values() if d.user_id == user_id]
        return sorted(drafts, key=lambda d: d.created_at, reverse=True)

    def delete(self, draft_id: str, user_id: str) -> bool:
        draft = self._store.get(draft_id)
        if draft and draft.user_id == user_id:
            del self._store[draft_id]
            return True
        return False


def new_draft_id() -> str:
    """Generate a unique draft identifier."""
    return str(uuid.uuid4())
