"""Regression tests for GitHub issue #106.

Prior to the fix, each router module (``applications``, ``fit_evaluations``,
``opportunities``, ``profile_api``, ...) independently called
``create_repositories``/``create_extended_repositories`` at import time. In
local/test mode (``DATABASE_URL`` uses ``sqlite://``, so the factory selects
in-memory repositories) this meant every module got its *own* throwaway
``InMemory*Repository`` instance instead of sharing one — so data created via
one router (e.g. an opportunity) was invisible to another router (e.g.
applications or fit evaluations), causing spurious 404s. This does not
reproduce against a real Postgres-backed deployment, where all routers share
the same database.

These tests exercise the app the same way production traffic would: create
data via one router's endpoint/state and read it back via a different
router's endpoint, all within a single running app instance in local/SQLite
mode.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from boardmatch.api import app
from boardmatch.api.v1.applications import _application_repo
from boardmatch.api.v1.applications import (
    _opportunity_repo as _applications_opportunity_repo,
)
from boardmatch.api.v1.fit_evaluations import (
    _candidate_repo as _fit_eval_candidate_repo,
)
from boardmatch.api.v1.fit_evaluations import (
    _evaluation_repo as _fit_eval_evaluation_repo,
)
from boardmatch.api.v1.fit_evaluations import (
    _opportunity_repo as _fit_eval_opportunity_repo,
)
from boardmatch.api.v1.opportunities import _repo as _opportunities_repo
from boardmatch.models import Opportunity, Remuneration
from boardmatch.profile_api import _candidate_repo as _profile_candidate_repo
from boardmatch.profile_api import _profile_versions

SAMPLE_PROFILE = {
    "name": "Test User",
    "headline": "Senior Executive",
    "years_experience": 12,
    "skills": ["governance", "finance", "risk management"],
    "sectors": ["Technology"],
    "credentials": ["AICD Company Directors Course"],
    "board_experience": [],
    "achievements": [],
    "locations": [],
    "connections": [],
}


def _headers(user_id: str = "user-repo-sharing") -> dict[str, str]:
    return {"X-Dev-User-Id": user_id}


@pytest.fixture(autouse=True)
def _reset_state():
    """Clear repository state between tests."""
    _application_repo._store.clear()
    _applications_opportunity_repo._store.clear()
    _fit_eval_evaluation_repo._store.clear()
    _fit_eval_candidate_repo._store.clear()
    _profile_candidate_repo._store.clear()
    _profile_versions.clear()
    yield
    _application_repo._store.clear()
    _applications_opportunity_repo._store.clear()
    _fit_eval_evaluation_repo._store.clear()
    _fit_eval_candidate_repo._store.clear()
    _profile_candidate_repo._store.clear()
    _profile_versions.clear()

    from boardmatch import discovery

    for opportunity in discovery.discover():
        _applications_opportunity_repo.add(opportunity)


@pytest.fixture
def client():
    return TestClient(app)


class TestRepositorySharedAcrossRouters:
    """Verify all routers see the same in-memory repository instances."""

    def test_opportunity_and_applications_repos_are_the_same_instance(self):
        """The opportunity repo must be the *same object* across routers."""
        assert _applications_opportunity_repo is _opportunities_repo
        assert _fit_eval_opportunity_repo is _opportunities_repo

    def test_candidate_repo_shared_between_profile_and_fit_evaluations(self):
        assert _profile_candidate_repo is _fit_eval_candidate_repo

    def test_application_created_for_opportunity_added_via_opportunities_router(
        self, client: TestClient
    ):
        """Create an opportunity in the opportunities repo, then use it from
        the applications router — this 404'd before the shared-instance fix.
        """
        opportunity = Opportunity(
            id="shared-opp-1",
            title="Board Director",
            organisation="Acme Corp",
            sector="Technology",
            location="Melbourne",
            source="manual",
            url="https://example.com",
            remuneration=Remuneration.PAID,
            fee_aud=50000,
        )
        _opportunities_repo.add(opportunity)

        resp = client.post(
            "/api/v1/applications",
            json={"opportunity_id": "shared-opp-1"},
            headers=_headers(),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["opportunity_id"] == "shared-opp-1"

    def test_fit_evaluation_for_opportunity_added_via_opportunities_router(
        self, client: TestClient
    ):
        """Create an opportunity via the opportunities repo and a profile via
        the profile router, then evaluate fit via the fit-evaluations
        router — this 404'd before the shared-instance fix.
        """
        opportunity = Opportunity(
            id="shared-opp-2",
            title="NED",
            organisation="Beta Inc",
            sector="Finance",
            location="Sydney",
            source="manual",
            url="https://example.com/2",
            remuneration=Remuneration.VOLUNTARY,
            required_skills=("governance",),
        )
        _opportunities_repo.add(opportunity)

        headers = _headers()
        profile_resp = client.put(
            "/api/v1/profile", json=SAMPLE_PROFILE, headers=headers
        )
        assert profile_resp.status_code == 200, profile_resp.text

        eval_resp = client.post(
            "/api/v1/fit-evaluations",
            json={"opportunity_id": "shared-opp-2"},
            headers=headers,
        )
        assert eval_resp.status_code == 201, eval_resp.text
        assert eval_resp.json()["opportunity_id"] == "shared-opp-2"
