"""v1 Readiness routes — board-readiness score and history."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends

from boardmatch.auth import CurrentUser, get_required_user
from boardmatch.config import get_settings
from boardmatch.infrastructure.repositories.factory import create_repositories
from boardmatch.infrastructure.repositories.memory import (
    InMemoryApplicationRepository,
    InMemoryCandidateRepository,
)
from boardmatch.models import Candidate
from boardmatch.readiness import ReadinessTracker

from .schemas import (
    ReadinessComponentsResponse,
    ReadinessHistoryEntry,
    ReadinessHistoryResponse,
    ReadinessResponse,
)

router = APIRouter(tags=["readiness"])

SCORING_VERSION = "1.0.0"

# In-memory history store: user_id -> list of snapshots
_history_store: dict[str, list[dict[str, Any]]] = {}

# Shared repository instances (same pattern as applications/profile)
_repos = create_repositories(get_settings())
_candidate_repo = _repos.candidate_repo
_application_repo = _repos.application_repo


def get_candidate_repo() -> InMemoryCandidateRepository:
    return _candidate_repo


def get_application_repo() -> InMemoryApplicationRepository:
    return _application_repo


def get_history_store() -> dict[str, list[dict[str, Any]]]:
    return _history_store


def _default_candidate(user: CurrentUser) -> Candidate:
    """Return a minimal candidate when no profile exists."""
    return Candidate(name=user.display_name or user.user_id)


def _compute_readiness(
    user: CurrentUser,
    candidate_repo: InMemoryCandidateRepository,
    application_repo: InMemoryApplicationRepository,
) -> dict[str, Any]:
    """Compute readiness score from user's profile and applications."""
    candidate = candidate_repo.get_for_user(user.user_id)
    if candidate is None:
        candidate = _default_candidate(user)

    applications = application_repo.list_for_user(user.user_id)
    app_dict = {app.opportunity_id: app for app in applications}

    tracker = ReadinessTracker(candidate=candidate, applications=app_dict)

    # Derive next actions from profile state (no fit evaluations needed for basic actions)
    next_actions = _derive_next_actions(tracker)

    return {
        "score": tracker.readiness_score(),
        "components": {
            "credentials": tracker.credentials_score(),
            "skills": tracker.skills_score(),
            "pipeline_momentum": tracker.pipeline_score(),
        },
        "scoring_version": SCORING_VERSION,
        "next_actions": next_actions,
        "stage_counts": tracker.stage_counts(),
    }


def _derive_next_actions(tracker: ReadinessTracker, limit: int = 5) -> list[str]:
    """Derive next actions from the user's actual profile and applications."""
    actions: list[str] = []

    if tracker.credentials_score() < 20:
        actions.append(
            "Complete a governance credential (e.g. AICD Company Directors Course)."
        )
    if not tracker.candidate.board_experience:
        actions.append("Add prior board or committee experience to your profile.")
    if tracker.skills_score() < 18:
        actions.append("Add more governance-relevant skills to your profile.")
    if not tracker.applications:
        actions.append(
            "Start your pipeline: track at least three paid seats this week."
        )
    elif tracker.pipeline_score() < 10:
        actions.append("Progress applications beyond the researching stage.")

    if not actions:
        actions.append("Maintain momentum — keep advancing your pipeline.")

    return actions[:limit]


@router.get("/readiness", response_model=ReadinessResponse)
def get_readiness(
    user: CurrentUser = Depends(get_required_user),
    candidate_repo: InMemoryCandidateRepository = Depends(get_candidate_repo),
    application_repo: InMemoryApplicationRepository = Depends(get_application_repo),
    history: dict[str, list[dict[str, Any]]] = Depends(get_history_store),
) -> ReadinessResponse:
    """Get board readiness assessment for the current user."""
    result = _compute_readiness(user, candidate_repo, application_repo)

    # Save snapshot to history
    snapshot = {
        **result,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    history.setdefault(user.user_id, []).append(snapshot)

    return ReadinessResponse(
        score=result["score"],
        components=ReadinessComponentsResponse(**result["components"]),
        scoring_version=result["scoring_version"],
        next_actions=result["next_actions"],
        stage_counts=result["stage_counts"],
    )


@router.get("/readiness/history", response_model=ReadinessHistoryResponse)
def get_readiness_history(
    user: CurrentUser = Depends(get_required_user),
    history: dict[str, list[dict[str, Any]]] = Depends(get_history_store),
) -> ReadinessHistoryResponse:
    """Get historical readiness snapshots for the current user."""
    user_history = history.get(user.user_id, [])
    snapshots = [
        ReadinessHistoryEntry(
            score=entry["score"],
            components=ReadinessComponentsResponse(**entry["components"]),
            scoring_version=entry["scoring_version"],
            next_actions=entry["next_actions"],
            stage_counts=entry["stage_counts"],
            timestamp=entry["timestamp"],
        )
        for entry in user_history
    ]
    return ReadinessHistoryResponse(snapshots=snapshots)
