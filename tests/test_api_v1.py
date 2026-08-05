"""Tests for API v1 versioned routes (BM-011)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from boardmatch.api import app
from boardmatch.api.v1.schemas import (
    ApplicationResponse,
    CoachingBoardCvResponse,
    OpportunityListResponse,
    OpportunityResponse,
    ReadinessResponse,
)

client = TestClient(app)

AUTH_HEADER = {"X-Dev-User-Id": "test-user"}


# --- Opportunities ---


class TestV1Opportunities:
    """Tests for GET /api/v1/opportunities."""

    def test_list_opportunities_success(self):
        resp = client.get("/api/v1/opportunities", headers=AUTH_HEADER)
        assert resp.status_code == 200
        body = resp.json()
        # Validate against Pydantic model
        parsed = OpportunityListResponse(**body)
        assert parsed.count > 0
        assert len(parsed.results) == parsed.count
        assert parsed.paid_count <= parsed.count

    def test_list_opportunities_with_filters(self):
        resp = client.get(
            "/api/v1/opportunities",
            headers=AUTH_HEADER,
            params={"paid_only": True, "limit": 5},
        )
        assert resp.status_code == 200
        body = resp.json()
        parsed = OpportunityListResponse(**body)
        assert parsed.count == parsed.paid_count
        assert parsed.count <= 5

    def test_get_opportunity_success(self):
        resp = client.get("/api/v1/opportunities/gov-001", headers=AUTH_HEADER)
        assert resp.status_code == 200
        body = resp.json()
        parsed = OpportunityResponse(**body)
        assert parsed.id == "gov-001"
        assert parsed.score > 0
        assert isinstance(parsed.matched_skills, list)

    def test_get_opportunity_not_found(self):
        resp = client.get("/api/v1/opportunities/nonexistent", headers=AUTH_HEADER)
        assert resp.status_code == 404
        body = resp.json()
        assert "detail" in body

    def test_opportunity_response_has_all_fields(self):
        resp = client.get("/api/v1/opportunities/gov-001", headers=AUTH_HEADER)
        body = resp.json()
        required_fields = {
            "id", "title", "organisation", "sector", "location",
            "source", "remuneration", "fee_display", "summary",
            "required_skills", "score", "band", "matched_skills",
            "missing_required", "missing_desirable", "rationale",
            "gap_actions",
        }
        assert required_fields.issubset(body.keys())


# --- Applications ---


class TestV1Applications:
    """Tests for GET /api/v1/applications."""

    def test_list_applications_success(self):
        resp = client.get("/api/v1/applications", headers=AUTH_HEADER)
        assert resp.status_code == 200
        body = resp.json()
        parsed = ApplicationResponse(**body)
        assert isinstance(parsed.applications, list)
        assert parsed.message


# --- Readiness ---


class TestV1Readiness:
    """Tests for GET /api/v1/readiness."""

    def test_get_readiness_success(self):
        resp = client.get("/api/v1/readiness", headers=AUTH_HEADER)
        assert resp.status_code == 200
        body = resp.json()
        parsed = ReadinessResponse(**body)
        assert isinstance(parsed.readiness_score, float)
        assert parsed.message


# --- Coaching ---


class TestV1Coaching:
    """Tests for POST /api/v1/coaching/board-cv."""

    def test_board_cv_success(self):
        resp = client.post("/api/v1/coaching/board-cv", headers=AUTH_HEADER)
        assert resp.status_code == 200
        body = resp.json()
        parsed = CoachingBoardCvResponse(**body)
        assert parsed.kind == "board_cv"
        assert parsed.content

    def test_board_cv_with_opportunity(self):
        resp = client.post(
            "/api/v1/coaching/board-cv",
            headers=AUTH_HEADER,
            params={"opportunity_id": "gov-002"},
        )
        assert resp.status_code == 200
        body = resp.json()
        parsed = CoachingBoardCvResponse(**body)
        assert "Priya Raman" in parsed.content

    def test_board_cv_unknown_opportunity(self):
        resp = client.post(
            "/api/v1/coaching/board-cv",
            headers=AUTH_HEADER,
            params={"opportunity_id": "nonexistent"},
        )
        assert resp.status_code == 404


# --- Authentication ---


class TestV1Authentication:
    """Tests for auth enforcement on v1 routes."""

    @pytest.mark.parametrize(
        "path,method",
        [
            ("/api/v1/opportunities", "get"),
            ("/api/v1/opportunities/gov-001", "get"),
            ("/api/v1/applications", "get"),
            ("/api/v1/readiness", "get"),
            ("/api/v1/coaching/board-cv", "post"),
        ],
    )
    def test_missing_auth_returns_401(self, path: str, method: str):
        """All v1 routes require authentication."""
        resp = getattr(client, method)(path)
        assert resp.status_code == 401
        body = resp.json()
        assert body["detail"] == "Authentication required"

    def test_auth_header_grants_access(self):
        """Providing X-Dev-User-Id header grants access."""
        resp = client.get("/api/v1/opportunities", headers={"X-Dev-User-Id": "alice"})
        assert resp.status_code == 200


# --- Legacy route compatibility ---


class TestLegacyRoutesUnaffected:
    """Legacy routes continue to work without authentication."""

    def test_legacy_opportunities_no_auth(self):
        resp = client.get("/api/opportunities")
        assert resp.status_code == 200

    def test_legacy_candidate_no_auth(self):
        resp = client.get("/api/candidate")
        assert resp.status_code == 200

    def test_legacy_readiness_no_auth(self):
        resp = client.get("/api/readiness")
        assert resp.status_code == 200

    def test_legacy_coach_no_auth(self):
        resp = client.post("/api/coach/board-cv")
        assert resp.status_code == 200


# --- OpenAPI documentation ---


class TestOpenAPIDocumentation:
    """v1 routes appear in OpenAPI schema."""

    def test_v1_routes_in_openapi(self):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        paths = schema["paths"]
        assert "/api/v1/opportunities" in paths
        assert "/api/v1/opportunities/{opportunity_id}" in paths
        assert "/api/v1/applications" in paths
        assert "/api/v1/readiness" in paths
        assert "/api/v1/coaching/board-cv" in paths

    def test_v1_schemas_in_openapi(self):
        resp = client.get("/openapi.json")
        schema = resp.json()
        components = schema.get("components", {}).get("schemas", {})
        assert "OpportunityListResponse" in components
        assert "OpportunityResponse" in components
        assert "ApplicationResponse" in components
        assert "ReadinessResponse" in components
        assert "CoachingBoardCvResponse" in components
