"""Microsoft Entra ID (Azure AD) authentication provider for BoardMatch.

Validates JWT tokens issued by Microsoft Entra ID, checking issuer, audience,
expiration, and optional tenant. Maps token claims to CurrentUser.
"""

from __future__ import annotations

import logging
from typing import Any

import jwt
import requests
from fastapi import HTTPException, Request, status

from .auth import CurrentUser

logger = logging.getLogger(__name__)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Authentication required",
    headers={"WWW-Authenticate": "Bearer"},
)


class EntraAuthProvider:
    """Authenticates requests using Microsoft Entra ID JWT tokens.

    Validates tokens against the configured issuer's JWKS endpoint,
    checks audience, expiration, and optional tenant restrictions.
    """

    def __init__(
        self,
        issuer: str,
        audience: str,
        tenant_id: str | None = None,
        *,
        jwks_client: jwt.PyJWKClient | None = None,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._tenant_id = tenant_id

        if jwks_client is not None:
            self._jwks_client = jwks_client
        else:
            jwks_uri = self._discover_jwks_uri(issuer)
            self._jwks_client = jwt.PyJWKClient(jwks_uri)

    @staticmethod
    def _discover_jwks_uri(issuer: str) -> str:
        """Fetch the JWKS URI from the issuer's OpenID Connect discovery endpoint."""
        well_known_url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
        try:
            response = requests.get(well_known_url, timeout=10)
            response.raise_for_status()
            return response.json()["jwks_uri"]
        except Exception:  # noqa: BLE001 - any discovery failure falls back to the default path
            logger.warning("Failed to discover JWKS URI, using default Microsoft path")
            return f"{issuer.rstrip('/')}/discovery/v2.0/keys"

    def authenticate(self, request: Request) -> CurrentUser:
        """Validate the Bearer token and return the authenticated user."""
        token = self._extract_token(request)
        claims = self._validate_token(token)
        return self._map_claims(claims)

    def _extract_token(self, request: Request) -> str:
        """Extract Bearer token from the Authorization header."""
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise _UNAUTHORIZED
        token = auth_header[7:].strip()
        if not token:
            raise _UNAUTHORIZED
        return token

    def _validate_token(self, token: str) -> dict[str, Any]:
        """Decode and validate the JWT token."""
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=self._audience,
                options={
                    "require": ["exp", "iss", "aud", "sub"],
                    "verify_exp": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except jwt.ExpiredSignatureError:
            logger.debug("Token expired")
            raise _UNAUTHORIZED
        except jwt.InvalidIssuerError:
            logger.debug("Invalid token issuer")
            raise _UNAUTHORIZED
        except jwt.InvalidAudienceError:
            logger.debug("Invalid token audience")
            raise _UNAUTHORIZED
        except jwt.InvalidTokenError:
            logger.debug("Invalid token")
            raise _UNAUTHORIZED

        # Verify subject claim is present and non-empty
        if not claims.get("sub"):
            raise _UNAUTHORIZED

        # Verify tenant if configured
        if self._tenant_id:
            token_tid = claims.get("tid", "")
            if token_tid != self._tenant_id:
                logger.debug("Token tenant mismatch")
                raise _UNAUTHORIZED

        return claims

    def _map_claims(self, claims: dict[str, Any]) -> CurrentUser:
        """Map JWT claims to a CurrentUser instance."""
        user_id = claims["sub"]
        email = claims.get("preferred_username") or claims.get("email")
        display_name = claims.get("name")

        # Extract roles from the token's roles claim
        roles_claim = claims.get("roles", [])
        if isinstance(roles_claim, str):
            roles_claim = [roles_claim]

        roles: list[str] = list(roles_claim)

        # Ensure at least the "user" role is present
        if not roles:
            roles = ["user"]

        return CurrentUser(
            user_id=user_id,
            email=email,
            display_name=display_name,
            roles=roles,
        )
