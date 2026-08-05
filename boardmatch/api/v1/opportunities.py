"""v1 Opportunities routes with auth and explicit response models."""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from boardmatch.auth import CurrentUser, get_required_user
from boardmatch.provenance import build_provenance

from ... import discovery, network, profiles
from ...fit import rank, score_opportunity
from ...infrastructure.repositories.memory import InMemoryOpportunityRepository
from ...models import FitResult, IntroPath, Opportunity
from .schemas import (
    IntroPathResponse,
    OpportunityListResponse,
    OpportunityResponse,
    PaginatedOpportunityResponse,
    ProvenanceResponse,
)

router = APIRouter(tags=["opportunities"])

_candidate = profiles.load_sample_candidate()

# Module-level repository seeded with discovery data
_repo = InMemoryOpportunityRepository(discovery.discover())


def _build_intro(intro: Optional[IntroPath]) -> Optional[IntroPathResponse]:
    if intro is None:
        return None
    return IntroPathResponse(
        connection=intro.connection.name,
        relationship=intro.connection.relationship,
        reason=intro.reason,
        warmth=intro.warmth,
    )


def _build_provenance_response(opp: Opportunity) -> ProvenanceResponse:
    """Build provenance indicators for an opportunity."""
    # Determine if this is from multiple sources (merged duplicate)
    duplicate_sources: Optional[list[str]] = None
    if ";" in opp.source:
        duplicate_sources = [s.strip() for s in opp.source.split(";")]

    # Use current time as first_seen approximation for in-memory data
    now = datetime.now(timezone.utc)
    first_seen = now
    last_verified = now

    provenance = build_provenance(
        opp,
        first_seen=first_seen,
        last_verified=last_verified,
        withdrawn=False,
        duplicate_sources=duplicate_sources,
        now=now,
    )

    return ProvenanceResponse(
        source_name=provenance.source_name,
        source_url=provenance.source_url,
        first_seen=provenance.first_seen,
        last_verified=provenance.last_verified,
        closing_date=provenance.closing_date,
        status=provenance.status.value,
        remuneration_confidence=provenance.remuneration_confidence.value,
        is_stale=provenance.is_stale,
        stale_warning=provenance.stale_warning,
        duplicate_sources=provenance.duplicate_sources,
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
        provenance=_build_provenance_response(opp),
    )


def _build_opportunity_response_from_opp(opp: Opportunity) -> OpportunityResponse:
    """Build response from opportunity with fit scoring."""
    fit = score_opportunity(_candidate, opp)
    intro = network.best_path(_candidate, opp)
    return _build_opportunity_response(fit, intro)


@router.get("/opportunities", response_model=PaginatedOpportunityResponse)
def list_opportunities(
    user: CurrentUser = Depends(get_required_user),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page (max 100)"),
    status: Optional[str] = Query(default="open", description="Filter by status ('open' excludes expired)"),
    paid_only: bool = Query(default=False, description="Only show paid opportunities"),
    sector: Optional[str] = Query(default=None, description="Filter by sector"),
    location: Optional[str] = Query(default=None, description="Filter by location"),
    min_fee_aud: Optional[int] = Query(default=None, description="Minimum fee in AUD"),
    closes_after: Optional[date] = Query(default=None, description="Closes on or after (YYYY-MM-DD)"),
    closes_before: Optional[date] = Query(default=None, description="Closes on or before (YYYY-MM-DD)"),
    source: Optional[str] = Query(default=None, description="Filter by source"),
) -> PaginatedOpportunityResponse:
    """List opportunities with pagination and composable filters."""
    filters: dict[str, object] = {}
    if status:
        filters["status"] = status
    if paid_only:
        filters["paid_only"] = True
    if sector:
        filters["sector"] = sector
    if location:
        filters["location"] = location
    if min_fee_aud is not None:
        filters["min_fee"] = min_fee_aud
    if closes_after is not None:
        filters["closes_after"] = closes_after.isoformat()
    if closes_before is not None:
        filters["closes_before"] = closes_before.isoformat()
    if source:
        filters["source"] = source

    result = _repo.search_paginated(page=page, page_size=page_size, **filters)

    items = [_build_opportunity_response_from_opp(opp) for opp in result.items]
    total_pages = math.ceil(result.total / page_size) if result.total > 0 else 0

    return PaginatedOpportunityResponse(
        items=items,
        total=result.total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
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
