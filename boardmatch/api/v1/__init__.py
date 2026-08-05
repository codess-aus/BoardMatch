"""API v1 router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from .applications import router as applications_router
from .coaching import router as coaching_router
from .documents import router as documents_router
from .integrations import router as integrations_router
from .opportunities import router as opportunities_router
from .privacy import router as privacy_router
from .readiness import router as readiness_router
from .suggestions import router as suggestions_router

router = APIRouter(prefix="/api/v1", tags=["v1"])
router.include_router(opportunities_router)
router.include_router(applications_router)
router.include_router(readiness_router)
router.include_router(coaching_router)
router.include_router(documents_router)
router.include_router(integrations_router)
router.include_router(privacy_router)
router.include_router(suggestions_router)
