"""v1 Applications routes — CRUD for user board applications."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from boardmatch.auth import CurrentUser, get_required_user
from boardmatch.infrastructure.repositories.memory import (
    InMemoryApplicationRepository,
    InMemoryOpportunityRepository,
)
from boardmatch.models import Application, ApplicationStage

from .schemas import (
    ApplicationCreateRequest,
    ApplicationListResponse,
    ApplicationResponse,
    ApplicationUpdateRequest,
)

router = APIRouter(tags=["applications"])

_application_repo = InMemoryApplicationRepository()
_opportunity_repo = InMemoryOpportunityRepository()


def get_application_repo() -> InMemoryApplicationRepository:
    return _application_repo


def get_opportunity_repo() -> InMemoryOpportunityRepository:
    return _opportunity_repo


def _to_response(app: Application) -> ApplicationResponse:
    return ApplicationResponse(
        id=app.id,
        opportunity_id=app.opportunity_id,
        stage=app.stage.value,
        notes=app.notes,
    )


@router.get("/applications", response_model=ApplicationListResponse)
def list_applications(
    user: CurrentUser = Depends(get_required_user),
    repo: InMemoryApplicationRepository = Depends(get_application_repo),
) -> ApplicationListResponse:
    """List all applications for the authenticated user."""
    apps = repo.list_for_user(user.user_id)
    return ApplicationListResponse(applications=[_to_response(a) for a in apps])


@router.post(
    "/applications",
    response_model=ApplicationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_application(
    body: ApplicationCreateRequest,
    user: CurrentUser = Depends(get_required_user),
    repo: InMemoryApplicationRepository = Depends(get_application_repo),
    opp_repo: InMemoryOpportunityRepository = Depends(get_opportunity_repo),
) -> ApplicationResponse:
    """Create a new application. Rejects duplicates for the same opportunity."""
    # Validate stage
    try:
        stage = ApplicationStage(body.stage)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid stage: {body.stage}",
        )

    # Validate opportunity exists
    if opp_repo.get_by_id(body.opportunity_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Opportunity not found",
        )

    # Reject duplicate application for same opportunity
    existing = repo.list_for_user(user.user_id)
    for app in existing:
        if app.opportunity_id == body.opportunity_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Application already exists for this opportunity",
            )

    application = Application(
        opportunity_id=body.opportunity_id,
        stage=stage,
        notes=body.notes,
    )
    created = repo.create(user.user_id, application)
    return _to_response(created)


@router.get("/applications/{application_id}", response_model=ApplicationResponse)
def get_application(
    application_id: str,
    user: CurrentUser = Depends(get_required_user),
    repo: InMemoryApplicationRepository = Depends(get_application_repo),
) -> ApplicationResponse:
    """Get a single application by ID."""
    app = repo.get_by_id(user.user_id, application_id)
    if app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found",
        )
    return _to_response(app)


@router.patch("/applications/{application_id}", response_model=ApplicationResponse)
def update_application(
    application_id: str,
    body: ApplicationUpdateRequest,
    user: CurrentUser = Depends(get_required_user),
    repo: InMemoryApplicationRepository = Depends(get_application_repo),
) -> ApplicationResponse:
    """Update stage and/or notes of an existing application."""
    stage = None
    if body.stage is not None:
        try:
            stage = ApplicationStage(body.stage)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid stage: {body.stage}",
            )

    updated = repo.update(user.user_id, application_id, stage=stage, notes=body.notes)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found",
        )
    return _to_response(updated)


@router.delete(
    "/applications/{application_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_application(
    application_id: str,
    user: CurrentUser = Depends(get_required_user),
    repo: InMemoryApplicationRepository = Depends(get_application_repo),
) -> None:
    """Delete an application."""
    if not repo.delete(user.user_id, application_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found",
        )
