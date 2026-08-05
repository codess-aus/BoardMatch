"""Tests for the fit evaluations API (BM-016)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from boardmatch.api import app
from boardmatch.api.v1.fit_evaluations import (
    _candidate_repo,
    _evaluation_repo,
    _opportunity_repo,
)
from boardmatch.models import Candidate, Opportunity, Remuneration
from boardmatch.profile_api import _profile_versions


@pytest.fixture(autouse=True)
def _reset_state():
    """Clear repository state between tests."""
    _evaluation_repo._store.clear()
    _candidate_repo._store.clear()
    _opportunity_repo._store.clear()
    _profile_versions.clear()

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
            required_skills=("governance", "finance"),
            desirable_skills=("cyber security",),
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
            required_skills=("risk management",),
        )
    )

    _candidate_repo.save_for_user(
        "user-001",
        Candidate(
            name="Test User",
            headline="Senior Executive",
            skills=["governance", "finance", "risk management"],
            sectors=["Technology"],
            credentials=["AICD Company Directors Course"],
        ),
    )
    _profile_versions["user-001"] = 1

    yield

    _evaluation_repo._store.clear()
    _candidate_repo._store.clear()
    _opportunity_repo._store.clear()
    _profile_versions.clear()


@pytest.fixture
def client():
    return TestClient(app)


def _headers(user_id: str = "user-001") -> dict[str, str]:
    return {"X-Dev-User-Id": user_id}


class TestCreateFitEvaluation:
    """POST /api/v1/fit-evaluations"""

    def test_create_success(self, client: TestClient):
        resp = client.post(
            "/api/v1/fit-evaluations",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["opportunity_id"] == "opp-1"
        assert data["profile_version"] == 1
        assert data["scoring_version"] == "1.0"
        assert data["score"] >= 0
        assert data["band"] in ("Strong fit", "Credible fit", "Stretch")
        assert isinstance(data["matched_skills"], list)
        assert isinstance(data["missing_skills"], list)
        assert isinstance(data["rationale"], list)
        assert isinstance(data["gap_actions"], list)
        assert "id" in data
        assert "created_at" in data

    def test_idempotent_same_versions(self, client: TestClient):
        """Same profile_version + scoring_version + opportunity returns existing."""
        resp1 = client.post(
            "/api/v1/fit-evaluations",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        resp2 = client.post(
            "/api/v1/fit-evaluations",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        assert resp1.status_code == 201
        assert resp2.status_code == 201
        assert resp1.json()["id"] == resp2.json()["id"]

    def test_new_profile_version_creates_new_evaluation(self, client: TestClient):
        """Profile change creates new evaluation version."""
        resp1 = client.post(
            "/api/v1/fit-evaluations",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        _profile_versions["user-001"] = 2
        resp2 = client.post(
            "/api/v1/fit-evaluations",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        assert resp1.json()["id"] != resp2.json()["id"]
        assert resp2.json()["profile_version"] == 2

    def test_old_evaluations_remain_for_audit(self, client: TestClient):
        """Old evaluations remain available after new version created."""
        resp1 = client.post(
            "/api/v1/fit-evaluations",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        eval_id_v1 = resp1.json()["id"]
        _profile_versions["user-001"] = 2
        client.post(
            "/api/v1/fit-evaluations",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        resp = client.get(
            f"/api/v1/fit-evaluations/{eval_id_v1}",
            headers=_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["profile_version"] == 1

    def test_unknown_opportunity_rejected(self, client: TestClient):
        resp = client.post(
            "/api/v1/fit-evaluations",
            json={"opportunity_id": "nonexistent"},
            headers=_headers(),
        )
        assert resp.status_code == 404
        assert "Opportunity not found" in resp.json()["message"]

    def test_no_profile_rejected(self, client: TestClient):
        """User without a profile gets an error."""
        resp = client.post(
            "/api/v1/fit-evaluations",
            json={"opportunity_id": "opp-1"},
            headers=_headers("user-no-profile"),
        )
        assert resp.status_code == 400
        assert "Profile not found" in resp.json()["message"]

    def test_requires_auth(self, client: TestClient):
        resp = client.post(
            "/api/v1/fit-evaluations",
            json={"opportunity_id": "opp-1"},
        )
        assert resp.status_code == 401

    def test_scoring_produces_expected_skills(self, client: TestClient):
        """Verify matched/missing skills reflect the candidate profile."""
        resp = client.post(
            "/api/v1/fit-evaluations",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        data = resp.json()
        assert "governance" in data["matched_skills"]
        assert "finance" in data["matched_skills"]
        assert "cyber security" in data["missing_skills"]


class TestListFitEvaluations:
    """GET /api/v1/fit-evaluations"""

    def test_empty_list(self, client: TestClient):
        resp = client.get("/api/v1/fit-evaluations", headers=_headers())
        assert resp.status_code == 200
        assert resp.json() == {"evaluations": []}

    def test_returns_created_evaluations(self, client: TestClient):
        client.post(
            "/api/v1/fit-evaluations",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        client.post(
            "/api/v1/fit-evaluations",
            json={"opportunity_id": "opp-2"},
            headers=_headers(),
        )
        resp = client.get("/api/v1/fit-evaluations", headers=_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["evaluations"]) == 2

    def test_requires_auth(self, client: TestClient):
        resp = client.get("/api/v1/fit-evaluations")
        assert resp.status_code == 401


class TestGetFitEvaluation:
    """GET /api/v1/fit-evaluations/{evaluation_id}"""

    def test_get_existing(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/fit-evaluations",
            json={"opportunity_id": "opp-1"},
            headers=_headers(),
        )
        eval_id = create_resp.json()["id"]
        resp = client.get(f"/api/v1/fit-evaluations/{eval_id}", headers=_headers())
        assert resp.status_code == 200
        assert resp.json()["id"] == eval_id

    def test_not_found(self, client: TestClient):
        resp = client.get(
            "/api/v1/fit-evaluations/nonexistent", headers=_headers()
        )
        assert resp.status_code == 404

    def test_requires_auth(self, client: TestClient):
        resp = client.get("/api/v1/fit-evaluations/some-id")
        assert resp.status_code == 401


class TestUserIsolation:
    """Users cannot access other users evaluations."""

    def test_cannot_see_other_users_evaluations(self, client: TestClient):
        _candidate_repo.save_for_user(
            "user-b",
            Candidate(name="User B", skills=["governance"]),
        )
        _profile_versions["user-b"] = 1
        create_resp = client.post(
            "/api/v1/fit-evaluations",
            json={"opportunity_id": "opp-1"},
            headers=_headers("user-001"),
        )
        eval_id = create_resp.json()["id"]
        resp = client.get(
            f"/api/v1/fit-evaluations/{eval_id}",
            headers=_headers("user-b"),
        )
        assert resp.status_code == 404

    def test_cannot_list_other_users_evaluations(self, client: TestClient):
        client.post(
            "/api/v1/fit-evaluations",
            json={"opportunity_id": "opp-1"},
            headers=_headers("user-001"),
        )
        _candidate_repo.save_for_user(
            "user-b",
            Candidate(name="User B", skills=["governance"]),
        )
        _profile_versions["user-b"] = 1
        resp = client.get("/api/v1/fit-evaluations", headers=_headers("user-b"))
        assert resp.status_code == 200
        assert resp.json()["evaluations"] == []
