"""Admin routes for triggering and monitoring ingestion operations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from boardmatch.auth import CurrentUser, get_current_user
from boardmatch.ingestion.base import OpportunitySource, SourceError
from boardmatch.ingestion.json_source import JsonFileSource, gov_vacancies_source, mock_sources_source
from boardmatch.ingestion.models import IngestionRun, IngestionStatus
from boardmatch.ingestion.runner import run_ingestion

router = APIRouter(prefix="/admin", tags=["admin"])

# ---------------------------------------------------------------------------
# In-memory storage for ingestion runs
# ---------------------------------------------------------------------------

_runs: dict[str, dict[str, Any]] = {}
_active_sources: set[str] = set()

# Registry of known sources
_SOURCE_REGISTRY: dict[str, callable] = {
    "gov_vacancies": gov_vacancies_source,
    "mock_sources": mock_sources_source,
}


def reset_state() -> None:
    """Reset in-memory state (for testing)."""
    _runs.clear()
    _active_sources.clear()


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Dependency that requires the authenticated user to have admin role."""
    if "admin" not in user.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class IngestionRunResponse(BaseModel):
    """Serialised representation of an ingestion run."""

    id: str
    source_id: str
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    records_created: int = 0
    records_updated: int = 0
    records_deactivated: int = 0
    error_message: str | None = None


class SyncResponse(BaseModel):
    """Response for a sync trigger."""

    run_id: str
    source_id: str
    status: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_to_response(run_id: str, data: dict[str, Any]) -> IngestionRunResponse:
    """Convert stored run data to response model."""
    run: IngestionRun = data["run"]
    return IngestionRunResponse(
        id=run_id,
        source_id=run.source_key,
        status=run.status.value,
        started_at=run.started_at.isoformat() if run.started_at else None,
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        records_created=run.records_created,
        records_updated=run.records_updated,
        records_deactivated=run.records_deactivated,
        error_message=run.error_message,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/sources/{source_id}/sync",
    response_model=SyncResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_sync(
    source_id: str,
    user: CurrentUser = Depends(require_admin),
) -> SyncResponse:
    """Trigger an ingestion sync for the specified source."""
    if source_id not in _SOURCE_REGISTRY:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown source: {source_id}",
        )

    if source_id in _active_sources:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Source '{source_id}' already has a running ingestion",
        )

    _active_sources.add(source_id)
    run_id = str(uuid.uuid4())

    source: OpportunitySource = _SOURCE_REGISTRY[source_id]()
    run = run_ingestion(source)

    # Remove from active once complete
    _active_sources.discard(source_id)

    _runs[run_id] = {"run": run}

    return SyncResponse(
        run_id=run_id,
        source_id=source_id,
        status=run.status.value,
    )


@router.get("/ingestion-runs", response_model=list[IngestionRunResponse])
def list_ingestion_runs(
    user: CurrentUser = Depends(require_admin),
) -> list[IngestionRunResponse]:
    """List all ingestion runs."""
    return [_run_to_response(run_id, data) for run_id, data in _runs.items()]


@router.get("/ingestion-runs/{run_id}", response_model=IngestionRunResponse)
def get_ingestion_run(
    run_id: str,
    user: CurrentUser = Depends(require_admin),
) -> IngestionRunResponse:
    """Get details of a specific ingestion run."""
    if run_id not in _runs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ingestion run not found: {run_id}",
        )
    return _run_to_response(run_id, _runs[run_id])
