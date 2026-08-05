"""Tests for BM-029: Authorisation and ownership enforcement."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from boardmatch.api import app
from boardmatch.api.v1.applications import _application_repo, _opportunity_repo
from boardmatch.api.v1.authorization import require_admin, require_active_user
from boardmatch.api.v1.coaching import _draft_repo
from boardmatch.auth import CurrentUser, get_current_user
from boardmatch.drafts import Draft
from boardmatch.models import Application, ApplicationStage, Opportunity, Remuneration
from boardmatch.profile_api import (
    _candidate_repo,
    _profile_statuses,
    _profile_versions,
)

client = TestClient(app)

USER_A = "user-alice"
USER_B = "user-bob"
ADMIN_USER = "user-admin"


def _headers(user_id: str, roles: str = "user") -> dict[str, str]:
    return {"X-Dev-User-Id": user_id, "X-Dev-User-Roles": roles}


@pytest.fixture(autouse=True)
def _reset_state():
    _application_repo._store.clear()
    _opportunity_repo._store.clear()
    _draft_repo._store.clear()
    _candidate_repo._store.clear()
    _profile_versions.clear()
    _profile_statuses.clear()
    yield
    _application_repo._store.clear()
    _opportunity_repo._store.clear()
    _draft_repo._store.clear()
    _candidate_repo._store.clear()
    _profile_versions.clear()
    _profile_statuses.clear()


SAMPLE_PROFILE = {
    "name": "Alice Test",
    "headline": "Engineering leader",
    "years_experience": 10,
    "skills": ["governance"],
    "sectors": ["technology"],
    "credentials": ["MBA"],
    "board_experience": [],
    "achievements": [],
    "locations": ["Melbourne"],
    "connections": [],
    "status": "draft",
}


def _seed_opportunity() -> str:
    opp = Opportunity(
        id="opp-test-001",
        title="Test Board",
        organisation="Test Org",
        sector="Technology",
        location="Melbourne",
        source="test",
        url="https://example.com",
        remuneration=Remuneration.PAID,
        fee_aud=10000,
        closes_on="2099-12-31",
        summary="Test opportunity",
        required_skills=("governance",),
    )
    _opportunity_repo.add(opp)
    return opp.id


def _seed_application(user_id: str, opp_id: str) -> str:
    app_obj = Application(
        opportunity_id=opp_id,
        stage=ApplicationStage.RESEARCHING,
        notes="test",
    )
    created = _application_repo.create(user_id, app_obj)
    return created.id


def _seed_draft(user_id: str) -> str:
    draft = Draft(
        id="draft-test-001",
        user_id=user_id,
        draft_type="board_cv",
        content="Test content",
        engine="template",
    )
    _draft_repo.create(draft)
    return draft.id


class TestProfileOwnership:
    def test_user_accesses_own_profile(self):
        resp = client.put("/api/v1/profile", json=SAMPLE_PROFILE, headers=_headers(USER_A))
        assert resp.status_code == 200
        resp = client.get("/api/v1/profile", headers=_headers(USER_A))
        assert resp.status_code == 200
        assert resp.json()["name"] == "Alice Test"

    def test_cross_user_profile_access_returns_404(self):
        client.put("/api/v1/profile", json=SAMPLE_PROFILE, headers=_headers(USER_A))
        resp = client.get("/api/v1/profile", headers=_headers(USER_B))
        assert resp.status_code == 404


class TestApplicationOwnership:
    def test_user_accesses_own_application(self):
        opp_id = _seed_opportunity()
        app_id = _seed_application(USER_A, opp_id)
        resp = client.get(f"/api/v1/applications/{app_id}", headers=_headers(USER_A))
        assert resp.status_code == 200

    def test_cross_user_application_access_returns_404(self):
        opp_id = _seed_opportunity()
        app_id = _seed_application(USER_A, opp_id)
        resp = client.get(f"/api/v1/applications/{app_id}", headers=_headers(USER_B))
        assert resp.status_code == 404

    def test_cross_user_application_list_isolated(self):
        opp_id = _seed_opportunity()
        _seed_application(USER_A, opp_id)
        resp = client.get("/api/v1/applications", headers=_headers(USER_B))
        assert resp.status_code == 200
        assert resp.json()["applications"] == []

    def test_cross_user_application_delete_fails(self):
        opp_id = _seed_opportunity()
        app_id = _seed_application(USER_A, opp_id)
        resp = client.delete(f"/api/v1/applications/{app_id}", headers=_headers(USER_B))
        assert resp.status_code == 404


class TestDraftOwnership:
    def test_user_accesses_own_draft(self):
        draft_id = _seed_draft(USER_A)
        resp = client.get(f"/api/v1/coaching/drafts/{draft_id}", headers=_headers(USER_A))
        assert resp.status_code == 200

    def test_cross_user_draft_access_returns_404(self):
        draft_id = _seed_draft(USER_A)
        resp = client.get(f"/api/v1/coaching/drafts/{draft_id}", headers=_headers(USER_B))
        assert resp.status_code == 404

    def test_cross_user_draft_list_isolated(self):
        _seed_draft(USER_A)
        resp = client.get("/api/v1/coaching/drafts", headers=_headers(USER_B))
        assert resp.status_code == 200
        assert resp.json()["drafts"] == []

    def test_cross_user_draft_delete_fails(self):
        draft_id = _seed_draft(USER_A)
        resp = client.delete(f"/api/v1/coaching/drafts/{draft_id}", headers=_headers(USER_B))
        assert resp.status_code == 404


class TestAdminRoleEnforcement:
    """Test require_admin dependency using a minimal test app."""

    @pytest.fixture
    def admin_app(self):
        test_app = FastAPI()

        @test_app.get("/admin/test")
        def admin_route(user: CurrentUser = Depends(require_admin)):
            return {"user_id": user.user_id}

        return TestClient(test_app)

    def test_non_admin_cannot_access_admin_route(self, admin_app):
        resp = admin_app.get("/admin/test", headers=_headers(USER_A, roles="user"))
        assert resp.status_code == 403
        assert "Admin access required" in resp.json()["detail"]

    def test_admin_can_access_admin_route(self, admin_app):
        resp = admin_app.get("/admin/test", headers=_headers(ADMIN_USER, roles="user,admin"))
        assert resp.status_code == 200
        assert resp.json()["user_id"] == ADMIN_USER

    def test_no_auth_uses_default_user_no_admin(self, admin_app):
        resp = admin_app.get("/admin/test")
        assert resp.status_code == 403


class TestDisabledUserRejection:
    def test_disabled_user_cannot_access_profile(self):
        client.put("/api/v1/profile", json=SAMPLE_PROFILE, headers=_headers(USER_A))
        resp = client.get("/api/v1/profile", headers=_headers(USER_A, roles="user,disabled"))
        assert resp.status_code == 403
        assert "disabled" in resp.json()["message"].lower()

    def test_disabled_user_cannot_access_applications(self):
        resp = client.get("/api/v1/applications", headers=_headers(USER_A, roles="user,disabled"))
        assert resp.status_code == 403

    def test_disabled_user_cannot_access_drafts(self):
        resp = client.get("/api/v1/coaching/drafts", headers=_headers(USER_A, roles="user,disabled"))
        assert resp.status_code == 403
