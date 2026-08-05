"""Tests for profile suggestion review and confirmation (BM-023)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from boardmatch.api import app
from boardmatch.api.v1.suggestions import _audit_log, _stale_fits, _suggestion_store
from boardmatch.profile_api import (
    _candidate_repo,
    _profile_statuses,
    _profile_versions,
)
from boardmatch.suggestions import ProfileSuggestion, SuggestionStatus


@pytest.fixture(autouse=True)
def _reset_state():
    """Clear all in-memory state between tests."""
    _candidate_repo._store.clear()
    _profile_versions.clear()
    _profile_statuses.clear()
    _suggestion_store._store.clear()
    _audit_log.clear()
    _stale_fits.clear()
    yield
    _candidate_repo._store.clear()
    _profile_versions.clear()
    _profile_statuses.clear()
    _suggestion_store._store.clear()
    _audit_log.clear()
    _stale_fits.clear()


@pytest.fixture
def client():
    return TestClient(app)


SAMPLE_PROFILE = {
    "name": "Test User",
    "headline": "Engineering leader",
    "years_experience": 10,
    "skills": ["governance", "finance"],
    "sectors": ["technology"],
    "credentials": ["MBA"],
    "board_experience": ["Board member, Startup Inc (2020-2023)"],
    "achievements": ["Led $50M capital raise"],
    "locations": ["Melbourne, VIC"],
    "connections": [],
    "status": "draft",
}

USER_ID = "user-001"


def _headers(user_id: str = USER_ID) -> dict[str, str]:
    return {"X-Dev-User-Id": user_id}


def _seed_profile(client: TestClient, user_id: str = USER_ID) -> None:
    """Create a profile for the given user."""
    resp = client.put("/api/v1/profile", json=SAMPLE_PROFILE, headers=_headers(user_id))
    assert resp.status_code == 200


def _seed_suggestion(
    user_id: str = USER_ID,
    field_name: str = "skills",
    suggested_value: str = "cyber security",
    source: str = "extracted from CV upload",
    confidence: float = 0.92,
) -> ProfileSuggestion:
    """Add a suggestion directly to the store."""
    suggestion = ProfileSuggestion(
        user_id=user_id,
        field_name=field_name,
        suggested_value=suggested_value,
        source=source,
        confidence=confidence,
    )
    _suggestion_store.add(suggestion)
    return suggestion


class TestListSuggestions:
    """GET /api/v1/profile/suggestions — list pending suggestions."""

    def test_empty_list(self, client: TestClient):
        resp = client.get("/api/v1/profile/suggestions", headers=_headers())
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_pending_suggestions(self, client: TestClient):
        s1 = _seed_suggestion(field_name="skills", suggested_value="risk management")
        s2 = _seed_suggestion(field_name="credentials", suggested_value="GAICD")
        resp = client.get("/api/v1/profile/suggestions", headers=_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        ids = {item["id"] for item in data}
        assert s1.id in ids
        assert s2.id in ids

    def test_does_not_return_resolved_suggestions(self, client: TestClient):
        s = _seed_suggestion()
        s.status = SuggestionStatus.ACCEPTED
        resp = client.get("/api/v1/profile/suggestions", headers=_headers())
        assert resp.status_code == 200
        assert resp.json() == []

    def test_user_isolation(self, client: TestClient):
        _seed_suggestion(user_id="user-a")
        _seed_suggestion(user_id="user-b")
        resp = client.get("/api/v1/profile/suggestions", headers=_headers("user-a"))
        data = resp.json()
        assert len(data) == 1
        assert data[0]["user_id"] == "user-a"

    def test_suggestion_fields_present(self, client: TestClient):
        _seed_suggestion(
            field_name="skills",
            suggested_value="esg",
            source="extracted from CV upload",
            confidence=0.85,
        )
        resp = client.get("/api/v1/profile/suggestions", headers=_headers())
        item = resp.json()[0]
        assert item["field_name"] == "skills"
        assert item["suggested_value"] == "esg"
        assert item["source"] == "extracted from CV upload"
        assert item["confidence"] == 0.85
        assert item["status"] == "pending"
        assert "created_at" in item


class TestAcceptSuggestion:
    """POST /api/v1/profile/suggestions/{id}/accept — accept a suggestion."""

    def test_accept_adds_skill_to_profile(self, client: TestClient):
        _seed_profile(client)
        s = _seed_suggestion(field_name="skills", suggested_value="cyber security")
        resp = client.post(
            f"/api/v1/profile/suggestions/{s.id}/accept", headers=_headers()
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["resolved_at"] is not None

        # Verify profile was updated
        profile = client.get("/api/v1/profile", headers=_headers()).json()
        assert "cyber security" in profile["skills"]

    def test_accept_increments_profile_version(self, client: TestClient):
        _seed_profile(client)
        s = _seed_suggestion()
        client.post(f"/api/v1/profile/suggestions/{s.id}/accept", headers=_headers())
        profile = client.get("/api/v1/profile", headers=_headers()).json()
        assert profile["profile_version"] == 2

    def test_accept_marks_fits_stale(self, client: TestClient):
        _seed_profile(client)
        s = _seed_suggestion()
        client.post(f"/api/v1/profile/suggestions/{s.id}/accept", headers=_headers())
        assert USER_ID in _stale_fits

    def test_accept_is_audited(self, client: TestClient):
        _seed_profile(client)
        s = _seed_suggestion(source="extracted from CV upload")
        client.post(f"/api/v1/profile/suggestions/{s.id}/accept", headers=_headers())
        assert len(_audit_log) == 1
        entry = _audit_log[0]
        assert entry["action"] == "accept"
        assert entry["suggestion_id"] == s.id
        assert entry["user_id"] == USER_ID
        assert entry["source"] == "extracted from CV upload"
        assert "timestamp" in entry

    def test_accept_retains_source(self, client: TestClient):
        _seed_profile(client)
        s = _seed_suggestion(source="extracted from CV upload")
        resp = client.post(
            f"/api/v1/profile/suggestions/{s.id}/accept", headers=_headers()
        )
        assert resp.json()["source"] == "extracted from CV upload"

    def test_accept_not_found(self, client: TestClient):
        resp = client.post(
            "/api/v1/profile/suggestions/nonexistent/accept", headers=_headers()
        )
        assert resp.status_code == 404

    def test_accept_already_accepted(self, client: TestClient):
        _seed_profile(client)
        s = _seed_suggestion()
        client.post(f"/api/v1/profile/suggestions/{s.id}/accept", headers=_headers())
        resp = client.post(
            f"/api/v1/profile/suggestions/{s.id}/accept", headers=_headers()
        )
        assert resp.status_code == 409

    def test_accept_other_users_suggestion_404(self, client: TestClient):
        s = _seed_suggestion(user_id="user-other")
        resp = client.post(
            f"/api/v1/profile/suggestions/{s.id}/accept", headers=_headers(USER_ID)
        )
        assert resp.status_code == 404

    def test_accept_without_profile_fails(self, client: TestClient):
        s = _seed_suggestion()
        resp = client.post(
            f"/api/v1/profile/suggestions/{s.id}/accept", headers=_headers()
        )
        assert resp.status_code == 404
        body = resp.json()
        message = body.get("detail", body.get("message", ""))
        assert "profile" in message.lower()

    def test_accept_scalar_field(self, client: TestClient):
        _seed_profile(client)
        s = _seed_suggestion(field_name="headline", suggested_value="Board-ready CTO")
        resp = client.post(
            f"/api/v1/profile/suggestions/{s.id}/accept", headers=_headers()
        )
        assert resp.status_code == 200
        profile = client.get("/api/v1/profile", headers=_headers()).json()
        assert profile["headline"] == "Board-ready CTO"

    def test_accept_numeric_field(self, client: TestClient):
        _seed_profile(client)
        s = _seed_suggestion(field_name="years_experience", suggested_value="15")
        resp = client.post(
            f"/api/v1/profile/suggestions/{s.id}/accept", headers=_headers()
        )
        assert resp.status_code == 200
        profile = client.get("/api/v1/profile", headers=_headers()).json()
        assert profile["years_experience"] == 15

    def test_accept_does_not_duplicate_list_value(self, client: TestClient):
        _seed_profile(client)
        # "governance" already in skills
        s = _seed_suggestion(field_name="skills", suggested_value="governance")
        resp = client.post(
            f"/api/v1/profile/suggestions/{s.id}/accept", headers=_headers()
        )
        assert resp.status_code == 200
        profile = client.get("/api/v1/profile", headers=_headers()).json()
        assert profile["skills"].count("governance") == 1


class TestRejectSuggestion:
    """POST /api/v1/profile/suggestions/{id}/reject — reject a suggestion."""

    def test_reject_suggestion(self, client: TestClient):
        s = _seed_suggestion()
        resp = client.post(
            f"/api/v1/profile/suggestions/{s.id}/reject", headers=_headers()
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected"
        assert data["resolved_at"] is not None

    def test_reject_does_not_modify_profile(self, client: TestClient):
        _seed_profile(client)
        s = _seed_suggestion(field_name="skills", suggested_value="new skill")
        client.post(f"/api/v1/profile/suggestions/{s.id}/reject", headers=_headers())
        profile = client.get("/api/v1/profile", headers=_headers()).json()
        assert "new skill" not in profile["skills"]
        assert profile["profile_version"] == 1

    def test_reject_does_not_mark_fits_stale(self, client: TestClient):
        _seed_profile(client)
        s = _seed_suggestion()
        client.post(f"/api/v1/profile/suggestions/{s.id}/reject", headers=_headers())
        assert USER_ID not in _stale_fits

    def test_reject_is_audited(self, client: TestClient):
        s = _seed_suggestion()
        client.post(f"/api/v1/profile/suggestions/{s.id}/reject", headers=_headers())
        assert len(_audit_log) == 1
        entry = _audit_log[0]
        assert entry["action"] == "reject"
        assert entry["suggestion_id"] == s.id

    def test_reject_not_found(self, client: TestClient):
        resp = client.post(
            "/api/v1/profile/suggestions/nonexistent/reject", headers=_headers()
        )
        assert resp.status_code == 404

    def test_reject_already_rejected(self, client: TestClient):
        s = _seed_suggestion()
        client.post(f"/api/v1/profile/suggestions/{s.id}/reject", headers=_headers())
        resp = client.post(
            f"/api/v1/profile/suggestions/{s.id}/reject", headers=_headers()
        )
        assert resp.status_code == 409

    def test_no_longer_in_pending_list_after_reject(self, client: TestClient):
        s = _seed_suggestion()
        client.post(f"/api/v1/profile/suggestions/{s.id}/reject", headers=_headers())
        resp = client.get("/api/v1/profile/suggestions", headers=_headers())
        assert resp.json() == []
