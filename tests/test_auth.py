"""Tests for BM-008: Authentication abstraction."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from boardmatch.auth import (
    AuthProvider,
    CurrentUser,
    DevAuthProvider,
    get_current_user,
)
from boardmatch.config import AppEnvironment, Settings


def _make_app(settings: Settings | None = None) -> FastAPI:
    """Create a minimal FastAPI app with auth wired up for testing."""
    if settings is None:
        settings = Settings(app_env=AppEnvironment.LOCAL)

    def _override_settings() -> Settings:
        return settings

    test_app = FastAPI()
    test_app.dependency_overrides[get_current_user] = lambda: None  # clear first

    # Wire the real dependency chain with overridden settings
    def _get_user(request) -> CurrentUser:
        from boardmatch.auth import _get_provider

        provider = _get_provider(settings)
        return provider.authenticate(request)

    @test_app.get("/api/me")
    def me(user: CurrentUser = Depends(get_current_user)):
        return {
            "user_id": user.user_id,
            "email": user.email,
            "display_name": user.display_name,
            "roles": user.roles,
        }

    # Override settings dependency so the auth chain uses our settings
    from boardmatch.config import get_settings

    test_app.dependency_overrides[get_settings] = _override_settings
    # Remove the get_current_user override so the real chain runs
    test_app.dependency_overrides.pop(get_current_user, None)

    return test_app


class TestDevAuthProvider:
    """Tests for DevAuthProvider in LOCAL/TEST environments."""

    def test_valid_dev_header_resolves_user(self):
        """Valid X-Dev-User-Id header resolves to the correct user."""
        app = _make_app(Settings(app_env=AppEnvironment.LOCAL))
        client = TestClient(app)

        response = client.get("/api/me", headers={"X-Dev-User-Id": "alice-123"})

        assert response.status_code == 200
        body = response.json()
        assert body["user_id"] == "alice-123"
        assert body["email"] == "alice-123@boardmatch.local"
        assert body["display_name"] == "alice-123"
        assert body["roles"] == ["user"]

    def test_missing_header_uses_default_dev_user(self):
        """Missing header returns the fixed default development user."""
        app = _make_app(Settings(app_env=AppEnvironment.LOCAL))
        client = TestClient(app)

        response = client.get("/api/me")

        assert response.status_code == 200
        body = response.json()
        assert body["user_id"] == "dev-user-001"
        assert body["email"] == "dev@boardmatch.local"
        assert body["display_name"] == "Development User"

    def test_dev_auth_works_in_test_environment(self):
        """DevAuthProvider is also active in the TEST environment."""
        app = _make_app(Settings(app_env=AppEnvironment.TEST))
        client = TestClient(app)

        response = client.get("/api/me", headers={"X-Dev-User-Id": "test-user"})

        assert response.status_code == 200
        assert response.json()["user_id"] == "test-user"


class TestProductionAuth:
    """Tests for authentication in PRODUCTION environment."""

    @pytest.fixture
    def production_settings(self) -> Settings:
        """Valid production settings (all required fields populated)."""
        return Settings(
            app_env=AppEnvironment.PRODUCTION,
            database_url="postgresql://host/db",
            auth_issuer="https://login.microsoftonline.com/tenant/v2.0",
            auth_audience="api://boardmatch",
            azure_openai_endpoint="https://oai.openai.azure.com",
            azure_openai_api_key="secret-key",
            azure_openai_deployment="gpt-4",
            azure_storage_account="bmstorageaccount",
        )

    def test_unauthenticated_request_returns_401(self, production_settings):
        """Requests without auth in production get 401."""
        app = _make_app(production_settings)
        client = TestClient(app)

        response = client.get("/api/me")

        assert response.status_code == 401
        assert response.json()["detail"] == "Authentication required"

    def test_dev_header_rejected_in_production(self, production_settings):
        """X-Dev-User-Id header is ignored in production (still 401)."""
        app = _make_app(production_settings)
        client = TestClient(app)

        response = client.get("/api/me", headers={"X-Dev-User-Id": "hacker"})

        assert response.status_code == 401

    def test_www_authenticate_header_present(self, production_settings):
        """401 responses include WWW-Authenticate header."""
        app = _make_app(production_settings)
        client = TestClient(app)

        response = client.get("/api/me")

        assert response.headers.get("WWW-Authenticate") == "Bearer"


class TestAuthProtocol:
    """Tests that AuthProvider protocol works correctly."""

    def test_dev_provider_satisfies_protocol(self):
        """DevAuthProvider is a valid AuthProvider implementation."""
        provider = DevAuthProvider()
        assert isinstance(provider, AuthProvider)

    def test_custom_provider_injectable(self):
        """A custom AuthProvider can be injected via dependency override."""

        class MockProvider:
            def authenticate(self, request) -> CurrentUser:
                return CurrentUser(
                    user_id="mock-user",
                    email="mock@test.com",
                    display_name="Mock User",
                    roles=["admin"],
                )

        app = FastAPI()

        @app.get("/api/me")
        def me(user: CurrentUser = Depends(get_current_user)):
            return {
                "user_id": user.user_id,
                "email": user.email,
                "roles": user.roles,
            }

        mock_user = CurrentUser(
            user_id="injected-user",
            email="injected@test.com",
            display_name="Injected",
            roles=["admin"],
        )
        app.dependency_overrides[get_current_user] = lambda: mock_user

        client = TestClient(app)
        response = client.get("/api/me")

        assert response.status_code == 200
        assert response.json()["user_id"] == "injected-user"
        assert response.json()["roles"] == ["admin"]


class TestCurrentUserModel:
    """Tests for the CurrentUser Pydantic model."""

    def test_minimal_user(self):
        """CurrentUser can be created with just user_id."""
        user = CurrentUser(user_id="u1")
        assert user.user_id == "u1"
        assert user.email is None
        assert user.display_name is None
        assert user.roles == []

    def test_full_user(self):
        """CurrentUser with all fields populated."""
        user = CurrentUser(
            user_id="u2",
            email="test@example.com",
            display_name="Test User",
            roles=["user", "admin"],
        )
        assert user.user_id == "u2"
        assert user.email == "test@example.com"
        assert user.roles == ["user", "admin"]
