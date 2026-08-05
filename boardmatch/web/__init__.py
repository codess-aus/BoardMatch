"""Web UI module for BoardMatch — serves the authenticated SPA."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

WEB_DIR = Path(__file__).resolve().parent

router = APIRouter(tags=["web"])


@router.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve the main SPA shell."""
    return FileResponse(WEB_DIR / "index.html")


@router.get("/api/session", include_in_schema=False)
def session_info(request: Request) -> JSONResponse:
    """Return minimal session info for the UI.

    In dev mode, reads from X-Dev-User-Id header.
    Returns 401 if no auth is present.
    """
    user_id = request.headers.get("X-Dev-User-Id")
    if not user_id:
        return JSONResponse(
            status_code=401,
            content={"authenticated": False, "detail": "Not signed in"},
        )
    return JSONResponse(
        content={
            "authenticated": True,
            "user_id": user_id,
            "display_name": user_id,
        }
    )
