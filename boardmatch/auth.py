"""Authentication abstraction for BoardMatch.

Provides a pluggable authentication layer via the AuthProvider protocol.
In LOCAL/TEST environments, DevAuthProvider reads identity from headers.
In PRODUCTION, a real provider (e.g. Microsoft Entra) must be configured.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel

from .config import AppEnvironment, Settings, get_settings


class CurrentUser(BaseModel):
    """Authenticated user identity available to route handlers."""

    user_id: str
    email: str | None = None
    display_name: str | None = None
    roles: list[str] = []


@runtime_checkable
class AuthProvider(Protocol):
    """Protocol that any authentication backend must satisfy."""

    def authenticate(self, request: Request) -> CurrentUser:
        """Extract and validate user identity from the incoming request.

        Raises HTTPException(401) if the request cannot be authenticated.
        """
        ...


_DEV_USER_HEADER = "X-Dev-User-Id"
_DEV_DEFAULT_USER = CurrentUser(
    user_id="dev-user-001",
    email="dev@boardmatch.local",
    display_name="Development User",
    roles=["user"],
)


class DevAuthProvider:
    """Development/test authentication provider.

    Reads user identity from the X-Dev-User-Id header. If the header is
    absent, returns a fixed default test user. This provider MUST NOT be
    used when APP_ENV=production.
    """

    def authenticate(self, request: Request) -> CurrentUser:
        user_id = request.headers.get(_DEV_USER_HEADER)
        if user_id is None:
            return _DEV_DEFAULT_USER
        return CurrentUser(
            user_id=user_id,
            email=f"{user_id}@boardmatch.local",
            display_name=user_id,
            roles=["user"],
        )


class _ProductionStubProvider:
    """Placeholder that rejects all requests until a real provider is configured."""

    def authenticate(self, request: Request) -> CurrentUser:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _get_provider(settings: Settings) -> AuthProvider:
    """Select the appropriate auth provider based on environment."""
    if settings.auth_issuer and settings.auth_audience:
        from .auth_entra import EntraAuthProvider

        return EntraAuthProvider(
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
        )
    if settings.app_env == AppEnvironment.PRODUCTION:
        return _ProductionStubProvider()
    return DevAuthProvider()


def get_current_user(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """FastAPI dependency that authenticates the current request.

    Usage:
        @app.get("/protected")
        def protected(user: CurrentUser = Depends(get_current_user)):
            return {"user_id": user.user_id}
    """
    provider = _get_provider(settings)
    return provider.authenticate(request)
