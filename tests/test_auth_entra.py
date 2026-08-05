"""Tests for BM-024: Microsoft Entra authentication provider."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from boardmatch.auth import CurrentUser, get_current_user
from boardmatch.auth_entra import EntraAuthProvider
from boardmatch.config import Settings


def _generate_rsa_key_pair():
    """Generate an RSA key pair for test JWT signing."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    public_key = private_key.public_key()
    return private_key, public_key


_PRIVATE_KEY, _PUBLIC_KEY = _generate_rsa_key_pair()

_TEST_ISSUER = "https://login.microsoftonline.com/test-tenant-id/v2.0"
_TEST_AUDIENCE = "api://boardmatch"
_TEST_KID = "test-key-id-001"


def _create_token(
    claims: dict[str, Any] | None = None,
    *,
    expired: bool = False,
    issuer: str | None = None,
    audience: str | None = None,
    subject: str | None = "user-sub-123",
    kid: str | None = None,
) -> str:
    """Create a signed JWT with the given claims."""
    now = datetime.now(timezone.utc)
    if expired:
        exp = now - timedelta(hours=1)
        iat = now - timedelta(hours=2)
    else:
        exp = now + timedelta(hours=1)
        iat = now

    payload: dict[str, Any] = {
        "iss": issuer or _TEST_ISSUER,
        "aud": audience or _TEST_AUDIENCE,
        "exp": exp,
        "iat": iat,
        "nbf": iat,
    }
    if subject is not None:
        payload["sub"] = subject

    if claims:
        payload.update(claims)

    headers = {"kid": kid or _TEST_KID}

    return jwt.encode(
        payload,
        _PRIVATE_KEY,
        algorithm="RS256",
        headers=headers,
    )


def _mock_jwks_client() -> MagicMock:
    """Create a mock PyJWKClient that returns our test public key."""
    mock_client = MagicMock(spec=jwt.PyJWKClient)
    mock_signing_key = MagicMock()
    mock_signing_key.key = _PUBLIC_KEY
    mock_client.get_signing_key_from_jwt.return_value = mock_signing_key
    return mock_client


def _make_provider(
    issuer: str = _TEST_ISSUER,
    audience: str = _TEST_AUDIENCE,
    tenant_id: str | None = None,
) -> EntraAuthProvider:
    """Create an EntraAuthProvider with a mocked JWKS client."""
    return EntraAuthProvider(
        issuer=issuer,
        audience=audience,
        tenant_id=tenant_id,
        jwks_client=_mock_jwks_client(),
    )


def _make_app(provider: EntraAuthProvider) -> FastAPI:
    """Create a FastAPI app wired with the given EntraAuthProvider."""
    app = FastAPI()

    def _get_user(request: Request) -> CurrentUser:
        return provider.authenticate(request)

    app.dependency_overrides[get_current_user] = _get_user

    @app.get("/api/me")
    def me(user: CurrentUser = Depends(get_current_user)):
        return {
            "user_id": user.user_id,
            "email": user.email,
            "display_name": user.display_name,
            "roles": user.roles,
        }

    return app


class TestEntraAuthValidToken:
    """Tests for valid token handling."""

    def test_valid_token_resolves_correct_user(self):
        """A valid token with standard claims resolves to the correct user."""
        provider = _make_provider()
        app = _make_app(provider)
        client = TestClient(app)

        token = _create_token(
            claims={
                "preferred_username": "alice@contoso.com",
                "name": "Alice Smith",
                "roles": ["user"],
            }
        )

        response = client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["user_id"] == "user-sub-123"
        assert body["email"] == "alice@contoso.com"
        assert body["display_name"] == "Alice Smith"
        assert body["roles"] == ["user"]

    def test_email_claim_fallback(self):
        """Falls back to 'email' claim when 'preferred_username' is absent."""
        provider = _make_provider()
        app = _make_app(provider)
        client = TestClient(app)

        token = _create_token(
            claims={
                "email": "bob@contoso.com",
                "name": "Bob Jones",
            }
        )

        response = client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["email"] == "bob@contoso.com"


class TestEntraAuthExpiredToken:
    """Tests for expired tokens."""

    def test_expired_token_returns_401(self):
        """An expired token is rejected with 401."""
        provider = _make_provider()
        app = _make_app(provider)
        client = TestClient(app)

        token = _create_token(expired=True)

        response = client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 401


class TestEntraAuthWrongAudience:
    """Tests for audience mismatch."""

    def test_wrong_audience_returns_401(self):
        """A token with wrong audience is rejected with 401."""
        provider = _make_provider()
        app = _make_app(provider)
        client = TestClient(app)

        token = _create_token(audience="api://wrong-app")

        response = client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 401


class TestEntraAuthWrongIssuer:
    """Tests for issuer mismatch."""

    def test_wrong_issuer_returns_401(self):
        """A token from a different issuer is rejected with 401."""
        provider = _make_provider()
        app = _make_app(provider)
        client = TestClient(app)

        token = _create_token(
            issuer="https://login.microsoftonline.com/other-tenant/v2.0"
        )

        response = client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 401


class TestEntraAuthMissingSubject:
    """Tests for missing subject claim."""

    def test_missing_subject_returns_401(self):
        """A token without a 'sub' claim is rejected with 401."""
        provider = _make_provider()
        app = _make_app(provider)
        client = TestClient(app)

        token = _create_token(subject=None)

        response = client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 401


class TestEntraAuthAdminRole:
    """Tests for admin role extraction."""

    def test_admin_role_extracted_from_token(self):
        """Admin role is correctly extracted from the token roles claim."""
        provider = _make_provider()
        app = _make_app(provider)
        client = TestClient(app)

        token = _create_token(
            claims={
                "preferred_username": "admin@contoso.com",
                "name": "Admin User",
                "roles": ["admin", "user"],
            }
        )

        response = client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        body = response.json()
        assert "admin" in body["roles"]
        assert "user" in body["roles"]

    def test_regular_user_no_admin_role(self):
        """A regular user token does not have the admin role."""
        provider = _make_provider()
        app = _make_app(provider)
        client = TestClient(app)

        token = _create_token(
            claims={
                "preferred_username": "user@contoso.com",
                "name": "Regular User",
                "roles": ["user"],
            }
        )

        response = client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        body = response.json()
        assert "admin" not in body["roles"]
        assert body["roles"] == ["user"]

    def test_no_roles_claim_defaults_to_user(self):
        """A token with no roles claim defaults to ['user']."""
        provider = _make_provider()
        app = _make_app(provider)
        client = TestClient(app)

        token = _create_token(
            claims={
                "preferred_username": "norole@contoso.com",
                "name": "No Role User",
            }
        )

        response = client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["roles"] == ["user"]


class TestEntraAuthErrorResponses:
    """Tests that error responses do not reveal token details."""

    def test_token_details_not_in_error_response(self):
        """Authentication errors do not reveal token content."""
        provider = _make_provider()
        app = _make_app(provider)
        client = TestClient(app)

        token = _create_token(expired=True)

        response = client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 401
        body = response.json()
        assert body["detail"] == "Authentication required"
        response_text = response.text
        assert "user-sub-123" not in response_text
        assert token not in response_text

    def test_invalid_token_format_returns_generic_error(self):
        """Malformed tokens get a generic 401 without details."""
        provider = _make_provider()
        app = _make_app(provider)
        client = TestClient(app)

        response = client.get(
            "/api/me", headers={"Authorization": "Bearer not-a-real-jwt"}
        )

        assert response.status_code == 401
        body = response.json()
        assert body["detail"] == "Authentication required"
        assert "not-a-real-jwt" not in response.text

    def test_missing_authorization_header_returns_401(self):
        """Requests without Authorization header get 401."""
        provider = _make_provider()
        app = _make_app(provider)
        client = TestClient(app)

        response = client.get("/api/me")

        assert response.status_code == 401
        assert response.json()["detail"] == "Authentication required"


class TestEntraAuthTenant:
    """Tests for tenant validation."""

    def test_correct_tenant_accepted(self):
        """A token with the correct tenant ID is accepted."""
        provider = _make_provider(tenant_id="test-tenant-id")
        app = _make_app(provider)
        client = TestClient(app)

        token = _create_token(
            claims={
                "tid": "test-tenant-id",
                "preferred_username": "user@contoso.com",
                "name": "Tenant User",
            }
        )

        response = client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        assert response.json()["user_id"] == "user-sub-123"

    def test_wrong_tenant_rejected(self):
        """A token with the wrong tenant ID is rejected."""
        provider = _make_provider(tenant_id="expected-tenant")
        app = _make_app(provider)
        client = TestClient(app)

        token = _create_token(
            claims={
                "tid": "wrong-tenant",
                "preferred_username": "user@contoso.com",
            }
        )

        response = client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 401
