"""Tests for the candidate profile API (BM-009)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from boardmatch.api import app
from boardmatch.profile_api import (
    _candidate_repo,
    _profile_statuses,
    _profile_versions,
)


@pytest.fixture(autouse=True)
def _reset_state():
    """Clear profile state between tests."""
    _candidate_repo._store.clear()
    _profile_versions.clear()
    _profile_statuses.clear()
    yield
    _candidate_repo._store.clear()
    _profile_versions.clear()
    _profile_statuses.clear()


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
    "connections": [
        {
            "name": "Jane Doe",
            "relationship": "Mentor",
            "organisations": ["Acme Corp"],
            "board_seats": ["Acme Corp"],
            "strength": 0.8,
        }
    ],
    "status": "draft",
}


def _headers(user_id: str = "user-001") -> dict[str, str]:
    return {"X-Dev-User-Id": user_id}


class TestCreateProfile:
    """PUT /api/v1/profile - create a new profile."""

    def test_create_profile(self, client: TestClient):
        resp = client.put("/api/v1/profile", json=SAMPLE_PROFILE, headers=_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test User"
        assert data["headline"] == "Engineering leader"
        assert data["years_experience"] == 10
        assert data["skills"] == ["governance", "finance"]
        assert data["profile_version"] == 1
        assert data["status"] == "draft"

    def test_create_profile_with_review_required_status(self, client: TestClient):
        profile = {**SAMPLE_PROFILE, "status": "review_required"}
        resp = client.put("/api/v1/profile", json=profile, headers=_headers())
        assert resp.status_code == 200
        assert resp.json()["status"] == "review_required"

    def test_create_profile_with_confirmed_status(self, client: TestClient):
        profile = {**SAMPLE_PROFILE, "status": "confirmed"}
        resp = client.put("/api/v1/profile", json=profile, headers=_headers())
        assert resp.status_code == 200
        assert resp.json()["status"] == "confirmed"


class TestRetrieveProfile:
    """GET /api/v1/profile - retrieve current user's profile."""

    def test_get_profile_not_found(self, client: TestClient):
        resp = client.get("/api/v1/profile", headers=_headers())
        assert resp.status_code == 404
        assert resp.json()["message"] == "Profile not found"

    def test_get_profile_after_create(self, client: TestClient):
        client.put("/api/v1/profile", json=SAMPLE_PROFILE, headers=_headers())
        resp = client.get("/api/v1/profile", headers=_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test User"
        assert data["profile_version"] == 1


class TestUpdateProfile:
    """PUT /api/v1/profile - update existing profile increments version."""

    def test_update_increments_version(self, client: TestClient):
        client.put("/api/v1/profile", json=SAMPLE_PROFILE, headers=_headers())
        updated = {**SAMPLE_PROFILE, "headline": "Updated headline"}
        resp = client.put("/api/v1/profile", json=updated, headers=_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["headline"] == "Updated headline"
        assert data["profile_version"] == 2

    def test_multiple_updates_increment_version(self, client: TestClient):
        client.put("/api/v1/profile", json=SAMPLE_PROFILE, headers=_headers())
        for i in range(3):
            updated = {**SAMPLE_PROFILE, "headline": f"v{i + 2}"}
            client.put("/api/v1/profile", json=updated, headers=_headers())
        resp = client.get("/api/v1/profile", headers=_headers())
        assert resp.json()["profile_version"] == 4


class TestUserIsolation:
    """Users cannot see other users' profiles."""

    def test_user_isolation(self, client: TestClient):
        client.put("/api/v1/profile", json=SAMPLE_PROFILE, headers=_headers("user-a"))
        resp = client.get("/api/v1/profile", headers=_headers("user-b"))
        assert resp.status_code == 404

    def test_users_see_own_profiles(self, client: TestClient):
        profile_a = {**SAMPLE_PROFILE, "name": "User A"}
        profile_b = {**SAMPLE_PROFILE, "name": "User B"}
        client.put("/api/v1/profile", json=profile_a, headers=_headers("user-a"))
        client.put("/api/v1/profile", json=profile_b, headers=_headers("user-b"))

        resp_a = client.get("/api/v1/profile", headers=_headers("user-a"))
        resp_b = client.get("/api/v1/profile", headers=_headers("user-b"))
        assert resp_a.json()["name"] == "User A"
        assert resp_b.json()["name"] == "User B"


class TestValidation:
    """Validation errors are properly handled."""

    def test_negative_years_experience_rejected(self, client: TestClient):
        profile = {**SAMPLE_PROFILE, "years_experience": -1}
        resp = client.put("/api/v1/profile", json=profile, headers=_headers())
        assert resp.status_code == 422

    def test_empty_name_rejected(self, client: TestClient):
        profile = {**SAMPLE_PROFILE, "name": ""}
        resp = client.put("/api/v1/profile", json=profile, headers=_headers())
        assert resp.status_code == 422

    def test_invalid_status_rejected(self, client: TestClient):
        profile = {**SAMPLE_PROFILE, "status": "invalid_status"}
        resp = client.put("/api/v1/profile", json=profile, headers=_headers())
        assert resp.status_code == 422

    def test_connection_strength_out_of_range(self, client: TestClient):
        profile = {
            **SAMPLE_PROFILE,
            "connections": [
                {
                    "name": "X",
                    "relationship": "Y",
                    "strength": 1.5,
                }
            ],
        }
        resp = client.put("/api/v1/profile", json=profile, headers=_headers())
        assert resp.status_code == 422


class TestPatchSkills:
    """PATCH /api/v1/profile/skills - update skills list."""

    def test_patch_skills(self, client: TestClient):
        client.put("/api/v1/profile", json=SAMPLE_PROFILE, headers=_headers())
        resp = client.patch(
            "/api/v1/profile/skills",
            json={"skills": ["risk", "compliance", "esg"]},
            headers=_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["skills"] == ["risk", "compliance", "esg"]
        assert data["profile_version"] == 2

    def test_patch_skills_no_profile(self, client: TestClient):
        resp = client.patch(
            "/api/v1/profile/skills",
            json={"skills": ["risk"]},
            headers=_headers(),
        )
        assert resp.status_code == 404


class TestPatchCredentials:
    """PATCH /api/v1/profile/credentials - update credentials list."""

    def test_patch_credentials(self, client: TestClient):
        client.put("/api/v1/profile", json=SAMPLE_PROFILE, headers=_headers())
        resp = client.patch(
            "/api/v1/profile/credentials",
            json={"credentials": ["CPA", "GAICD"]},
            headers=_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["credentials"] == ["CPA", "GAICD"]
        assert data["profile_version"] == 2

    def test_patch_credentials_no_profile(self, client: TestClient):
        resp = client.patch(
            "/api/v1/profile/credentials",
            json={"credentials": ["CPA"]},
            headers=_headers(),
        )
        assert resp.status_code == 404


class TestPatchExperience:
    """PATCH /api/v1/profile/experience - update board experience list."""

    def test_patch_experience(self, client: TestClient):
        client.put("/api/v1/profile", json=SAMPLE_PROFILE, headers=_headers())
        resp = client.patch(
            "/api/v1/profile/experience",
            json={"board_experience": ["Chair, Nonprofit ABC (2022-present)"]},
            headers=_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["board_experience"] == ["Chair, Nonprofit ABC (2022-present)"]
        assert data["profile_version"] == 2

    def test_patch_experience_no_profile(self, client: TestClient):
        resp = client.patch(
            "/api/v1/profile/experience",
            json={"board_experience": ["X"]},
            headers=_headers(),
        )
        assert resp.status_code == 404
