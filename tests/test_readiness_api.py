"""Tests for the persistent readiness API (BM-017)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from boardmatch.api import app
from boardmatch.api.v1.readiness import (
    SCORING_VERSION,
    get_application_repo,
    get_candidate_repo,
    get_fit_evaluation_repo,
    get_readiness_repo,
)
from boardmatch.infrastructure.repositories.memory import (
    InMemoryApplicationRepository,
    InMemoryCandidateRepository,
    InMemoryFitEvaluationRepository,
    InMemoryReadinessRepository,
)
from boardmatch.models import (
    Application,
    ApplicationStage,
    Candidate,
    FitEvaluation,
)

AUTH_HEADER = {"X-Dev-User-Id": "readiness-test-user"}
USER_ID = "readiness-test-user"

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_repos():
    """Provide fresh repos for each test via dependency overrides."""
    candidate_repo = InMemoryCandidateRepository()
    application_repo = InMemoryApplicationRepository()
    fit_evaluation_repo = InMemoryFitEvaluationRepository()
    readiness_repo = InMemoryReadinessRepository()

    app.dependency_overrides[get_candidate_repo] = lambda: candidate_repo
    app.dependency_overrides[get_application_repo] = lambda: application_repo
    app.dependency_overrides[get_fit_evaluation_repo] = lambda: fit_evaluation_repo
    app.dependency_overrides[get_readiness_repo] = lambda: readiness_repo

    yield candidate_repo, application_repo, fit_evaluation_repo, readiness_repo

    app.dependency_overrides.pop(get_candidate_repo, None)
    app.dependency_overrides.pop(get_application_repo, None)
    app.dependency_overrides.pop(get_fit_evaluation_repo, None)
    app.dependency_overrides.pop(get_readiness_repo, None)


class TestEmptyProfile:
    def test_empty_profile_returns_zero_score(self):
        resp = client.get("/api/v1/readiness", headers=AUTH_HEADER)
        assert resp.status_code == 200
        body = resp.json()
        assert body["score"] == 0
        assert body["components"]["credentials"] == 0
        assert body["components"]["skills"] == 0
        assert body["components"]["pipeline_momentum"] == 0

    def test_empty_profile_has_pipeline_action(self):
        resp = client.get("/api/v1/readiness", headers=AUTH_HEADER)
        assert any("pipeline" in a.lower() for a in resp.json()["next_actions"])

    def test_empty_profile_has_scoring_version(self):
        assert client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["scoring_version"] == SCORING_VERSION

    def test_response_structure(self):
        body = client.get("/api/v1/readiness", headers=AUTH_HEADER).json()
        for key in ("score", "components", "scoring_version", "next_actions", "stage_counts"):
            assert key in body


class TestProfileWithCredentials:
    def test_aicd_credential(self, _reset_repos):
        _reset_repos[0].save_for_user(USER_ID, Candidate(name="C", credentials=["AICD Company Directors Course"]))
        assert client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["components"]["credentials"] == 20

    def test_gaicd_credential(self, _reset_repos):
        _reset_repos[0].save_for_user(USER_ID, Candidate(name="C", credentials=["GAICD"]))
        assert client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["components"]["credentials"] == 20

    def test_other_credentials(self, _reset_repos):
        _reset_repos[0].save_for_user(USER_ID, Candidate(name="C", credentials=["CPA"]))
        assert client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["components"]["credentials"] == 8

    def test_board_experience(self, _reset_repos):
        _reset_repos[0].save_for_user(USER_ID, Candidate(name="C", credentials=["AICD Company Directors Course"], board_experience=["A", "B"]))
        assert client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["components"]["credentials"] == 40


class TestProfileWithSkills:
    def test_all_core_skills(self, _reset_repos):
        _reset_repos[0].save_for_user(USER_ID, Candidate(name="S", skills=["governance", "finance", "risk management", "esg", "cyber security"]))
        assert client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["components"]["skills"] == 30

    def test_partial_skills(self, _reset_repos):
        _reset_repos[0].save_for_user(USER_ID, Candidate(name="S", skills=["governance", "finance"]))
        assert client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["components"]["skills"] == 12

    def test_no_relevant_skills(self, _reset_repos):
        _reset_repos[0].save_for_user(USER_ID, Candidate(name="S", skills=["python"]))
        assert client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["components"]["skills"] == 0


class TestPipelineStageScoring:
    def test_researching(self, _reset_repos):
        _reset_repos[1].create(USER_ID, Application(opportunity_id="o1", stage=ApplicationStage.RESEARCHING))
        assert client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["components"]["pipeline_momentum"] == 2

    def test_interviewing(self, _reset_repos):
        _reset_repos[1].create(USER_ID, Application(opportunity_id="o1", stage=ApplicationStage.INTERVIEWING))
        assert client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["components"]["pipeline_momentum"] == 16

    def test_offered(self, _reset_repos):
        _reset_repos[1].create(USER_ID, Application(opportunity_id="o1", stage=ApplicationStage.OFFERED))
        assert client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["components"]["pipeline_momentum"] == 24

    def test_multiple_apps(self, _reset_repos):
        _reset_repos[1].create(USER_ID, Application(opportunity_id="o1", stage=ApplicationStage.APPLIED))
        _reset_repos[1].create(USER_ID, Application(opportunity_id="o2", stage=ApplicationStage.APPLIED))
        assert client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["components"]["pipeline_momentum"] == 20

    def test_stage_counts(self, _reset_repos):
        _reset_repos[1].create(USER_ID, Application(opportunity_id="o1", stage=ApplicationStage.APPLIED))
        _reset_repos[1].create(USER_ID, Application(opportunity_id="o2", stage=ApplicationStage.RESEARCHING))
        counts = client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["stage_counts"]
        assert counts["applied"] == 1 and counts["researching"] == 1

    def test_closed_zero(self, _reset_repos):
        _reset_repos[1].create(USER_ID, Application(opportunity_id="o1", stage=ApplicationStage.CLOSED))
        assert client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["components"]["pipeline_momentum"] == 0


class TestScoreMinimumAndMaximum:
    def test_minimum(self):
        assert client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["score"] == 0

    def test_maximum(self, _reset_repos):
        _reset_repos[0].save_for_user(USER_ID, Candidate(name="P", credentials=["AICD Company Directors Course"], board_experience=["A", "B"], skills=["governance", "finance", "risk management", "esg", "cyber security"]))
        for i in range(5):
            _reset_repos[1].create(USER_ID, Application(opportunity_id=f"o{i}", stage=ApplicationStage.OFFERED))
        assert client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["score"] == 100

    def test_bounds(self, _reset_repos):
        _reset_repos[0].save_for_user(USER_ID, Candidate(name="M", skills=["governance"]))
        assert 0 <= client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["score"] <= 100


class TestNextActionDeduplication:
    def test_no_duplicates(self, _reset_repos):
        _reset_repos[2].create(FitEvaluation(id="e1", user_id=USER_ID, opportunity_id="o1", profile_version=1, scoring_version="1.0", score=60, band="Credible fit", matched_skills=("governance",), missing_skills=("finance",), rationale=("OK",), gap_actions=("Do X", "Do Y"), created_at=datetime.now(timezone.utc)))
        _reset_repos[2].create(FitEvaluation(id="e2", user_id=USER_ID, opportunity_id="o2", profile_version=1, scoring_version="1.0", score=50, band="Credible fit", matched_skills=("governance",), missing_skills=("risk",), rationale=("OK",), gap_actions=("Do X", "Do Z"), created_at=datetime.now(timezone.utc)))
        _reset_repos[1].create(USER_ID, Application(opportunity_id="o1", stage=ApplicationStage.RESEARCHING))
        actions = client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["next_actions"]
        assert len(actions) == len(set(actions))
        assert "Do X" in actions

    def test_limited_to_five(self, _reset_repos):
        _reset_repos[2].create(FitEvaluation(id="e1", user_id=USER_ID, opportunity_id="o1", profile_version=1, scoring_version="1.0", score=60, band="Credible fit", matched_skills=(), missing_skills=(), rationale=(), gap_actions=("A1", "A2", "A3", "A4", "A5", "A6", "A7"), created_at=datetime.now(timezone.utc)))
        _reset_repos[1].create(USER_ID, Application(opportunity_id="o1", stage=ApplicationStage.RESEARCHING))
        assert len(client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["next_actions"]) <= 5


class TestUserIsolation:
    def test_separate_scores(self, _reset_repos):
        _reset_repos[0].save_for_user(USER_ID, Candidate(name="A", skills=["governance", "finance", "risk management", "esg", "cyber security"]))
        _reset_repos[0].save_for_user("other", Candidate(name="B", skills=[]))
        a = client.get("/api/v1/readiness", headers=AUTH_HEADER).json()["score"]
        b = client.get("/api/v1/readiness", headers={"X-Dev-User-Id": "other"}).json()["score"]
        assert a > b

    def test_history_isolation(self, _reset_repos):
        client.get("/api/v1/readiness", headers=AUTH_HEADER)
        client.get("/api/v1/readiness", headers={"X-Dev-User-Id": "other"})
        assert len(client.get("/api/v1/readiness/history", headers=AUTH_HEADER).json()["snapshots"]) == 1

    def test_requires_auth(self):
        assert client.get("/api/v1/readiness").status_code == 401

    def test_history_requires_auth(self):
        assert client.get("/api/v1/readiness/history").status_code == 401


class TestHistoricalReadinessSnapshot:
    def test_empty_history(self):
        resp = client.get("/api/v1/readiness/history", headers=AUTH_HEADER)
        assert resp.status_code == 200 and resp.json() == {"snapshots": []}

    def test_persisted_after_get(self, _reset_repos):
        client.get("/api/v1/readiness", headers=AUTH_HEADER)
        snapshots = client.get("/api/v1/readiness/history", headers=AUTH_HEADER).json()["snapshots"]
        assert len(snapshots) == 1 and "timestamp" in snapshots[0]

    def test_multiple_snapshots(self, _reset_repos):
        client.get("/api/v1/readiness", headers=AUTH_HEADER)
        _reset_repos[0].save_for_user(USER_ID, Candidate(name="U", skills=["governance"]))
        client.get("/api/v1/readiness", headers=AUTH_HEADER)
        assert len(client.get("/api/v1/readiness/history", headers=AUTH_HEADER).json()["snapshots"]) == 2

    def test_newest_first(self, _reset_repos):
        client.get("/api/v1/readiness", headers=AUTH_HEADER)
        _reset_repos[0].save_for_user(USER_ID, Candidate(name="U", skills=["governance", "finance", "risk management", "esg", "cyber security"]))
        client.get("/api/v1/readiness", headers=AUTH_HEADER)
        s = client.get("/api/v1/readiness/history", headers=AUTH_HEADER).json()["snapshots"]
        assert s[0]["score"] > s[1]["score"]

    def test_entry_structure(self, _reset_repos):
        client.get("/api/v1/readiness", headers=AUTH_HEADER)
        entry = client.get("/api/v1/readiness/history", headers=AUTH_HEADER).json()["snapshots"][0]
        for key in ("score", "components", "scoring_version", "next_actions", "stage_counts", "timestamp"):
            assert key in entry


class TestDeterminism:
    def test_same_data_same_score(self, _reset_repos):
        _reset_repos[0].save_for_user(USER_ID, Candidate(name="D", skills=["governance", "finance"], credentials=["CPA"]))
        _reset_repos[1].create(USER_ID, Application(opportunity_id="ox", stage=ApplicationStage.OUTREACH_SENT))
        r1 = client.get("/api/v1/readiness", headers=AUTH_HEADER).json()
        r2 = client.get("/api/v1/readiness", headers=AUTH_HEADER).json()
        assert r1["score"] == r2["score"]
        assert r1["components"] == r2["components"]
        assert r1["scoring_version"] == r2["scoring_version"]
        assert r1["next_actions"] == r2["next_actions"]
