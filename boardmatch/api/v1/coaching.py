"""v1 Coaching routes."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from boardmatch.auth import CurrentUser, get_required_user
from boardmatch.drafts import Draft, InMemoryDraftRepository, new_draft_id
from boardmatch.validation import validate_draft

from ... import coach, discovery, profiles
from ...fit import score_opportunity
from .rate_limit import draft_rate_limiter
from .authorization import require_active_user
from .schemas import CoachingBoardCvResponse

router = APIRouter(prefix="/coaching", tags=["coaching"], dependencies=[Depends(require_active_user)])

_candidate = profiles.load_sample_candidate()
_draft_repo = InMemoryDraftRepository()

# Current prompt version
_PROMPT_VERSION = "1.0"

# Module-level profile version tracker
_profile_versions: dict[str, int] = {}


def get_draft_repo() -> InMemoryDraftRepository:
    """Accessor for test overrides."""
    return _draft_repo


def _check_rate_limit(user_id: str) -> None:
    """Raise 429 if user has exceeded draft generation rate limit."""
    if not draft_rate_limiter.is_allowed(user_id):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded: maximum 10 drafts per hour",
        )


def _validate_and_raise(content: str, draft_type: str, engine: str) -> None:
    """Validate draft content and raise 422 if validation fails."""
    result = validate_draft(content, draft_type, engine)
    if not result.valid:
        raise HTTPException(
            status_code=422,
            detail=f"Draft validation failed: {'; '.join(result.errors)}",
        )


def _current_profile_version(user_id: str) -> int:
    return _profile_versions.get(user_id, 1)


def _model_name() -> Optional[str]:
    if coach.azure_openai_configured():
        return os.getenv("AZURE_OPENAI_DEPLOYMENT")
    return None


# --- Response schemas ---


class DraftResponse(BaseModel):
    id: str
    draft_type: str
    content: str
    engine: str
    model_name: Optional[str] = None
    prompt_version: str
    profile_version: int
    opportunity_id: Optional[str] = None
    created_at: str


class DraftListResponse(BaseModel):
    count: int
    drafts: list[DraftResponse]


def _draft_to_response(d: Draft) -> DraftResponse:
    return DraftResponse(
        id=d.id,
        draft_type=d.draft_type,
        content=d.content,
        engine=d.engine,
        model_name=d.model_name,
        prompt_version=d.prompt_version,
        profile_version=d.profile_version,
        opportunity_id=d.opportunity_id,
        created_at=d.created_at.isoformat(),
    )


# --- Generation + persist endpoints ---


@router.post("/board-cv", response_model=CoachingBoardCvResponse)
def draft_board_cv(
    user: CurrentUser = Depends(get_required_user),
    opportunity_id: Optional[str] = None,
) -> CoachingBoardCvResponse:
    """Generate a board CV draft, optionally tailored to an opportunity."""
    _check_rate_limit(user.user_id)

    fit = None
    if opportunity_id:
        opportunity = discovery.get_opportunity(opportunity_id)
        if opportunity is None:
            raise HTTPException(status_code=404, detail="Opportunity not found")
        fit = score_opportunity(_candidate, opportunity)
    raw_draft = coach.board_cv(_candidate, fit)

    _validate_and_raise(raw_draft.content, raw_draft.kind, raw_draft.engine)

    persisted = Draft(
        id=new_draft_id(),
        user_id=user.user_id,
        draft_type=raw_draft.kind,
        content=raw_draft.content,
        engine=raw_draft.engine,
        model_name=_model_name(),
        prompt_version=_PROMPT_VERSION,
        profile_version=_current_profile_version(user.user_id),
        opportunity_id=opportunity_id,
    )
    _draft_repo.create(persisted)
    draft_rate_limiter.record(user.user_id)

    return CoachingBoardCvResponse(
        kind=raw_draft.kind, engine=raw_draft.engine, content=raw_draft.content
    )


@router.post("/director-bio", response_model=DraftResponse)
def draft_director_bio(
    user: CurrentUser = Depends(get_required_user),
) -> DraftResponse:
    """Generate a director bio and persist the draft."""
    _check_rate_limit(user.user_id)

    raw_draft = coach.director_bio(_candidate)

    _validate_and_raise(raw_draft.content, raw_draft.kind, raw_draft.engine)

    persisted = Draft(
        id=new_draft_id(),
        user_id=user.user_id,
        draft_type=raw_draft.kind,
        content=raw_draft.content,
        engine=raw_draft.engine,
        model_name=_model_name(),
        prompt_version=_PROMPT_VERSION,
        profile_version=_current_profile_version(user.user_id),
    )
    _draft_repo.create(persisted)
    draft_rate_limiter.record(user.user_id)
    return _draft_to_response(persisted)


@router.post("/outreach", response_model=DraftResponse)
def draft_outreach(
    user: CurrentUser = Depends(get_required_user),
    opportunity_id: Optional[str] = None,
) -> DraftResponse:
    """Generate an outreach message and persist the draft."""
    _check_rate_limit(user.user_id)

    if not opportunity_id:
        raise HTTPException(status_code=400, detail="opportunity_id is required")
    opportunity = discovery.get_opportunity(opportunity_id)
    if opportunity is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    raw_draft = coach.outreach_message(_candidate, opportunity)

    _validate_and_raise(raw_draft.content, raw_draft.kind, raw_draft.engine)

    persisted = Draft(
        id=new_draft_id(),
        user_id=user.user_id,
        draft_type=raw_draft.kind,
        content=raw_draft.content,
        engine=raw_draft.engine,
        model_name=_model_name(),
        prompt_version=_PROMPT_VERSION,
        profile_version=_current_profile_version(user.user_id),
        opportunity_id=opportunity_id,
    )
    _draft_repo.create(persisted)
    draft_rate_limiter.record(user.user_id)
    return _draft_to_response(persisted)


# --- CRUD endpoints ---


@router.get("/drafts", response_model=DraftListResponse)
def list_drafts(
    user: CurrentUser = Depends(get_required_user),
) -> DraftListResponse:
    """List all drafts for the authenticated user."""
    drafts = _draft_repo.list_for_user(user.user_id)
    return DraftListResponse(
        count=len(drafts),
        drafts=[_draft_to_response(d) for d in drafts],
    )


@router.get("/drafts/{draft_id}", response_model=DraftResponse)
def get_draft(
    draft_id: str,
    user: CurrentUser = Depends(get_required_user),
) -> DraftResponse:
    """Get a single draft by ID."""
    draft = _draft_repo.get_by_id(draft_id, user.user_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    return _draft_to_response(draft)


@router.delete("/drafts/{draft_id}", status_code=204)
def delete_draft(
    draft_id: str,
    user: CurrentUser = Depends(get_required_user),
) -> None:
    """Delete a draft."""
    deleted = _draft_repo.delete(draft_id, user.user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Draft not found")
