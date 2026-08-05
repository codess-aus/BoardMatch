"""v1 Opportunities routes with auth and explicit response models."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from boardmatch.auth import CurrentUser, get_required_user

from ... import discovery, network, profiles
from ...fit import rank, score_opportunity
from ...models import FitResult, IntroPath
from .schemas import IntroPathResponse, OpportunityListResponse, OpportunityResponse

router = APIRouter(tags=["opportunities"])

_candidate = profiles.load_sample_candidate()


def _build_intro(intro: Optional[IntroPath]) -> Optional[IntroPathResponse]:
    if intro is None:
        return None
    return IntroPathResponse(
        connection=intro.connection.name,
        relationship=intro.connection.relationship,
        reason=intro.reason,
        warmth=intro.warmth,
    )


def _build_opportunity_response(
    fit: FitResult, intro: Optional[IntroPath] = None
) -> OpportunityResponse:
    opp = fit.opportunity
    return OpportunityResponse(
        id=opp.id,
        title=opp.title,
        organisation=opp.organisation,
        sector=opp.sector,
        location=opp.location,
        source=opp.source,
        url=opp.url,
        remuneration=opp.remuneration.value,
        fee_display=opp.fee_display,
        fee_aud=opp.fee_aud,
        closes_on=opp.closes_on,
        summary=opp.summary,
        required_skills=list(opp.required_skills),
        score=fit.score,
        band=fit.band,
        matched_skills=list(fit.matched_skills),
        missing_required=list(fit.missing_required),
        missing_desirable=list(fit.missing_desirable),
        rationale=list(fit.rationale),
        gap_actions=list(fit.gap_actions),
        intro_path=_build_intro(intro),
    )


@router.get("/opportunities", response_model=OpportunityListResponse)
def list_opportunities(
    user: CurrentUser = Depends(get_required_user),
    paid_only: bool = False,
    sector: Optional[str] = None,
    min_fee_aud: Optional[int] = None,
    limit: int = 20,
) -> OpportunityListResponse:
    """List opportunities with fit scoring for the current user."""
    opportunities = discovery.discover(
        paid_only=paid_only, sector=sector, min_fee_aud=min_fee_aud
    )
    fits = rank(_candidate, opportunities, limit=limit)
    results = [
        _build_opportunity_response(f, network.best_path(_candidate, f.opportunity))
        for f in fits
    ]
    return OpportunityListResponse(
        count=len(fits),
        paid_count=sum(1 for f in fits if f.opportunity.is_paid),
        results=results,
    )


@router.get("/opportunities/{opportunity_id}", response_model=OpportunityResponse)
def get_opportunity(
    opportunity_id: str,
    user: CurrentUser = Depends(get_required_user),
) -> OpportunityResponse:
    """Get a single opportunity with fit analysis."""
    opportunity = discovery.get_opportunity(opportunity_id)
    if opportunity is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    fit = score_opportunity(_candidate, opportunity)
    return _build_opportunity_response(fit, network.best_path(_candidate, opportunity))
