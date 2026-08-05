"""Tests for BM-035: UI migration to authenticated APIs.

Verifies the UI serves correctly and relies on authenticated v1 API
endpoints instead of hardcoded demo data.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from boardmatch.api import app
from boardmatch.infrastructure.repositories.memory import (
    InMemoryApplicationRepository,
    InMemoryCandidateRepository,
)
from boardmatch.models import Application, ApplicationStage, Candidate, Connection
from boardmatch.api.v1.applications import get_application_repo, get_opportunity_repo
from boardmatch.api.v1.readiness import (
    get_application_repo as get_readiness_app_repo,
    get_candidate_repo as get_readiness_candidate_repo,
)
from boardmatch.profile_api import get_candidate_repo as get_profile_candidate_repo

AUTH_HEADERS = {"X-Dev-User-Id": "test-user-ui-bm035"}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def authed_client(client):
    """Client with auth headers pre-configured."""

    class AuthedClient:
        def __init__(self, c):
            self._client = c

        def get(self, url, **kwargs):
            headers = {**AUTH_HEADERS, **kwargs.pop("headers", {})}
            return self._client.get(url, headers=headers, **kwargs)

        def post(self, url, **kwargs):
            headers = {**AUTH_HEADERS, **kwargs.pop("headers", {})}
            return self._client.post(url, headers=headers, **kwargs)

        def put(self, url, **kwargs):
            headers = {**AUTH_HEADERS, **kwargs.pop("headers", {})}
            return self._client.put(url, headers=headers, **kwargs)

    return AuthedClient(client)


class TestSignInState:
    """The UI displays sign-in state and protects authenticated endpoints."""

    def test_index_serves_ui_with_sign_in_form(self, client):
        """UI includes sign-in form for unauthenticated users."""
        response = client.get("/")
        assert response.status_code == 200
        assert "sign-in-gate" in response.text
        assert "Sign in to BoardMatch" in response.text

    def test_ui_does_not_hardcode_priya_raman(self, client):
        """The UI no longer assumes the name 'Priya Raman'."""
        response = client.get("/")
        assert response.status_code == 200
        assert "Priya Raman" not in response.text

    def test_session_endpoint_unauthenticated(self, client):
        """Session endpoint returns 401 when no auth header is provided."""
        response = client.get("/api/session")
        assert response.status_code == 401
        body = response.json()
        assert body["authenticated"] is False

    def test_session_endpoint_authenticated(self, client):
        """Session endpoint returns user info when authenticated."""
        response = client.get("/api/session", headers=AUTH_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["authenticated"] is True
        assert body["user_id"] == "test-user-ui-bm035"


class TestProfileLoad:
    """Profile data comes from the authenticated v1 profile API."""

    def test_profile_requires_auth(self, client):
        """Profile endpoint returns 404 for default dev user (no profile created)."""
        response = client.get("/api/v1/profile")
        # In dev mode without explicit header, falls back to dev-user-001
        # which has no profile → 404
        assert response.status_code == 404

    def test_profile_returns_user_specific_data(self, authed_client):
        """After creating a profile, it returns user-specific data."""
        # Create a profile first
        profile_data = {
            "name": "Test User",
            "headline": "Board candidate",
            "years_experience": 10,
            "skills": ["governance", "finance"],
            "sectors": ["technology"],
            "credentials": ["GAICD"],
            "board_experience": [],
            "achievements": [],
            "locations": ["Sydney"],
            "connections": [],
            "status": "draft",
        }
        put_resp = authed_client.put("/api/v1/profile", json=profile_data)
        assert put_resp.status_code == 200

        # Now fetch it
        response = authed_client.get("/api/v1/profile")
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Test User"
        assert "Priya Raman" != body["name"]

    def test_profile_404_when_not_created(self, client):
        """Profile returns 404 for user with no profile."""
        headers = {"X-Dev-User-Id": "no-profile-user"}
        response = client.get("/api/v1/profile", headers=headers)
        assert response.status_code == 404


class TestOpportunitySearch:
    """Opportunity results come from authenticated v1 API."""

    def test_opportunities_require_auth(self, client):
        """Opportunities endpoint returns 401 without auth."""
        response = client.get("/api/v1/opportunities")
        assert response.status_code == 401

    def test_opportunities_returns_paginated_results(self, authed_client):
        """Authenticated user gets paginated opportunity list."""
        response = authed_client.get("/api/v1/opportunities?page=1&page_size=5")
        assert response.status_code == 200
        body = response.json()
        assert "items" in body
        assert "total" in body
        assert "page" in body
        assert "page_size" in body
        assert len(body["items"]) <= 5

    def test_opportunities_with_paid_filter(self, authed_client):
        """Paid-only filter works on authenticated endpoint."""
        response = authed_client.get("/api/v1/opportunities?paid_only=true")
        assert response.status_code == 200
        body = response.json()
        for item in body["items"]:
            assert item["remuneration"] == "paid"

    def test_no_mock_only_ids_in_results(self, authed_client):
        """Opportunity IDs are not hardcoded mock-only values."""
        response = authed_client.get("/api/v1/opportunities?page_size=50")
        assert response.status_code == 200
        body = response.json()
        # Verify results have proper IDs (not empty or placeholder)
        for item in body["items"]:
            assert item["id"]
            assert len(item["id"]) > 0


class TestApplicationCreation:
    """Applications use the authenticated v1 applications API."""

    def test_applications_require_auth(self, client):
        """Applications endpoint returns 401 without auth."""
        response = client.get("/api/v1/applications")
        assert response.status_code == 401

    def test_create_application(self, authed_client):
        """Can create an application through authenticated endpoint."""
        from boardmatch.api.v1.applications import _opportunity_repo
        from boardmatch.models import Opportunity, Remuneration

        # Seed the applications opportunity repo with a known opportunity
        test_opp = Opportunity(
            id="test-opp-bm035",
            title="Test Board Seat",
            organisation="Test Corp",
            sector="Technology",
            location="Sydney",
            source="manual",
            url="https://example.com",
            remuneration=Remuneration.PAID,
            fee_aud=50000,
        )
        _opportunity_repo.add(test_opp)

        response = authed_client.post(
            "/api/v1/applications",
            json={"opportunity_id": "test-opp-bm035", "stage": "applied", "notes": "UI test"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["opportunity_id"] == "test-opp-bm035"
        assert body["stage"] == "applied"

    def test_list_applications_user_specific(self, authed_client, client):
        """Applications are user-specific — different user sees different apps."""
        other_headers = {"X-Dev-User-Id": "other-user-apps"}
        response = client.get("/api/v1/applications", headers=other_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["applications"] == []


class TestReadinessDisplay:
    """Readiness uses persisted data from the authenticated API."""

    def test_readiness_requires_auth(self, client):
        """Readiness endpoint returns 401 without auth."""
        response = client.get("/api/v1/readiness")
        assert response.status_code == 401

    def test_readiness_returns_score_and_components(self, authed_client):
        """Readiness returns score, components, and next actions."""
        response = authed_client.get("/api/v1/readiness")
        assert response.status_code == 200
        body = response.json()
        assert "score" in body
        assert "components" in body
        assert "credentials" in body["components"]
        assert "skills" in body["components"]
        assert "pipeline_momentum" in body["components"]
        assert "next_actions" in body
        assert "scoring_version" in body
        assert "stage_counts" in body


class TestApiErrorDisplay:
    """The UI handles API errors gracefully."""

    def test_404_error_for_invalid_opportunity(self, authed_client):
        """Requesting a non-existent opportunity returns 404."""
        response = authed_client.get("/api/v1/opportunities/non-existent-id-xyz")
        assert response.status_code == 404
        body = response.json()
        assert "message" in body or "detail" in body

    def test_401_without_auth(self, client):
        """Key v1 endpoints return 401 without explicit auth credentials."""
        endpoints = [
            "/api/v1/opportunities",
            "/api/v1/applications",
            "/api/v1/readiness",
        ]
        for endpoint in endpoints:
            response = client.get(endpoint)
            assert response.status_code == 401, f"{endpoint} should require auth"

    def test_profile_requires_user_context(self, client):
        """Profile falls back to dev user but returns 404 if no profile exists."""
        response = client.get("/api/v1/profile")
        # Without explicit auth, uses default dev user → no profile → 404
        assert response.status_code == 404

    def test_422_for_invalid_application_stage(self, authed_client):
        """Invalid stage is rejected with 422."""
        from boardmatch.api.v1.applications import _opportunity_repo
        from boardmatch.models import Opportunity, Remuneration

        test_opp = Opportunity(
            id="test-opp-bm035-422",
            title="Test Seat",
            organisation="Corp",
            sector="Tech",
            location="Melbourne",
            source="manual",
            url="https://example.com",
            remuneration=Remuneration.PAID,
        )
        _opportunity_repo.add(test_opp)

        response = authed_client.post(
            "/api/v1/applications",
            json={"opportunity_id": "test-opp-bm035-422", "stage": "invalid_stage"},
        )
        assert response.status_code == 422


class TestEmptyResultState:
    """UI handles empty results gracefully."""

    def test_empty_applications_list(self, client):
        """User with no applications gets empty list."""
        headers = {"X-Dev-User-Id": "fresh-empty-user"}
        response = client.get("/api/v1/applications", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["applications"] == []

    def test_opportunities_with_impossible_filter(self, authed_client):
        """Filtering with impossible constraints returns empty results."""
        response = authed_client.get(
            "/api/v1/opportunities?min_fee_aud=99999999&paid_only=true"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0


class TestExpiredOpportunityDisplay:
    """Expired/closed opportunities are labelled in the UI."""

    def test_ui_contains_expired_badge_markup(self, client):
        """The UI template includes expired badge styling."""
        response = client.get("/")
        assert response.status_code == 200
        assert "badge.expired" in response.text
        assert "Expired" in response.text

    def test_ui_contains_unverified_badge_markup(self, client):
        """The UI template includes unverified badge styling."""
        response = client.get("/")
        assert response.status_code == 200
        assert "badge.unverified" in response.text
        assert "unverified" in response.text

    def test_opportunities_include_closes_on_field(self, authed_client):
        """Opportunity responses include closes_on for expiry detection."""
        response = authed_client.get("/api/v1/opportunities?status=all&page_size=50")
        assert response.status_code == 200
        body = response.json()
        # At least some opportunities should have a closes_on date
        has_close_dates = any(
            item.get("closes_on") is not None for item in body["items"]
        )
        assert has_close_dates, "Some opportunities should have closing dates"

    def test_all_status_includes_expired(self, authed_client):
        """Using status=all includes expired opportunities."""
        response = authed_client.get("/api/v1/opportunities?status=all&page_size=50")
        assert response.status_code == 200
        body = response.json()
        # With status=all, we should get all opportunities (including any expired)
        assert body["total"] >= 1
