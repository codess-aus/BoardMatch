"""Tests for network connections API (BM-026)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from boardmatch.api import app
from boardmatch.api.v1 import integrations as integrations_module
from boardmatch.api.v1 import network as network_module
from boardmatch.config import Settings, get_settings
from boardmatch.integrations import InMemoryIntegrationRepository


@pytest.fixture(autouse=True)
def _reset_state():
    original_network_repo = network_module._network_repo
    network_module._network_repo = network_module.InMemoryNetworkRepository()

    original_integration_repo = integrations_module._repository
    original_states = integrations_module._pending_states.copy()
    integrations_module._repository = InMemoryIntegrationRepository()
    integrations_module._pending_states.clear()

    yield

    network_module._network_repo = original_network_repo
    integrations_module._repository = original_integration_repo
    integrations_module._pending_states.clear()
    integrations_module._pending_states.update(original_states)


@pytest.fixture()
def client():
    return TestClient(app)


AUTH_HEADERS = {"X-Dev-User-Id": "user-1"}
AUTH_HEADERS_USER2 = {"X-Dev-User-Id": "user-2"}


def _grant_microsoft_consent(client, headers=None):
    if headers is None:
        headers = AUTH_HEADERS
    auth_resp = client.post("/api/v1/integrations/microsoft/authorize", headers=headers)
    state = auth_resp.json()["state"]
    client.get(
        "/api/v1/integrations/microsoft/callback",
        params={"code": "test-code", "state": state},
    )


class TestListConnections:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/network/connections", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["connections"] == []

    def test_requires_auth(self, client):
        resp = client.get("/api/v1/network/connections")
        assert resp.status_code == 401


class TestSyncConnections:
    def test_sync_requires_consent(self, client):
        resp = client.post("/api/v1/network/sync", headers=AUTH_HEADERS)
        assert resp.status_code == 403
        body = resp.json()
        msg = body.get("detail", body.get("message", ""))
        assert "consent" in msg.lower()

    def test_sync_imports_connections(self, client):
        _grant_microsoft_consent(client)
        resp = client.post("/api/v1/network/sync", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 3
        assert len(data["connections"]) == 3
        for conn in data["connections"]:
            assert conn["approved"] is False
            assert conn["strength"] == 5
            assert conn["source"] == "microsoft_graph"

    def test_sync_connections_appear_in_list(self, client):
        _grant_microsoft_consent(client)
        client.post("/api/v1/network/sync", headers=AUTH_HEADERS)
        resp = client.get("/api/v1/network/connections", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert len(resp.json()["connections"]) == 3

    def test_sync_requires_auth(self, client):
        resp = client.post("/api/v1/network/sync")
        assert resp.status_code == 401

    def test_sync_with_revoked_consent_fails(self, client):
        _grant_microsoft_consent(client)
        client.delete("/api/v1/integrations/microsoft", headers=AUTH_HEADERS)
        resp = client.post("/api/v1/network/sync", headers=AUTH_HEADERS)
        assert resp.status_code == 403


class TestUpdateConnection:
    def test_approve_connection(self, client):
        _grant_microsoft_consent(client)
        sync_resp = client.post("/api/v1/network/sync", headers=AUTH_HEADERS)
        conn_id = sync_resp.json()["connections"][0]["id"]
        resp = client.patch(
            f"/api/v1/network/connections/{conn_id}",
            json={"approved": True},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["approved"] is True

    def test_adjust_strength(self, client):
        _grant_microsoft_consent(client)
        sync_resp = client.post("/api/v1/network/sync", headers=AUTH_HEADERS)
        conn_id = sync_resp.json()["connections"][0]["id"]
        resp = client.patch(
            f"/api/v1/network/connections/{conn_id}",
            json={"strength": 8},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["strength"] == 8

    def test_strength_validation(self, client):
        _grant_microsoft_consent(client)
        sync_resp = client.post("/api/v1/network/sync", headers=AUTH_HEADERS)
        conn_id = sync_resp.json()["connections"][0]["id"]
        resp = client.patch(
            f"/api/v1/network/connections/{conn_id}",
            json={"strength": 0},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 422
        resp = client.patch(
            f"/api/v1/network/connections/{conn_id}",
            json={"strength": 11},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 422

    def test_update_not_found(self, client):
        resp = client.patch(
            "/api/v1/network/connections/nonexistent",
            json={"approved": True},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 404

    def test_cannot_update_other_users_connection(self, client):
        _grant_microsoft_consent(client)
        sync_resp = client.post("/api/v1/network/sync", headers=AUTH_HEADERS)
        conn_id = sync_resp.json()["connections"][0]["id"]
        resp = client.patch(
            f"/api/v1/network/connections/{conn_id}",
            json={"approved": True},
            headers=AUTH_HEADERS_USER2,
        )
        assert resp.status_code == 404


class TestDeleteConnection:
    def test_delete_connection(self, client):
        _grant_microsoft_consent(client)
        sync_resp = client.post("/api/v1/network/sync", headers=AUTH_HEADERS)
        conn_id = sync_resp.json()["connections"][0]["id"]
        resp = client.delete(
            f"/api/v1/network/connections/{conn_id}", headers=AUTH_HEADERS
        )
        assert resp.status_code == 204

    def test_deleted_not_in_list(self, client):
        _grant_microsoft_consent(client)
        sync_resp = client.post("/api/v1/network/sync", headers=AUTH_HEADERS)
        conn_id = sync_resp.json()["connections"][0]["id"]
        client.delete(f"/api/v1/network/connections/{conn_id}", headers=AUTH_HEADERS)
        resp = client.get("/api/v1/network/connections", headers=AUTH_HEADERS)
        ids = [c["id"] for c in resp.json()["connections"]]
        assert conn_id not in ids
        assert len(resp.json()["connections"]) == 2

    def test_delete_not_found(self, client):
        resp = client.delete(
            "/api/v1/network/connections/nonexistent", headers=AUTH_HEADERS
        )
        assert resp.status_code == 404

    def test_cannot_delete_other_users_connection(self, client):
        _grant_microsoft_consent(client)
        sync_resp = client.post("/api/v1/network/sync", headers=AUTH_HEADERS)
        conn_id = sync_resp.json()["connections"][0]["id"]
        resp = client.delete(
            f"/api/v1/network/connections/{conn_id}", headers=AUTH_HEADERS_USER2
        )
        assert resp.status_code == 404


class TestIntroPaths:
    def test_no_paths_without_approved_connections(self, client):
        _grant_microsoft_consent(client)
        client.post("/api/v1/network/sync", headers=AUTH_HEADERS)
        from boardmatch import discovery

        opps = discovery.discover()
        if not opps:
            pytest.skip("No opportunities available")
        opp_id = opps[0].id
        resp = client.get(
            f"/api/v1/opportunities/{opp_id}/intro-paths", headers=AUTH_HEADERS
        )
        assert resp.status_code == 200
        assert resp.json()["paths"] == []

    def test_approved_connections_generate_paths(self, client):
        _grant_microsoft_consent(client)
        sync_resp = client.post("/api/v1/network/sync", headers=AUTH_HEADERS)
        for conn in sync_resp.json()["connections"]:
            cid = conn["id"]
            client.patch(
                f"/api/v1/network/connections/{cid}",
                json={"approved": True},
                headers=AUTH_HEADERS,
            )
        from boardmatch import discovery

        opps = discovery.discover()
        if not opps:
            pytest.skip("No opportunities available")
        opp_id = opps[0].id
        resp = client.get(
            f"/api/v1/opportunities/{opp_id}/intro-paths", headers=AUTH_HEADERS
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["opportunity_id"] == opp_id
        assert isinstance(data["paths"], list)

    def test_deleted_connections_excluded(self, client):
        _grant_microsoft_consent(client)
        sync_resp = client.post("/api/v1/network/sync", headers=AUTH_HEADERS)
        connections = sync_resp.json()["connections"]
        for conn in connections:
            cid = conn["id"]
            client.patch(
                f"/api/v1/network/connections/{cid}",
                json={"approved": True},
                headers=AUTH_HEADERS,
            )
        first_id = connections[0]["id"]
        client.delete(
            f"/api/v1/network/connections/{first_id}",
            headers=AUTH_HEADERS,
        )
        from boardmatch import discovery

        opps = discovery.discover()
        if not opps:
            pytest.skip("No opportunities available")
        opp_id = opps[0].id
        resp = client.get(
            f"/api/v1/opportunities/{opp_id}/intro-paths", headers=AUTH_HEADERS
        )
        assert resp.status_code == 200
        deleted_name = connections[0]["name"]
        for path in resp.json()["paths"]:
            assert path["connection_name"] != deleted_name

    def test_opportunity_not_found(self, client):
        resp = client.get(
            "/api/v1/opportunities/nonexistent/intro-paths", headers=AUTH_HEADERS
        )
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.get("/api/v1/opportunities/any-id/intro-paths")
        assert resp.status_code == 401


class TestUserIsolation:
    def test_user_isolation(self, client):
        _grant_microsoft_consent(client)
        client.post("/api/v1/network/sync", headers=AUTH_HEADERS)
        resp = client.get("/api/v1/network/connections", headers=AUTH_HEADERS_USER2)
        assert resp.status_code == 200
        assert resp.json()["connections"] == []
        resp = client.get("/api/v1/network/connections", headers=AUTH_HEADERS)
        assert len(resp.json()["connections"]) == 3


class TestRealGraphSync:
    """When the Microsoft integration has a real access token (i.e. real
    Graph OAuth is configured and the exchange succeeded), sync calls the
    real Microsoft Graph /me/people endpoint instead of using fixtures. All
    HTTP calls are mocked — no real network traffic is generated."""

    @pytest.fixture(autouse=True)
    def _configure_real_graph(self):
        settings = Settings(
            ms_graph_client_id="real-client-id",
            ms_graph_client_secret="real-client-secret",
        )
        app.dependency_overrides[get_settings] = lambda: settings
        yield
        app.dependency_overrides.pop(get_settings, None)

    def _grant_with_real_token(self, client, access_token="graph-token-123"):
        auth_resp = client.post(
            "/api/v1/integrations/microsoft/authorize", headers=AUTH_HEADERS
        )
        state = auth_resp.json()["state"]

        mock_token_response = MagicMock()
        mock_token_response.json.return_value = {"access_token": access_token}
        mock_token_response.raise_for_status.return_value = None
        with patch("requests.post", return_value=mock_token_response):
            client.get(
                "/api/v1/integrations/microsoft/callback",
                params={"code": "auth-code", "state": state},
            )

    def test_sync_calls_graph_people_endpoint(self, client):
        self._grant_with_real_token(client)

        mock_graph_response = MagicMock()
        mock_graph_response.json.return_value = {
            "value": [
                {
                    "displayName": "Alex Rivera",
                    "jobTitle": "Chief Risk Officer",
                    "companyName": "Northbridge Bank",
                }
            ]
        }
        mock_graph_response.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_graph_response) as mock_get:
            resp = client.post("/api/v1/network/sync", headers=AUTH_HEADERS)

        assert resp.status_code == 200
        mock_get.assert_called_once()
        url = mock_get.call_args.args[0]
        assert url == "https://graph.microsoft.com/v1.0/me/people"
        headers = mock_get.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer graph-token-123"

        data = resp.json()
        assert data["imported"] == 1
        assert data["connections"][0]["name"] == "Alex Rivera"
        assert data["connections"][0]["relationship"] == "Chief Risk Officer"
        assert data["connections"][0]["organisations"] == ["Northbridge Bank"]
        assert data["connections"][0]["source"] == "microsoft_graph"

    def test_sync_falls_back_to_fixtures_on_graph_failure(self, client):
        self._grant_with_real_token(client)

        with patch("requests.get", side_effect=ConnectionError("network down")):
            resp = client.post("/api/v1/network/sync", headers=AUTH_HEADERS)

        # Sync still succeeds, using the deterministic fixture connections.
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 3
        names = {c["name"] for c in data["connections"]}
        assert "Sarah Chen" in names

    def test_sync_without_real_token_uses_fixtures(self, client):
        # No configured client secret in this case → simulated token → no
        # access_token stored → fixtures used (existing deterministic path).
        app.dependency_overrides.pop(get_settings, None)
        _grant_microsoft_consent(client)
        resp = client.post("/api/v1/network/sync", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["imported"] == 3
