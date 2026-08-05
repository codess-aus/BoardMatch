"""v1 Applications routes (placeholder)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from boardmatch.auth import CurrentUser, get_required_user

from .schemas import ApplicationResponse

router = APIRouter(tags=["applications"])


@router.get("/applications", response_model=ApplicationResponse)
def list_applications(
    user: CurrentUser = Depends(get_required_user),
) -> ApplicationResponse:
    """List applications for the current user (stub)."""
    return ApplicationResponse()
