"""Tests for application event history (BM-015)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from boardmatch.api import app
from boardmatch.api.v1.applications import _application_repo, _opportunity_repo
from boardmatch.models import Opportunity, Remuneration


@pytest.fixture(autouse=True)
def _reset_state():
    """Clear repository state between tests."""
    _application_repo._store.clear()
    _application_repo._events.clear()
    _opportunity_repo._store.clear()
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
    yield
    _application_repo._store.clear()
    _application_repo._events.clear()
    _opportunity_repo._store.clear()


@pytest.fixture
def client():
    return TestClient(app)


def _headers(user_id: str = "user-001") -> dict[str, str]:
    return {"X-Dev-User-Id": user_id}


def _create_app(client: TestClient, user_id: str = "user-001") -> str:
    resp = client.post(
        "/api/v1/applications",
        json={"opportunity_id": "opp-1"},
        headers=_headers(user_id),
    )
    assert resp.status_code == 201
    return resp.json()["id"]


class TestCreateEvent:
    def test_create_event_success(self, client: TestClient):
        app_id = _create_app(client)
        resp = client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "applied", "notes": "Submitted online"},
            headers=_headers(),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["application_id"] == app_id
        assert data["previous_stage"] == "researching"
        assert data["new_stage"] == "applied"
        assert data["notes"] == "Submitted online"
        assert "id" in data
        assert "timestamp" in data

    def test_create_event_updates_application_stage(self, client: TestClient):
        app_id = _create_app(client)
        client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "applied"},
            headers=_headers(),
        )
        resp = client.get(f"/api/v1/applications/{app_id}", headers=_headers())
        assert resp.json()["stage"] == "applied"

    def test_invalid_transition_rejected(self, client: TestClient):
        app_id = _create_app(client)
        resp = client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "offered"},
            headers=_headers(),
        )
        assert resp.status_code == 422
        assert "Invalid transition" in resp.json()["message"]

    def test_same_stage_rejected(self, client: TestClient):
        app_id = _create_app(client)
        resp = client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "researching"},
            headers=_headers(),
        )
        assert resp.status_code == 422
        assert "must differ" in resp.json()["message"]

    def test_invalid_stage_value_rejected(self, client: TestClient):
        app_id = _create_app(client)
        resp = client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "nonexistent"},
            headers=_headers(),
        )
        assert resp.status_code == 422

    def test_application_not_found(self, client: TestClient):
        resp = client.post(
            "/api/v1/applications/nonexistent/events",
            json={"new_stage": "applied"},
            headers=_headers(),
        )
        assert resp.status_code == 404

    def test_requires_auth(self, client: TestClient):
        resp = client.post(
            "/api/v1/applications/some-id/events",
            json={"new_stage": "applied"},
        )
        assert resp.status_code == 401

    def test_closed_is_terminal(self, client: TestClient):
        app_id = _create_app(client)
        client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "closed"},
            headers=_headers(),
        )
        resp = client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "researching"},
            headers=_headers(),
        )
        assert resp.status_code == 422
        assert "Invalid transition" in resp.json()["message"]


class TestListEvents:
    def test_empty_events(self, client: TestClient):
        app_id = _create_app(client)
        resp = client.get(f"/api/v1/applications/{app_id}/events", headers=_headers())
        assert resp.status_code == 200
        assert resp.json() == {"events": []}

    def test_list_events_chronological(self, client: TestClient):
        app_id = _create_app(client)
        client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "outreach_sent"},
            headers=_headers(),
        )
        client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "applied"},
            headers=_headers(),
        )
        client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "interviewing"},
            headers=_headers(),
        )
        resp = client.get(f"/api/v1/applications/{app_id}/events", headers=_headers())
        assert resp.status_code == 200
        events = resp.json()["events"]
        assert len(events) == 3
        assert events[0]["new_stage"] == "outreach_sent"
        assert events[1]["new_stage"] == "applied"
        assert events[2]["new_stage"] == "interviewing"

    def test_application_not_found(self, client: TestClient):
        resp = client.get("/api/v1/applications/nonexistent/events", headers=_headers())
        assert resp.status_code == 404

    def test_requires_auth(self, client: TestClient):
        resp = client.get("/api/v1/applications/some-id/events")
        assert resp.status_code == 401


class TestAutoEventOnPatch:
    def test_patch_creates_event(self, client: TestClient):
        app_id = _create_app(client)
        client.patch(
            f"/api/v1/applications/{app_id}",
            json={"stage": "applied"},
            headers=_headers(),
        )
        resp = client.get(f"/api/v1/applications/{app_id}/events", headers=_headers())
        events = resp.json()["events"]
        assert len(events) == 1
        assert events[0]["previous_stage"] == "researching"
        assert events[0]["new_stage"] == "applied"

    def test_patch_notes_only_no_event(self, client: TestClient):
        app_id = _create_app(client)
        client.patch(
            f"/api/v1/applications/{app_id}",
            json={"notes": "Updated"},
            headers=_headers(),
        )
        resp = client.get(f"/api/v1/applications/{app_id}/events", headers=_headers())
        assert resp.json()["events"] == []

    def test_patch_same_stage_no_event(self, client: TestClient):
        app_id = _create_app(client)
        client.patch(
            f"/api/v1/applications/{app_id}",
            json={"stage": "researching"},
            headers=_headers(),
        )
        resp = client.get(f"/api/v1/applications/{app_id}/events", headers=_headers())
        assert resp.json()["events"] == []

    def test_patch_invalid_transition_rejected(self, client: TestClient):
        app_id = _create_app(client)
        resp = client.patch(
            f"/api/v1/applications/{app_id}",
            json={"stage": "offered"},
            headers=_headers(),
        )
        assert resp.status_code == 422
        assert "Invalid transition" in resp.json()["message"]


class TestStageTransitionMap:
    def test_researching_to_outreach_sent(self, client: TestClient):
        app_id = _create_app(client)
        resp = client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "outreach_sent"},
            headers=_headers(),
        )
        assert resp.status_code == 201

    def test_researching_to_applied(self, client: TestClient):
        app_id = _create_app(client)
        resp = client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "applied"},
            headers=_headers(),
        )
        assert resp.status_code == 201

    def test_researching_to_closed(self, client: TestClient):
        app_id = _create_app(client)
        resp = client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "closed"},
            headers=_headers(),
        )
        assert resp.status_code == 201

    def test_researching_to_interviewing_invalid(self, client: TestClient):
        app_id = _create_app(client)
        resp = client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "interviewing"},
            headers=_headers(),
        )
        assert resp.status_code == 422

    def test_applied_to_interviewing(self, client: TestClient):
        app_id = _create_app(client)
        client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "applied"},
            headers=_headers(),
        )
        resp = client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "interviewing"},
            headers=_headers(),
        )
        assert resp.status_code == 201

    def test_interviewing_to_offered(self, client: TestClient):
        app_id = _create_app(client)
        client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "applied"},
            headers=_headers(),
        )
        client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "interviewing"},
            headers=_headers(),
        )
        resp = client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "offered"},
            headers=_headers(),
        )
        assert resp.status_code == 201

    def test_offered_to_closed(self, client: TestClient):
        app_id = _create_app(client)
        client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "applied"},
            headers=_headers(),
        )
        client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "interviewing"},
            headers=_headers(),
        )
        client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "offered"},
            headers=_headers(),
        )
        resp = client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "closed"},
            headers=_headers(),
        )
        assert resp.status_code == 201


class TestUserIsolationEvents:
    def test_cannot_see_other_users_events(self, client: TestClient):
        app_id = _create_app(client, "user-a")
        client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "applied"},
            headers=_headers("user-a"),
        )
        resp = client.get(
            f"/api/v1/applications/{app_id}/events", headers=_headers("user-b")
        )
        assert resp.status_code == 404

    def test_cannot_create_events_on_other_users_app(self, client: TestClient):
        app_id = _create_app(client, "user-a")
        resp = client.post(
            f"/api/v1/applications/{app_id}/events",
            json={"new_stage": "applied"},
            headers=_headers("user-b"),
        )
        assert resp.status_code == 404


class TestEventImmutability:
    def test_no_patch_on_events(self, client: TestClient):
        app_id = _create_app(client)
        resp = client.patch(
            f"/api/v1/applications/{app_id}/events",
            json={"notes": "changed"},
            headers=_headers(),
        )
        assert resp.status_code == 405

    def test_no_delete_on_events(self, client: TestClient):
        app_id = _create_app(client)
        resp = client.delete(
            f"/api/v1/applications/{app_id}/events", headers=_headers()
        )
        assert resp.status_code == 405
