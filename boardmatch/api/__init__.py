"""FastAPI app exposing the BoardMatch agent and authenticated UI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .. import coach, discovery, network, profiles
from ..config import get_settings
from ..fit import rank, score_opportunity
from ..models import ApplicationStage, Candidate, FitResult, IntroPath
from ..profile_api import router as profile_router
from ..readiness import ReadinessTracker
from ..web import router as web_router
from .errors import register_error_handlers
from .health import router as health_router
from .middleware import register_middleware
from .v1 import router as v1_router
from .v1.account import router as account_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings()
    yield


app = FastAPI(
    title="BoardMatch",
    description="Find, qualify for and win paid board seats.",
    version="0.1.0",
    lifespan=lifespan,
)

# Register error handlers and middleware
register_error_handlers(app)
register_middleware(app)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Include versioned API routes
app.include_router(v1_router)
app.include_router(account_router)
app.include_router(profile_router)
app.include_router(health_router)
app.include_router(web_router)

# Demo-scoped in-memory state.
_candidate: Candidate = profiles.load_sample_candidate()
_tracker = ReadinessTracker(candidate=_candidate)


def _serialise_fit(fit: FitResult, intro: IntroPath | None = None) -> dict:
    opportunity = fit.opportunity
    return {
        "id": opportunity.id,
        "title": opportunity.title,
        "organisation": opportunity.organisation,
        "sector": opportunity.sector,
        "location": opportunity.location,
        "source": opportunity.source,
        "url": opportunity.url,
        "remuneration": opportunity.remuneration.value,
        "fee_display": opportunity.fee_display,
        "fee_aud": opportunity.fee_aud,
        "closes_on": opportunity.closes_on,
        "summary": opportunity.summary,
        "required_skills": list(opportunity.required_skills),
        "score": fit.score,
        "band": fit.band,
        "matched_skills": list(fit.matched_skills),
        "missing_required": list(fit.missing_required),
        "missing_desirable": list(fit.missing_desirable),
        "rationale": list(fit.rationale),
        "gap_actions": list(fit.gap_actions),
        "intro_path": (
            {
                "connection": intro.connection.name,
                "relationship": intro.connection.relationship,
                "reason": intro.reason,
                "warmth": intro.warmth,
            }
            if intro
            else None
        ),
    }


@app.get("/api/candidate")
def get_candidate() -> dict:
    return {
        "name": _candidate.name,
        "headline": _candidate.headline,
        "years_experience": _candidate.years_experience,
        "skills": _candidate.skills,
        "sectors": _candidate.sectors,
        "credentials": _candidate.credentials,
        "board_experience": _candidate.board_experience,
        "connections": [c.name for c in _candidate.connections],
    }


@app.get("/api/opportunities")
def list_opportunities(
    paid_only: bool = False,
    sector: str | None = None,
    min_fee_aud: int | None = None,
    limit: int = 20,
) -> dict:
    """Discovery + fit + gap analysis + warm intro path in one call."""
    opportunities = discovery.discover(
        paid_only=paid_only, sector=sector, min_fee_aud=min_fee_aud
    )
    fits = rank(_candidate, opportunities, limit=limit)
    return {
        "count": len(fits),
        "paid_count": sum(1 for f in fits if f.opportunity.is_paid),
        "results": [
            _serialise_fit(f, network.best_path(_candidate, f.opportunity))
            for f in fits
        ],
    }


@app.get("/api/opportunities/{opportunity_id}")
def get_opportunity(opportunity_id: str) -> dict:
    opportunity = discovery.get_opportunity(opportunity_id)
    if opportunity is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    fit = score_opportunity(_candidate, opportunity)
    return _serialise_fit(fit, network.best_path(_candidate, opportunity))


@app.get("/api/opportunities/{opportunity_id}/intro-paths")
def intro_paths(opportunity_id: str) -> dict:
    opportunity = discovery.get_opportunity(opportunity_id)
    if opportunity is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    paths = network.paths_for(_candidate, opportunity)
    return {
        "opportunity_id": opportunity_id,
        "paths": [
            {
                "connection": p.connection.name,
                "relationship": p.connection.relationship,
                "reason": p.reason,
                "warmth": p.warmth,
            }
            for p in paths
        ],
    }


@app.post("/api/coach/board-cv")
def draft_board_cv(opportunity_id: str | None = None) -> dict:
    fit = None
    if opportunity_id:
        opportunity = discovery.get_opportunity(opportunity_id)
        if opportunity is None:
            raise HTTPException(status_code=404, detail="Opportunity not found")
        fit = score_opportunity(_candidate, opportunity)
    draft = coach.board_cv(_candidate, fit)
    return {"kind": draft.kind, "engine": draft.engine, "content": draft.content}


@app.post("/api/coach/bio")
def draft_bio() -> dict:
    draft = coach.director_bio(_candidate)
    return {"kind": draft.kind, "engine": draft.engine, "content": draft.content}


@app.post("/api/coach/outreach")
def draft_outreach(
    opportunity_id: str, recipient: str = "Nominations Committee"
) -> dict:
    opportunity = discovery.get_opportunity(opportunity_id)
    if opportunity is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    path = network.best_path(_candidate, opportunity)
    draft = coach.outreach_message(
        _candidate,
        opportunity,
        recipient=recipient,
        intro_via=path.connection.name if path else None,
    )
    return {"kind": draft.kind, "engine": draft.engine, "content": draft.content}


class TrackRequest(BaseModel):
    opportunity_id: str
    stage: ApplicationStage
    notes: str = ""


@app.post("/api/tracker")
def track(request: TrackRequest) -> dict:
    if discovery.get_opportunity(request.opportunity_id) is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    application = _tracker.track(request.opportunity_id, request.stage, request.notes)
    return {
        "opportunity_id": application.opportunity_id,
        "stage": application.stage.value,
        "notes": application.notes,
        "readiness_score": _tracker.readiness_score(),
    }


@app.get("/api/readiness")
def readiness() -> dict:
    fits = rank(_candidate, discovery.discover(), limit=10)
    return _tracker.snapshot(fits)
