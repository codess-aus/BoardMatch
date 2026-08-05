"""Health check endpoints for liveness and readiness probes."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def liveness() -> dict:
    """Liveness probe — always returns 200 if the process is running."""
    return {"status": "ok"}


@router.get("/ready")
def readiness_check() -> dict:
    """Readiness probe — checks dependencies. Currently always 200."""
    return {"status": "ok"}
