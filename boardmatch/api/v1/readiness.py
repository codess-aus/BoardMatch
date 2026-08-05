"""v1 Readiness routes — persistent readiness scoring service."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from boardmatch.auth import CurrentUser, get_required_user
from boardmatch.infrastructure.repositories.memory import (
    InMemoryApplicationRepository,
    InMemoryCandidateRepository,
    InMemoryFitEvaluationRepository,
    InMemoryReadinessRepository,
)
from boardmatch.models import Candidate, ReadinessSnapshot
from boardmatch.readiness import ReadinessTracker

from .schemas import (
    ReadinessComponentsResponse,
    ReadinessHistoryEntry,
    ReadinessHistoryResponse,
    ReadinessResponse,
)

SCORING_VERSION = "1.0"

router = APIRouter(tags=["readiness"])

_readiness_repo = InMemoryReadinessRepository()
_candidate_repo = InMemoryCandidateRepository()
_application_repo = InMemoryApplicationRepository()
_fit_evaluation_repo = InMemoryFitEvaluationRepository()


def get_readiness_repo() -> InMemoryReadinessRepository:
    return _readiness_repo


def get_candidate_repo() -> InMemoryCandidateRepository:
    return _candidate_repo


def get_application_repo() -> InMemoryApplicationRepository:
    return _application_repo


def get_fit_evaluation_repo() -> InMemoryFitEvaluationRepository:
    return _fit_evaluation_repo


def _compute_snapshot(
    user_id: str,
    candidate: Candidate | None,
    application_repo: InMemoryApplicationRepository,
    fit_repo: InMemoryFitEvaluationRepository,
) -> ReadinessSnapshot:
    """Compute a readiness snapshot from the user's current data."""
    if candidate is None:
        candidate = Candidate(name="")

    tracker = ReadinessTracker(candidate=candidate)
    apps = application_repo.list_for_user(user_id)
    for app in apps:
        tracker.applications[app.opportunity_id] = app

    # Derive next_actions from fit evaluations gap_actions (deduplicated)
    fit_evals = fit_repo.list_for_user(user_id)
    actions: list[str] = []
    for ev in fit_evals:
        for action in ev.gap_actions:
            if action not in actions:
                actions.append(action)

    if not tracker.applications:
        pipeline_action = "Start your pipeline: track at least three paid seats this week."
        if pipeline_action not in actions:
            actions.insert(0, pipeline_action)

    next_actions = tuple(actions[:5])

    return ReadinessSnapshot(
        id=str(uuid.uuid4()),
        user_id=user_id,
        scoring_version=SCORING_VERSION,
        readiness_score=tracker.readiness_score(),
        credentials_score=tracker.credentials_score(),
        skills_score=tracker.skills_score(),
        pipeline_score=tracker.pipeline_score(),
        stage_counts=tracker.stage_counts(),
        next_actions=next_actions,
        created_at=datetime.now(timezone.utc),
    )


def _to_response(snapshot: ReadinessSnapshot) -> ReadinessResponse:
    return ReadinessResponse(
        score=snapshot.readiness_score,
        components=ReadinessComponentsResponse(
            credentials=snapshot.credentials_score,
            skills=snapshot.skills_score,
            pipeline_momentum=snapshot.pipeline_score,
        ),
        scoring_version=snapshot.scoring_version,
        next_actions=list(snapshot.next_actions),
        stage_counts=snapshot.stage_counts,
    )


def _to_history_entry(snapshot: ReadinessSnapshot) -> ReadinessHistoryEntry:
    return ReadinessHistoryEntry(
        score=snapshot.readiness_score,
        components=ReadinessComponentsResponse(
            credentials=snapshot.credentials_score,
            skills=snapshot.skills_score,
            pipeline_momentum=snapshot.pipeline_score,
        ),
        scoring_version=snapshot.scoring_version,
        next_actions=list(snapshot.next_actions),
        stage_counts=snapshot.stage_counts,
        timestamp=snapshot.created_at.isoformat(),
    )


@router.get("/readiness", response_model=ReadinessResponse)
def get_readiness(
    user: CurrentUser = Depends(get_required_user),
    readiness_repo: InMemoryReadinessRepository = Depends(get_readiness_repo),
    cand_repo: InMemoryCandidateRepository = Depends(get_candidate_repo),
    app_repo: InMemoryApplicationRepository = Depends(get_application_repo),
    fit_repo: InMemoryFitEvaluationRepository = Depends(get_fit_evaluation_repo),
) -> ReadinessResponse:
    """Compute and persist a readiness snapshot for the current user."""
    candidate = cand_repo.get_for_user(user.user_id)
    snapshot = _compute_snapshot(user.user_id, candidate, app_repo, fit_repo)
    readiness_repo.create(snapshot)
    return _to_response(snapshot)


@router.get("/readiness/history", response_model=ReadinessHistoryResponse)
def get_readiness_history(
    user: CurrentUser = Depends(get_required_user),
    readiness_repo: InMemoryReadinessRepository = Depends(get_readiness_repo),
) -> ReadinessHistoryResponse:
    """Return historical readiness snapshots for the current user."""
    snapshots = readiness_repo.list_for_user(user.user_id)
    return ReadinessHistoryResponse(
        snapshots=[_to_history_entry(s) for s in snapshots]
    )
