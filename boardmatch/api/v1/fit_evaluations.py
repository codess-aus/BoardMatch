"""v1 Fit Evaluations routes — persist and retrieve fit evaluations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from boardmatch.auth import CurrentUser, get_required_user
from boardmatch.config import get_settings
from boardmatch.fit import SCORING_VERSION, score_opportunity
from boardmatch.infrastructure.repositories.factory import create_repositories
from boardmatch.infrastructure.repositories.memory import (
    InMemoryCandidateRepository,
    InMemoryFitEvaluationRepository,
    InMemoryOpportunityRepository,
)
from boardmatch.models import FitEvaluation

from .schemas import (
    FitEvaluationCreateRequest,
    FitEvaluationListResponse,
    FitEvaluationResponse,
)

router = APIRouter(tags=["fit-evaluations"])

_repos = create_repositories(get_settings())
_evaluation_repo = _repos.fit_evaluation_repo
_candidate_repo = _repos.candidate_repo
_opportunity_repo = _repos.opportunity_repo


def get_evaluation_repo() -> InMemoryFitEvaluationRepository:
    return _evaluation_repo


def get_candidate_repo() -> InMemoryCandidateRepository:
    return _candidate_repo


def get_opportunity_repo() -> InMemoryOpportunityRepository:
    return _opportunity_repo


def _to_response(ev: FitEvaluation) -> FitEvaluationResponse:
    return FitEvaluationResponse(
        id=ev.id,
        opportunity_id=ev.opportunity_id,
        profile_version=ev.profile_version,
        scoring_version=ev.scoring_version,
        score=ev.score,
        band=ev.band,
        matched_skills=list(ev.matched_skills),
        missing_skills=list(ev.missing_skills),
        rationale=list(ev.rationale),
        gap_actions=list(ev.gap_actions),
        created_at=ev.created_at.isoformat(),
    )


@router.post(
    "/fit-evaluations",
    response_model=FitEvaluationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_fit_evaluation(
    body: FitEvaluationCreateRequest,
    user: CurrentUser = Depends(get_required_user),
    eval_repo: InMemoryFitEvaluationRepository = Depends(get_evaluation_repo),
    cand_repo: InMemoryCandidateRepository = Depends(get_candidate_repo),
    opp_repo: InMemoryOpportunityRepository = Depends(get_opportunity_repo),
) -> FitEvaluationResponse:
    """Evaluate or re-evaluate fit. Idempotent for same version tuple."""
    opportunity = opp_repo.get_by_id(body.opportunity_id)
    if opportunity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Opportunity not found",
        )

    candidate = cand_repo.get_for_user(user.user_id)
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profile not found. Create a profile before evaluating fit.",
        )

    from boardmatch.profile_api import _profile_versions
    profile_version = _profile_versions.get(user.user_id, 1)

    existing = eval_repo.find_existing(
        user.user_id, body.opportunity_id, profile_version, SCORING_VERSION
    )
    if existing is not None:
        return _to_response(existing)

    fit = score_opportunity(candidate, opportunity)

    evaluation = FitEvaluation(
        id=str(uuid.uuid4()),
        user_id=user.user_id,
        opportunity_id=body.opportunity_id,
        profile_version=profile_version,
        scoring_version=SCORING_VERSION,
        score=fit.score,
        band=fit.band,
        matched_skills=fit.matched_skills,
        missing_skills=tuple(list(fit.missing_required) + list(fit.missing_desirable)),
        rationale=fit.rationale,
        gap_actions=fit.gap_actions,
        created_at=datetime.now(timezone.utc),
    )

    eval_repo.create(evaluation)
    return _to_response(evaluation)


@router.get("/fit-evaluations", response_model=FitEvaluationListResponse)
def list_fit_evaluations(
    user: CurrentUser = Depends(get_required_user),
    eval_repo: InMemoryFitEvaluationRepository = Depends(get_evaluation_repo),
) -> FitEvaluationListResponse:
    """List all fit evaluations for the authenticated user."""
    evals = eval_repo.list_for_user(user.user_id)
    return FitEvaluationListResponse(evaluations=[_to_response(e) for e in evals])


@router.get("/fit-evaluations/{evaluation_id}", response_model=FitEvaluationResponse)
def get_fit_evaluation(
    evaluation_id: str,
    user: CurrentUser = Depends(get_required_user),
    eval_repo: InMemoryFitEvaluationRepository = Depends(get_evaluation_repo),
) -> FitEvaluationResponse:
    """Get a specific fit evaluation by ID."""
    ev = eval_repo.get_by_id(user.user_id, evaluation_id)
    if ev is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evaluation not found",
        )
    return _to_response(ev)
