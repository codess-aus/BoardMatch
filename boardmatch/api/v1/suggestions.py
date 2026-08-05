"""Profile suggestion review endpoints at /api/v1/profile/suggestions (BM-023).

Allows users to list pending suggestions from document processing,
and accept or reject individual suggestions. Accepted changes update
the profile, increment version, and mark fit evaluations as stale.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from ...auth import CurrentUser, get_current_user
from ...infrastructure.repositories.memory import InMemoryCandidateRepository
from ...profile_api import _candidate_repo, _profile_versions
from ...suggestions import (
    InMemorySuggestionStore,
    ProfileSuggestion,
    SuggestionResponse,
    SuggestionStatus,
)

router = APIRouter(prefix="/profile/suggestions", tags=["suggestions"])

# Module-level store shared across requests
_suggestion_store = InMemorySuggestionStore()

# Audit log: list of dicts recording each accept/reject action
_audit_log: list[dict] = []

# Stale fit evaluations: set of user_ids whose fits are stale
_stale_fits: set[str] = set()


def get_suggestion_store() -> InMemorySuggestionStore:
    return _suggestion_store


def _to_response(suggestion: ProfileSuggestion) -> SuggestionResponse:
    return SuggestionResponse(
        id=suggestion.id,
        user_id=suggestion.user_id,
        field_name=suggestion.field_name,
        suggested_value=suggestion.suggested_value,
        source=suggestion.source,
        confidence=suggestion.confidence,
        status=suggestion.status,
        created_at=suggestion.created_at.isoformat(),
        resolved_at=suggestion.resolved_at.isoformat() if suggestion.resolved_at else None,
    )


# List of profile fields that map to list-type attributes on Candidate
_LIST_FIELDS = frozenset(
    {
        "skills",
        "sectors",
        "credentials",
        "board_experience",
        "achievements",
        "locations",
    }
)

# Scalar string fields on Candidate
_SCALAR_FIELDS = frozenset({"name", "headline"})

# Numeric fields on Candidate
_NUMERIC_FIELDS = frozenset({"years_experience"})

VALID_PROFILE_FIELDS = _LIST_FIELDS | _SCALAR_FIELDS | _NUMERIC_FIELDS


@router.get("", response_model=list[SuggestionResponse])
def list_suggestions(
    user: CurrentUser = Depends(get_current_user),
    store: InMemorySuggestionStore = Depends(get_suggestion_store),
) -> list[SuggestionResponse]:
    """List pending suggestions for the current user."""
    suggestions = store.list_pending_for_user(user.user_id)
    return [_to_response(s) for s in suggestions]


@router.post(
    "/{suggestion_id}/accept",
    response_model=SuggestionResponse,
    status_code=status.HTTP_200_OK,
)
def accept_suggestion(
    suggestion_id: str,
    user: CurrentUser = Depends(get_current_user),
    store: InMemorySuggestionStore = Depends(get_suggestion_store),
    repo: InMemoryCandidateRepository = Depends(lambda: _candidate_repo),
) -> SuggestionResponse:
    """Accept a suggestion, applying it to the user's profile."""
    suggestion = store.get_by_id(suggestion_id)
    if suggestion is None or suggestion.user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Suggestion not found",
        )
    if suggestion.status != SuggestionStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Suggestion already {suggestion.status}",
        )

    # Apply to profile
    candidate = repo.get_for_user(user.user_id)
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found — create a profile first",
        )

    field_name = suggestion.field_name
    value = suggestion.suggested_value

    if field_name in _LIST_FIELDS:
        current_list: list = getattr(candidate, field_name)
        if value not in current_list:
            current_list.append(value)
    elif field_name in _SCALAR_FIELDS:
        setattr(candidate, field_name, value)
    elif field_name in _NUMERIC_FIELDS:
        setattr(candidate, field_name, int(value))
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown profile field: {field_name}",
        )

    repo.save_for_user(user.user_id, candidate)

    # Increment profile version
    _profile_versions[user.user_id] = _profile_versions.get(user.user_id, 1) + 1

    # Mark fit evaluations as stale
    _stale_fits.add(user.user_id)

    # Update suggestion status
    suggestion.status = SuggestionStatus.ACCEPTED
    suggestion.resolved_at = datetime.now(timezone.utc)

    # Audit
    _audit_log.append(
        {
            "action": "accept",
            "suggestion_id": suggestion.id,
            "user_id": user.user_id,
            "field_name": suggestion.field_name,
            "suggested_value": suggestion.suggested_value,
            "source": suggestion.source,
            "timestamp": suggestion.resolved_at.isoformat(),
        }
    )

    return _to_response(suggestion)


@router.post(
    "/{suggestion_id}/reject",
    response_model=SuggestionResponse,
    status_code=status.HTTP_200_OK,
)
def reject_suggestion(
    suggestion_id: str,
    user: CurrentUser = Depends(get_current_user),
    store: InMemorySuggestionStore = Depends(get_suggestion_store),
) -> SuggestionResponse:
    """Reject a suggestion without modifying the profile."""
    suggestion = store.get_by_id(suggestion_id)
    if suggestion is None or suggestion.user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Suggestion not found",
        )
    if suggestion.status != SuggestionStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Suggestion already {suggestion.status}",
        )

    suggestion.status = SuggestionStatus.REJECTED
    suggestion.resolved_at = datetime.now(timezone.utc)

    # Audit
    _audit_log.append(
        {
            "action": "reject",
            "suggestion_id": suggestion.id,
            "user_id": user.user_id,
            "field_name": suggestion.field_name,
            "suggested_value": suggestion.suggested_value,
            "source": suggestion.source,
            "timestamp": suggestion.resolved_at.isoformat(),
        }
    )

    return _to_response(suggestion)
