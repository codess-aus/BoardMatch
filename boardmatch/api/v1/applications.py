"""v1 Applications routes — CRUD for user board applications."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from boardmatch.auth import CurrentUser, get_required_user
from boardmatch.config import get_settings
from boardmatch.infrastructure.repositories.factory import create_repositories
from boardmatch.infrastructure.repositories.memory import (
    InMemoryApplicationRepository,
    InMemoryOpportunityRepository,
)
from boardmatch.models import (
    VALID_STAGE_TRANSITIONS,
    Application,
    ApplicationEvent,
    ApplicationStage,
)

from .authorization import require_active_user
from .schemas import (
    ApplicationCreateRequest,
    ApplicationEventCreateRequest,
    ApplicationEventListResponse,
    ApplicationEventResponse,
    ApplicationListResponse,
    ApplicationResponse,
    ApplicationUpdateRequest,
)

router = APIRouter(tags=["applications"], dependencies=[Depends(require_active_user)])

_repos = create_repositories(get_settings())
_application_repo = _repos.application_repo
_opportunity_repo = _repos.opportunity_repo


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


def _to_event_response(event: ApplicationEvent) -> ApplicationEventResponse:
    return ApplicationEventResponse(
        id=event.id,
        application_id=event.application_id,
        previous_stage=event.previous_stage.value,
        new_stage=event.new_stage.value,
        timestamp=event.timestamp.isoformat(),
        notes=event.notes,
    )


def _create_event(
    repo: InMemoryApplicationRepository,
    user_id: str,
    application_id: str,
    previous_stage: ApplicationStage,
    new_stage: ApplicationStage,
    notes: str = "",
) -> ApplicationEvent:
    """Create and persist an application event."""
    event = ApplicationEvent(
        id=str(uuid.uuid4()),
        application_id=application_id,
        previous_stage=previous_stage,
        new_stage=new_stage,
        timestamp=datetime.now(timezone.utc),
        notes=notes,
    )
    return repo.add_event(user_id, event)


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
    app = repo.get_by_id(user.user_id, application_id)
    if app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found",
        )

    stage = None
    if body.stage is not None:
        try:
            stage = ApplicationStage(body.stage)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid stage: {body.stage}",
            )
        # Validate transition
        allowed = VALID_STAGE_TRANSITIONS.get(app.stage, set())
        if stage != app.stage and stage not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid transition from {app.stage.value} to {stage.value}",
            )

    previous_stage = app.stage
    updated = repo.update(user.user_id, application_id, stage=stage, notes=body.notes)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found",
        )

    # Auto-create event on stage change
    if stage is not None and stage != previous_stage:
        _create_event(repo, user.user_id, application_id, previous_stage, stage)

    return _to_response(updated)


@router.delete("/applications/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
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


# --- Event endpoints ---


@router.post(
    "/applications/{application_id}/events",
    response_model=ApplicationEventResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_application_event(
    application_id: str,
    body: ApplicationEventCreateRequest,
    user: CurrentUser = Depends(get_required_user),
    repo: InMemoryApplicationRepository = Depends(get_application_repo),
) -> ApplicationEventResponse:
    """Create a stage transition event for an application."""
    app = repo.get_by_id(user.user_id, application_id)
    if app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found",
        )

    try:
        new_stage = ApplicationStage(body.new_stage)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid stage: {body.new_stage}",
        )

    # Validate transition
    allowed = VALID_STAGE_TRANSITIONS.get(app.stage, set())
    if new_stage != app.stage and new_stage not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid transition from {app.stage.value} to {new_stage.value}",
        )

    if new_stage == app.stage:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="New stage must differ from current stage",
        )

    previous_stage = app.stage
    # Update the application stage
    repo.update(user.user_id, application_id, stage=new_stage)
    # Create the event
    event = _create_event(
        repo, user.user_id, application_id, previous_stage, new_stage, body.notes
    )
    return _to_event_response(event)


@router.get(
    "/applications/{application_id}/events",
    response_model=ApplicationEventListResponse,
)
def list_application_events(
    application_id: str,
    user: CurrentUser = Depends(get_required_user),
    repo: InMemoryApplicationRepository = Depends(get_application_repo),
) -> ApplicationEventListResponse:
    """List all events for an application in chronological order."""
    app = repo.get_by_id(user.user_id, application_id)
    if app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found",
        )
    events = repo.list_events(user.user_id, application_id)
    return ApplicationEventListResponse(events=[_to_event_response(e) for e in events])
