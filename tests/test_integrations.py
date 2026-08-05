"""Tests for consent and integration records (BM-025)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from boardmatch.api import app
from boardmatch.api.v1 import integrations as integrations_module
from boardmatch.integrations import (
    AuditEventType,
    InMemoryIntegrationRepository,
    Integration,
    IntegrationStatus,
)


@pytest.fixture(autouse=True)
def _reset_integration_state():
    """Reset module-level state between tests."""
    original_repo = integrations_module._repository
    original_states = integrations_module._pending_states.copy()
    integrations_module._repository = InMemoryIntegrationRepository()
    integrations_module._pending_states.clear()
    yield
    integrations_module._repository = original_repo
    integrations_module._pending_states.clear()
    integrations_module._pending_states.update(original_states)


@pytest.fixture()
def client():
    return TestClient(app)


AUTH_HEADERS = {"X-Dev-User-Id": "user-1"}
AUTH_HEADERS_USER2 = {"X-Dev-User-Id": "user-2"}


class TestListIntegrations:
    """GET /api/v1/integrations"""

    def test_list_empty(self, client: TestClient):
        """Returns empty list when user has no integrations."""
        resp = client.get("/api/v1/integrations", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["integrations"] == []

    def test_list_shows_active_integration(self, client: TestClient):
        """After granting consent, the integration appears in the list."""
        # Create integration via authorize + callback
        auth_resp = client.post(
            "/api/v1/integrations/microsoft/authorize", headers=AUTH_HEADERS
        )
        state = auth_resp.json()["state"]
        client.get(
            "/api/v1/integrations/microsoft/callback",
            params={"code": "test-code", "state": state},
        )

        resp = client.get("/api/v1/integrations", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        integrations = resp.json()["integrations"]
        assert len(integrations) == 1
        assert integrations[0]["provider"] == "microsoft"
        assert integrations[0]["status"] == "active"


class TestAuthorize:
    """POST /api/v1/integrations/microsoft/authorize"""

    def test_authorize_returns_url(self, client: TestClient):
        """Returns an authorization URL with required OAuth params."""
        resp = client.post(
            "/api/v1/integrations/microsoft/authorize", headers=AUTH_HEADERS
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "authorize_url" in data
        assert "state" in data
        assert "login.microsoftonline.com" in data["authorize_url"]
        assert "client_id=" in data["authorize_url"]
        assert "scope=" in data["authorize_url"]

    def test_authorize_requires_auth(self, client: TestClient):
        """Rejects unauthenticated requests."""
        resp = client.post("/api/v1/integrations/microsoft/authorize")
        assert resp.status_code == 401


class TestCallback:
    """GET /api/v1/integrations/microsoft/callback"""

    def test_callback_creates_integration(self, client: TestClient):
        """Valid callback creates an active integration record."""
        auth_resp = client.post(
            "/api/v1/integrations/microsoft/authorize", headers=AUTH_HEADERS
        )
        state = auth_resp.json()["state"]

        resp = client.get(
            "/api/v1/integrations/microsoft/callback",
            params={"code": "auth-code-123", "state": state},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "active"
        assert data["provider"] == "microsoft"
        assert "User.Read" in data["scopes"]

    def test_callback_invalid_state(self, client: TestClient):
        """Rejects callback with invalid state token."""
        resp = client.get(
            "/api/v1/integrations/microsoft/callback",
            params={"code": "some-code", "state": "invalid-state"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "Invalid or expired state" in body.get("detail", body.get("message", ""))


class TestConsentPersistence:
    """Scopes are properly stored and visible."""

    def test_scopes_stored(self, client: TestClient):
        """Integration record persists the granted scopes."""
        auth_resp = client.post(
            "/api/v1/integrations/microsoft/authorize", headers=AUTH_HEADERS
        )
        state = auth_resp.json()["state"]
        client.get(
            "/api/v1/integrations/microsoft/callback",
            params={"code": "code-1", "state": state},
        )

        resp = client.get("/api/v1/integrations", headers=AUTH_HEADERS)
        scopes = resp.json()["integrations"][0]["scopes"]
        assert "User.Read" in scopes
        assert "Calendars.Read" in scopes
        assert "Mail.Read" in scopes


class TestRevocation:
    """DELETE /api/v1/integrations/microsoft"""

    def test_revoke_disables_integration(self, client: TestClient):
        """Revoking sets status to revoked and records revoked_at."""
        # Grant first
        auth_resp = client.post(
            "/api/v1/integrations/microsoft/authorize", headers=AUTH_HEADERS
        )
        state = auth_resp.json()["state"]
        client.get(
            "/api/v1/integrations/microsoft/callback",
            params={"code": "code-2", "state": state},
        )

        # Revoke
        resp = client.delete(
            "/api/v1/integrations/microsoft", headers=AUTH_HEADERS
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "revoked"
        assert data["revoked_at"] is not None

    def test_revoke_not_found(self, client: TestClient):
        """Revoking non-existent integration returns 404."""
        resp = client.delete(
            "/api/v1/integrations/microsoft", headers=AUTH_HEADERS
        )
        assert resp.status_code == 404

    def test_revoked_integration_rejects_sync(self, client: TestClient):
        """A revoked integration is visible as revoked (sync would check status)."""
        # Grant
        auth_resp = client.post(
            "/api/v1/integrations/microsoft/authorize", headers=AUTH_HEADERS
        )
        state = auth_resp.json()["state"]
        client.get(
            "/api/v1/integrations/microsoft/callback",
            params={"code": "code-3", "state": state},
        )
        # Revoke
        client.delete("/api/v1/integrations/microsoft", headers=AUTH_HEADERS)

        # Verify status is revoked — any sync attempt should check this
        resp = client.get("/api/v1/integrations", headers=AUTH_HEADERS)
        integration = resp.json()["integrations"][0]
        assert integration["status"] == "revoked"
        assert integration["revoked_at"] is not None

    def test_double_revoke_rejected(self, client: TestClient):
        """Cannot revoke an already-revoked integration."""
        auth_resp = client.post(
            "/api/v1/integrations/microsoft/authorize", headers=AUTH_HEADERS
        )
        state = auth_resp.json()["state"]
        client.get(
            "/api/v1/integrations/microsoft/callback",
            params={"code": "code-4", "state": state},
        )
        client.delete("/api/v1/integrations/microsoft", headers=AUTH_HEADERS)

        resp = client.delete(
            "/api/v1/integrations/microsoft", headers=AUTH_HEADERS
        )
        assert resp.status_code == 400


class TestUserIsolation:
    """Users can only see their own integrations."""

    def test_user_isolation(self, client: TestClient):
        """User 2 cannot see User 1's integrations."""
        # User 1 grants consent
        auth_resp = client.post(
            "/api/v1/integrations/microsoft/authorize", headers=AUTH_HEADERS
        )
        state = auth_resp.json()["state"]
        client.get(
            "/api/v1/integrations/microsoft/callback",
            params={"code": "code-5", "state": state},
        )

        # User 2 sees nothing
        resp = client.get("/api/v1/integrations", headers=AUTH_HEADERS_USER2)
        assert resp.status_code == 200
        assert resp.json()["integrations"] == []

        # User 1 sees their integration
        resp = client.get("/api/v1/integrations", headers=AUTH_HEADERS)
        assert len(resp.json()["integrations"]) == 1


class TestAuditEvents:
    """Audit events are recorded on grant and revoke."""

    def test_audit_event_on_grant(self, client: TestClient):
        """Granting consent creates an audit event."""
        auth_resp = client.post(
            "/api/v1/integrations/microsoft/authorize", headers=AUTH_HEADERS
        )
        state = auth_resp.json()["state"]
        client.get(
            "/api/v1/integrations/microsoft/callback",
            params={"code": "code-6", "state": state},
        )

        repo = integrations_module._repository
        events = repo.get_audit_events("user-1")
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.CONSENT_GRANTED
        assert events[0].provider == "microsoft"
        assert "User.Read" in events[0].scopes

    def test_audit_event_on_revoke(self, client: TestClient):
        """Revoking consent creates an audit event."""
        auth_resp = client.post(
            "/api/v1/integrations/microsoft/authorize", headers=AUTH_HEADERS
        )
        state = auth_resp.json()["state"]
        client.get(
            "/api/v1/integrations/microsoft/callback",
            params={"code": "code-7", "state": state},
        )
        client.delete("/api/v1/integrations/microsoft", headers=AUTH_HEADERS)

        repo = integrations_module._repository
        events = repo.get_audit_events("user-1")
        assert len(events) == 2
        assert events[0].event_type == AuditEventType.CONSENT_GRANTED
        assert events[1].event_type == AuditEventType.CONSENT_REVOKED


class TestTokenSecurity:
    """Tokens must not be stored in plain text."""

    def test_token_not_plain_text(self, client: TestClient):
        """The stored token_hash is a hash, not the raw token."""
        auth_resp = client.post(
            "/api/v1/integrations/microsoft/authorize", headers=AUTH_HEADERS
        )
        state = auth_resp.json()["state"]
        client.get(
            "/api/v1/integrations/microsoft/callback",
            params={"code": "my-secret-code", "state": state},
        )

        repo = integrations_module._repository
        integration = repo.get("user-1", "microsoft")
        assert integration is not None
        assert integration.token_hash is not None
        # Token hash should NOT contain the original code or simulated token
        assert "my-secret-code" not in integration.token_hash
        assert "access_token_for_" not in integration.token_hash
        # Should be a hex hash (sha256 = 64 chars)
        assert len(integration.token_hash) == 64
