"""Pydantic response models for API v1."""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Consistent error structure for v1 endpoints."""

    detail: str


class IntroPathResponse(BaseModel):
    connection: str
    relationship: str
    reason: str
    warmth: int


class OpportunityResponse(BaseModel):
    id: str
    title: str
    organisation: str
    sector: str
    location: str
    source: str
    url: str | None = None
    remuneration: str
    fee_display: str
    fee_aud: int | None = None
    closes_on: date | None = None
    summary: str
    required_skills: list[str]
    score: float
    band: str
    matched_skills: list[str]
    missing_required: list[str]
    missing_desirable: list[str]
    rationale: list[str]
    gap_actions: list[str]
    intro_path: Optional[IntroPathResponse] = None


class OpportunityListResponse(BaseModel):
    count: int
    paid_count: int
    results: list[OpportunityResponse]


class ApplicationResponse(BaseModel):
    """Response model for a single application."""

    id: str
    opportunity_id: str
    stage: str
    notes: str


class ApplicationListResponse(BaseModel):
    """Response model for listing applications."""

    applications: list[ApplicationResponse]


class ApplicationCreateRequest(BaseModel):
    """Request body for creating an application."""

    opportunity_id: str
    stage: str = "researching"
    notes: str = ""


class ApplicationUpdateRequest(BaseModel):
    """Request body for updating an application."""

    stage: str | None = None
    notes: str | None = None


class ReadinessResponse(BaseModel):
    """Placeholder response for readiness endpoint."""

    readiness_score: float = 0.0
    message: str = "Readiness endpoint - coming soon"


class CoachingBoardCvResponse(BaseModel):
    """Response for coaching board-cv endpoint."""

    kind: str
    engine: str
    content: str


class PaginatedOpportunityResponse(BaseModel):
    """Paginated listing of opportunities."""

    items: list[OpportunityResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
