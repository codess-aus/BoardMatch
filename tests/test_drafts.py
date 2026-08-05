"""Tests for coaching draft persistence (BM-027)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from boardmatch.api import app
from boardmatch.api.v1.coaching import _draft_repo
from boardmatch.api.v1.rate_limit import draft_rate_limiter

client = TestClient(app)

AUTH_HEADER = {"X-Dev-User-Id": "test-user-drafts"}
OTHER_USER_HEADER = {"X-Dev-User-Id": "other-user"}


@pytest.fixture(autouse=True)
def _clear_drafts():
    """Reset draft store and rate limiter between tests."""
    _draft_repo._store.clear()
    draft_rate_limiter.reset("test-user-drafts")
    draft_rate_limiter.reset("other-user")
    yield
    _draft_repo._store.clear()
    draft_rate_limiter.reset("test-user-drafts")
    draft_rate_limiter.reset("other-user")


class TestBoardCvDraftPersistence:
    """POST /api/v1/coaching/board-cv persists a draft."""

    def test_template_generation_creates_draft(self):
        resp = client.post("/api/v1/coaching/board-cv", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["engine"] == "template"
        assert data["content"]

        drafts = _draft_repo.list_for_user("test-user-drafts")
        assert len(drafts) == 1
        assert drafts[0].draft_type == "board_cv"
        assert drafts[0].engine == "template"

    def test_draft_persisted_with_metadata(self):
        client.post("/api/v1/coaching/board-cv", headers=AUTH_HEADER)
        drafts = _draft_repo.list_for_user("test-user-drafts")
        draft = drafts[0]
        assert draft.user_id == "test-user-drafts"
        assert draft.prompt_version == "1.0"
        assert draft.profile_version >= 1
        assert draft.engine == "template"
        assert draft.model_name is None
        assert draft.id

    def test_board_cv_with_opportunity(self):
        resp = client.post(
            "/api/v1/coaching/board-cv",
            headers=AUTH_HEADER,
            params={"opportunity_id": "gov-002"},
        )
        assert resp.status_code == 200
        drafts = _draft_repo.list_for_user("test-user-drafts")
        assert drafts[0].opportunity_id == "gov-002"


class TestDirectorBioDraft:
    """POST /api/v1/coaching/director-bio generates and persists."""

    def test_director_bio_creates_draft(self):
        resp = client.post("/api/v1/coaching/director-bio", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["draft_type"] == "director_bio"
        assert data["engine"] == "template"
        assert data["content"]
        assert data["id"]

    def test_director_bio_metadata(self):
        resp = client.post("/api/v1/coaching/director-bio", headers=AUTH_HEADER)
        data = resp.json()
        assert data["prompt_version"] == "1.0"
        assert data["profile_version"] >= 1
        assert data["model_name"] is None


class TestOutreachDraft:
    """POST /api/v1/coaching/outreach generates opportunity-specific outreach."""

    def test_outreach_requires_opportunity(self):
        resp = client.post("/api/v1/coaching/outreach", headers=AUTH_HEADER)
        assert resp.status_code == 400

    def test_outreach_with_opportunity(self):
        resp = client.post(
            "/api/v1/coaching/outreach",
            headers=AUTH_HEADER,
            params={"opportunity_id": "gov-002"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["draft_type"] == "outreach"
        assert data["opportunity_id"] == "gov-002"
        assert data["engine"] == "template"

    def test_outreach_unknown_opportunity(self):
        resp = client.post(
            "/api/v1/coaching/outreach",
            headers=AUTH_HEADER,
            params={"opportunity_id": "nonexistent"},
        )
        assert resp.status_code == 404


class TestListDrafts:
    """GET /api/v1/coaching/drafts returns user drafts."""

    def test_list_drafts_empty(self):
        resp = client.get("/api/v1/coaching/drafts", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["drafts"] == []

    def test_list_drafts_after_generation(self):
        client.post("/api/v1/coaching/board-cv", headers=AUTH_HEADER)
        client.post("/api/v1/coaching/director-bio", headers=AUTH_HEADER)

        resp = client.get("/api/v1/coaching/drafts", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert len(data["drafts"]) == 2


class TestGetDraft:
    """GET /api/v1/coaching/drafts/{draft_id} returns a specific draft."""

    def test_get_draft_by_id(self):
        client.post("/api/v1/coaching/director-bio", headers=AUTH_HEADER)
        drafts = _draft_repo.list_for_user("test-user-drafts")
        draft_id = drafts[0].id

        resp = client.get(
            f"/api/v1/coaching/drafts/{draft_id}", headers=AUTH_HEADER
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == draft_id

    def test_get_draft_not_found(self):
        resp = client.get(
            "/api/v1/coaching/drafts/nonexistent", headers=AUTH_HEADER
        )
        assert resp.status_code == 404


class TestDeleteDraft:
    """DELETE /api/v1/coaching/drafts/{draft_id} removes a draft."""

    def test_delete_draft(self):
        client.post("/api/v1/coaching/director-bio", headers=AUTH_HEADER)
        drafts = _draft_repo.list_for_user("test-user-drafts")
        draft_id = drafts[0].id

        resp = client.delete(
            f"/api/v1/coaching/drafts/{draft_id}", headers=AUTH_HEADER
        )
        assert resp.status_code == 204
        assert _draft_repo.get_by_id(draft_id, "test-user-drafts") is None

    def test_delete_nonexistent(self):
        resp = client.delete(
            "/api/v1/coaching/drafts/nonexistent", headers=AUTH_HEADER
        )
        assert resp.status_code == 404


class TestUserIsolation:
    """Drafts are scoped to their owner."""

    def test_cannot_see_other_users_drafts(self):
        client.post("/api/v1/coaching/director-bio", headers=AUTH_HEADER)

        resp = client.get("/api/v1/coaching/drafts", headers=OTHER_USER_HEADER)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_cannot_get_other_users_draft(self):
        client.post("/api/v1/coaching/director-bio", headers=AUTH_HEADER)
        drafts = _draft_repo.list_for_user("test-user-drafts")
        draft_id = drafts[0].id

        resp = client.get(
            f"/api/v1/coaching/drafts/{draft_id}", headers=OTHER_USER_HEADER
        )
        assert resp.status_code == 404

    def test_cannot_delete_other_users_draft(self):
        client.post("/api/v1/coaching/director-bio", headers=AUTH_HEADER)
        drafts = _draft_repo.list_for_user("test-user-drafts")
        draft_id = drafts[0].id

        resp = client.delete(
            f"/api/v1/coaching/drafts/{draft_id}", headers=OTHER_USER_HEADER
        )
        assert resp.status_code == 404
        assert _draft_repo.get_by_id(draft_id, "test-user-drafts") is not None
