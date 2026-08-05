"""v1 Readiness routes (placeholder)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from boardmatch.auth import CurrentUser, get_required_user

from .schemas import ReadinessResponse

router = APIRouter(tags=["readiness"])


@router.get("/readiness", response_model=ReadinessResponse)
def get_readiness(
    user: CurrentUser = Depends(get_required_user),
) -> ReadinessResponse:
    """Get board readiness assessment for the current user (stub)."""
    return ReadinessResponse()
