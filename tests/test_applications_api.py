"""Tests for the applications CRUD API (BM-014)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from boardmatch import discovery
from boardmatch.api import app
from boardmatch.api.v1.applications import _application_repo, _opportunity_repo
from boardmatch.models import Opportunity, Remuneration


@pytest.fixture(autouse=True)
def _reset_state():
    """Clear repository state between tests."""
    _application_repo._store.clear()
    _opportunity_repo._store.clear()
    # Seed a default opportunity for tests
    _opportunity_repo.add(
        Opportunity(
            id="opp-1",
            title="Board Director",
            organisation="Acme Corp",
            sector="Technology",
            location="Melbourne",
            source="manual",
            url="https://example.com",
            remuneration=Remuneration.PAID,
            fee_aud=50000,
        )
    )
    _opportunity_repo.add(
        Opportunity(
            id="opp-2",
            title="NED",
            organisation="Beta Inc",
            sector="Finance",
            location="Sydney",
            source="manual",
            url="https://example.com/2",
            remuneration=Remuneration.VOLUNTARY,
        )
    )
    yield
    _application_repo._store.clear()
    _opportunity_repo._store.clear()
    # Restore the discovery-seeded opportunities so other test modules that
    # share this same in-memory repo instance see it in its default state.
    for opportunity in discovery.discover():
        _opportunity_repo.add(opportunity)


@pytest.fixture
def client():
    return TestClient(app)


def _headers(user_id: str = "user-001") -> dict[str, str]:
    return {"X-Dev-User-Id": user_id}


class TestListApplications:
    """GET /api/v1/applications"""

    def test_empty_list(self, client: TestClient):
        resp = client.get("/api/v1/applications", headers=_headers())
        assert resp.status_code == 200
        assert resp.json() == {"applications": []}

    def test_returns_created_applications(self, client: TestClient):
        client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        resp = client.get("/api/v1/applications", headers=_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["applications"]) == 1
        assert data["applications"][0]["opportunity_id"] == "opp-1"

    def test_requires_auth(self, client: TestClient):
        resp = client.get("/api/v1/applications")
        assert resp.status_code == 401


class TestCreateApplication:
    """POST /api/v1/applications"""

    def test_create_success(self, client: TestClient):
        resp = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1", "notes": "Interesting role"},
            headers=_headers(),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["opportunity_id"] == "opp-1"
        assert data["stage"] == "researching"
        assert data["notes"] == "Interesting role"
        assert "id" in data

    def test_create_with_stage(self, client: TestClient):
        resp = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1", "stage": "applied"},
            headers=_headers(),
        )
        assert resp.status_code == 201
        assert resp.json()["stage"] == "applied"

    def test_reject_duplicate_opportunity(self, client: TestClient):
        client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        resp = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["message"]

    def test_different_opportunities_allowed(self, client: TestClient):
        resp1 = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        resp2 = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-2"},
            headers=_headers(),
        )
        assert resp1.status_code == 201
        assert resp2.status_code == 201

    def test_unknown_opportunity_rejected(self, client: TestClient):
        resp = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "nonexistent"},
            headers=_headers(),
        )
        assert resp.status_code == 404
        assert "Opportunity not found" in resp.json()["message"]

    def test_invalid_stage_rejected(self, client: TestClient):
        resp = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1", "stage": "invalid_stage"},
            headers=_headers(),
        )
        assert resp.status_code == 422

    def test_requires_auth(self, client: TestClient):
        resp = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1"},
        )
        assert resp.status_code == 401


class TestGetApplication:
    """GET /api/v1/applications/{application_id}"""

    def test_get_existing(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        app_id = create_resp.json()["id"]
        resp = client.get(f"/api/v1/applications/{app_id}", headers=_headers())
        assert resp.status_code == 200
        assert resp.json()["id"] == app_id

    def test_not_found(self, client: TestClient):
        resp = client.get("/api/v1/applications/nonexistent", headers=_headers())
        assert resp.status_code == 404


class TestUpdateApplication:
    """PATCH /api/v1/applications/{application_id}"""

    def test_update_stage(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        app_id = create_resp.json()["id"]
        resp = client.patch(
            f"/api/v1/applications/{app_id}",
            json={"stage": "applied"},
            headers=_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["stage"] == "applied"

    def test_update_notes(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        app_id = create_resp.json()["id"]
        resp = client.patch(
            f"/api/v1/applications/{app_id}",
            json={"notes": "Updated notes"},
            headers=_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["notes"] == "Updated notes"

    def test_update_both(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        app_id = create_resp.json()["id"]
        resp = client.patch(
            f"/api/v1/applications/{app_id}",
            json={"stage": "applied", "notes": "Going well"},
            headers=_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"] == "applied"
        assert data["notes"] == "Going well"

    def test_invalid_stage(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        app_id = create_resp.json()["id"]
        resp = client.patch(
            f"/api/v1/applications/{app_id}",
            json={"stage": "bad_stage"},
            headers=_headers(),
        )
        assert resp.status_code == 422

    def test_not_found(self, client: TestClient):
        resp = client.patch(
            "/api/v1/applications/nonexistent",
            json={"notes": "x"},
            headers=_headers(),
        )
        assert resp.status_code == 404


class TestDeleteApplication:
    """DELETE /api/v1/applications/{application_id}"""

    def test_delete_existing(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        app_id = create_resp.json()["id"]
        resp = client.delete(f"/api/v1/applications/{app_id}", headers=_headers())
        assert resp.status_code == 204

        # Verify it's gone
        resp = client.get(f"/api/v1/applications/{app_id}", headers=_headers())
        assert resp.status_code == 404

    def test_delete_not_found(self, client: TestClient):
        resp = client.delete("/api/v1/applications/nonexistent", headers=_headers())
        assert resp.status_code == 404


class TestUserIsolation:
    """Users cannot access other users' applications."""

    def test_cannot_see_other_users_applications(self, client: TestClient):
        # User A creates an application
        create_resp = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1"},
            headers=_headers("user-a"),
        )
        app_id = create_resp.json()["id"]

        # User B cannot see it
        resp = client.get(f"/api/v1/applications/{app_id}", headers=_headers("user-b"))
        assert resp.status_code == 404

    def test_cannot_list_other_users_applications(self, client: TestClient):
        client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1"},
            headers=_headers("user-a"),
        )
        resp = client.get("/api/v1/applications", headers=_headers("user-b"))
        assert resp.status_code == 200
        assert resp.json()["applications"] == []

    def test_cannot_update_other_users_application(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1"},
            headers=_headers("user-a"),
        )
        app_id = create_resp.json()["id"]

        resp = client.patch(
            f"/api/v1/applications/{app_id}",
            json={"stage": "applied"},
            headers=_headers("user-b"),
        )
        assert resp.status_code == 404

    def test_cannot_delete_other_users_application(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1"},
            headers=_headers("user-a"),
        )
        app_id = create_resp.json()["id"]

        resp = client.delete(
            f"/api/v1/applications/{app_id}", headers=_headers("user-b")
        )
        assert resp.status_code == 404

    def test_duplicate_check_is_per_user(self, client: TestClient):
        """Different users can apply to the same opportunity."""
        resp_a = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1"},
            headers=_headers("user-a"),
        )
        resp_b = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "opp-1"},
            headers=_headers("user-b"),
        )
        assert resp_a.status_code == 201
        assert resp_b.status_code == 201
