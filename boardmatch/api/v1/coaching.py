"""v1 Coaching routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from boardmatch.auth import CurrentUser, get_required_user

from ... import coach, discovery, profiles
from ...fit import score_opportunity
from .schemas import CoachingBoardCvResponse

router = APIRouter(prefix="/coaching", tags=["coaching"])

_candidate = profiles.load_sample_candidate()


@router.post("/board-cv", response_model=CoachingBoardCvResponse)
def draft_board_cv(
    user: CurrentUser = Depends(get_required_user),
    opportunity_id: Optional[str] = None,
) -> CoachingBoardCvResponse:
    """Generate a board CV draft, optionally tailored to an opportunity."""
    fit = None
    if opportunity_id:
        opportunity = discovery.get_opportunity(opportunity_id)
        if opportunity is None:
            raise HTTPException(status_code=404, detail="Opportunity not found")
        fit = score_opportunity(_candidate, opportunity)
    draft = coach.board_cv(_candidate, fit)
    return CoachingBoardCvResponse(
        kind=draft.kind, engine=draft.engine, content=draft.content
    )
