"""Authorisation helpers for BM-029: ownership and role enforcement.

Provides reusable FastAPI dependencies and helpers to enforce:
- Admin-only access via role check
- Resource ownership verification (user can only access own resources)
- Disabled/deleted user rejection
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status

from boardmatch.auth import CurrentUser, get_current_user


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """FastAPI dependency that requires the authenticated user to have admin role.

    Raises HTTP 403 if user.roles does not contain "admin".
    """
    if "admin" not in user.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


def require_resource_owner(resource_user_id: str, current_user: CurrentUser) -> None:
    """Verify the current user owns the requested resource.

    Raises HTTP 403 if resource_user_id does not match current_user.user_id.
    """
    if resource_user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: you do not own this resource",
        )


def require_active_user(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """FastAPI dependency that rejects disabled or deleted users.

    Checks for 'disabled' in user.roles as the signal for deactivated accounts.
    """
    if "disabled" in user.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )
    return user
