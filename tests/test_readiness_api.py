"""Tests for the persistent readiness service (BM-017)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from boardmatch.api import app
from boardmatch.api.v1.readiness import (
    SCORING_VERSION,
    get_application_repo,
    get_candidate_repo,
    get_history_store,
)
from boardmatch.infrastructure.repositories.memory import (
    InMemoryApplicationRepository,
    InMemoryCandidateRepository,
)
from boardmatch.models import Application, ApplicationStage, Candidate

AUTH_HEADER = {"X-Dev-User-Id": "readiness-test-user"}
USER_ID = "readiness-test-user"

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_repos():
    """Provide fresh repos for each test via dependency overrides."""
    candidate_repo = InMemoryCandidateRepository()
    application_repo = InMemoryApplicationRepository()
    history: dict[str, list] = {}

    app.dependency_overrides[get_candidate_repo] = lambda: candidate_repo
    app.dependency_overrides[get_application_repo] = lambda: application_repo
    app.dependency_overrides[get_history_store] = lambda: history

    yield candidate_repo, application_repo, history

    app.dependency_overrides.pop(get_candidate_repo, None)
    app.dependency_overrides.pop(get_application_repo, None)
    app.dependency_overrides.pop(get_history_store, None)


class TestGetReadiness:
    """Tests for GET /api/v1/readiness."""

    def test_returns_200_authenticated(self):
        resp = client.get("/api/v1/readiness", headers=AUTH_HEADER)
        assert resp.status_code == 200

    def test_returns_401_without_auth(self):
        resp = client.get("/api/v1/readiness")
        assert resp.status_code == 401

    def test_response_structure(self):
        resp = client.get("/api/v1/readiness", headers=AUTH_HEADER)
        body = resp.json()
        assert "score" in body
        assert "components" in body
        assert "scoring_version" in body
        assert "next_actions" in body
        assert "stage_counts" in body

    def test_score_range_0_to_100(self):
        resp = client.get("/api/v1/readiness", headers=AUTH_HEADER)
        body = resp.json()
        assert 0 <= body["score"] <= 100

    def test_components_breakdown(self):
        resp = client.get("/api/v1/readiness", headers=AUTH_HEADER)
        components = resp.json()["components"]
        assert "credentials" in components
        assert "skills" in components
        assert "pipeline_momentum" in components
        assert 0 <= components["credentials"] <= 40
        assert 0 <= components["skills"] <= 30
        assert 0 <= components["pipeline_momentum"] <= 30

    def test_scoring_version_returned(self):
        resp = client.get("/api/v1/readiness", headers=AUTH_HEADER)
        assert resp.json()["scoring_version"] == SCORING_VERSION

    def test_empty_profile_baseline(self):
        """User with no profile gets zero score."""
        resp = client.get("/api/v1/readiness", headers=AUTH_HEADER)
        body = resp.json()
        assert body["score"] == 0
        assert body["components"]["credentials"] == 0
        assert body["components"]["skills"] == 0
        assert body["components"]["pipeline_momentum"] == 0

    def test_credentials_contribute_to_score(self, _reset_repos):
        candidate_repo, _, _ = _reset_repos
        candidate = Candidate(
            name="Test User",
            credentials=["AICD Company Directors Course", "GAICD"],
            board_experience=["Community Board"],
        )
        candidate_repo.save_for_user(USER_ID, candidate)

        resp = client.get("/api/v1/readiness", headers=AUTH_HEADER)
        body = resp.json()
        assert body["components"]["credentials"] > 0
        assert body["score"] > 0

    def test_skills_contribute_to_score(self, _reset_repos):
        candidate_repo, _, _ = _reset_repos
        candidate = Candidate(
            name="Test User",
            skills=["governance", "finance", "risk management", "esg", "cyber security"],
        )
        candidate_repo.save_for_user(USER_ID, candidate)

        resp = client.get("/api/v1/readiness", headers=AUTH_HEADER)
        body = resp.json()
        assert body["components"]["skills"] == 30

    def test_pipeline_momentum_from_applications(self, _reset_repos):
        _, application_repo, _ = _reset_repos
        app1 = Application(
            opportunity_id="opp-1", stage=ApplicationStage.APPLIED, notes=""
        )
        app2 = Application(
            opportunity_id="opp-2", stage=ApplicationStage.INTERVIEWING, notes=""
        )
        application_repo.create(USER_ID, app1)
        application_repo.create(USER_ID, app2)

        resp = client.get("/api/v1/readiness", headers=AUTH_HEADER)
        body = resp.json()
        assert body["components"]["pipeline_momentum"] > 0

    def test_stage_counts_reflect_applications(self, _reset_repos):
        _, application_repo, _ = _reset_repos
        application_repo.create(
            USER_ID,
            Application(opportunity_id="opp-1", stage=ApplicationStage.RESEARCHING),
        )
        application_repo.create(
            USER_ID,
            Application(opportunity_id="opp-2", stage=ApplicationStage.APPLIED),
        )

        resp = client.get("/api/v1/readiness", headers=AUTH_HEADER)
        counts = resp.json()["stage_counts"]
        assert counts["researching"] == 1
        assert counts["applied"] == 1

    def test_next_actions_derived_from_profile(self, _reset_repos):
        """Next actions reflect actual profile state."""
        candidate_repo, _, _ = _reset_repos
        # User with no credentials, no experience, no applications
        candidate = Candidate(name="New User")
        candidate_repo.save_for_user(USER_ID, candidate)

        resp = client.get("/api/v1/readiness", headers=AUTH_HEADER)
        actions = resp.json()["next_actions"]
        assert len(actions) > 0
        # Should suggest credential and pipeline actions
        action_text = " ".join(actions).lower()
        assert "credential" in action_text or "pipeline" in action_text

    def test_deterministic_for_same_data(self, _reset_repos):
        """Same profile + applications yield same score."""
        candidate_repo, application_repo, _ = _reset_repos
        candidate = Candidate(
            name="Stable User",
            skills=["governance", "finance"],
            credentials=["GAICD"],
        )
        candidate_repo.save_for_user(USER_ID, candidate)
        application_repo.create(
            USER_ID,
            Application(opportunity_id="opp-x", stage=ApplicationStage.OUTREACH_SENT),
        )

        resp1 = client.get("/api/v1/readiness", headers=AUTH_HEADER)
        resp2 = client.get("/api/v1/readiness", headers=AUTH_HEADER)

        body1 = resp1.json()
        body2 = resp2.json()
        assert body1["score"] == body2["score"]
        assert body1["components"] == body2["components"]
        assert body1["scoring_version"] == body2["scoring_version"]
        assert body1["next_actions"] == body2["next_actions"]


class TestReadinessHistory:
    """Tests for GET /api/v1/readiness/history."""

    def test_returns_200_authenticated(self):
        resp = client.get("/api/v1/readiness/history", headers=AUTH_HEADER)
        assert resp.status_code == 200

    def test_returns_401_without_auth(self):
        resp = client.get("/api/v1/readiness/history")
        assert resp.status_code == 401

    def test_empty_history_initially(self):
        resp = client.get("/api/v1/readiness/history", headers=AUTH_HEADER)
        body = resp.json()
        assert body["snapshots"] == []

    def test_snapshot_saved_on_readiness_calculation(self, _reset_repos):
        """Each GET /readiness saves a snapshot to history."""
        # First calculation
        client.get("/api/v1/readiness", headers=AUTH_HEADER)

        resp = client.get("/api/v1/readiness/history", headers=AUTH_HEADER)
        body = resp.json()
        assert len(body["snapshots"]) == 1

    def test_multiple_snapshots_accumulate(self, _reset_repos):
        """Multiple readiness calculations produce multiple history entries."""
        candidate_repo, application_repo, _ = _reset_repos

        # First calculation with empty profile
        client.get("/api/v1/readiness", headers=AUTH_HEADER)

        # Add some data and calculate again
        candidate_repo.save_for_user(
            USER_ID,
            Candidate(name="Growing User", skills=["governance", "finance"]),
        )
        client.get("/api/v1/readiness", headers=AUTH_HEADER)

        resp = client.get("/api/v1/readiness/history", headers=AUTH_HEADER)
        snapshots = resp.json()["snapshots"]
        assert len(snapshots) == 2
        # Score should increase between snapshots
        assert snapshots[1]["score"] >= snapshots[0]["score"]

    def test_history_entry_structure(self, _reset_repos):
        """History entries contain all required fields."""
        client.get("/api/v1/readiness", headers=AUTH_HEADER)

        resp = client.get("/api/v1/readiness/history", headers=AUTH_HEADER)
        entry = resp.json()["snapshots"][0]
        assert "score" in entry
        assert "components" in entry
        assert "scoring_version" in entry
        assert "next_actions" in entry
        assert "stage_counts" in entry
        assert "timestamp" in entry

    def test_history_isolated_per_user(self, _reset_repos):
        """Different users have separate history."""
        # User A calculates readiness
        client.get("/api/v1/readiness", headers=AUTH_HEADER)

        # User B checks history
        other_header = {"X-Dev-User-Id": "other-user"}
        resp = client.get("/api/v1/readiness/history", headers=other_header)
        assert resp.json()["snapshots"] == []

    def test_history_contains_scoring_version(self, _reset_repos):
        """Each snapshot records the scoring version."""
        client.get("/api/v1/readiness", headers=AUTH_HEADER)

        resp = client.get("/api/v1/readiness/history", headers=AUTH_HEADER)
        entry = resp.json()["snapshots"][0]
        assert entry["scoring_version"] == SCORING_VERSION
