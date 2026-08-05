"""Tests for standardised API errors and observability (BM-013)."""

import uuid

from fastapi.testclient import TestClient

from boardmatch.api import app

client = TestClient(app)


class TestStructuredErrors:
    """Verify error responses follow the APIError schema."""

    def test_404_returns_structured_error(self):
        response = client.get("/api/opportunities/nonexistent-id-xyz")
        assert response.status_code == 404
        body = response.json()
        assert body["code"] == "not_found"
        assert "message" in body
        assert "request_id" in body
        # request_id should be a valid UUID
        uuid.UUID(body["request_id"])

    def test_422_validation_error_with_field_details(self):
        # POST /api/tracker expects a JSON body with opportunity_id and stage
        response = client.post("/api/tracker", json={})
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "validation_error"
        assert body["message"] == "Request validation failed"
        assert "request_id" in body
        uuid.UUID(body["request_id"])
        assert body["details"] is not None
        assert len(body["details"]) > 0
        # Each detail should identify the field
        for detail in body["details"]:
            assert "field" in detail
            assert "message" in detail

    def test_500_does_not_leak_internal_details(self):
        """Force an unhandled exception and verify no internals leak."""
        from boardmatch.api import app as _app
        from fastapi import APIRouter

        router = APIRouter()

        @router.get("/test-internal-error")
        def blow_up():
            raise RuntimeError("SECRET: database password is hunter2")

        _app.include_router(router)
        try:
            no_raise_client = TestClient(_app, raise_server_exceptions=False)
            resp = no_raise_client.get("/test-internal-error")
            assert resp.status_code == 500
            body = resp.json()
            assert body["code"] == "internal_error"
            assert body["message"] == "An internal error occurred"
            assert "request_id" in body
            uuid.UUID(body["request_id"])
            # Must not leak internal exception details
            assert "hunter2" not in resp.text
            assert "SECRET" not in resp.text
            assert body.get("details") is None
        finally:
            # Clean up the test route
            _app.router.routes = [
                r for r in _app.router.routes
                if getattr(r, "path", "") != "/test-internal-error"
            ]


class TestRequestID:
    """Verify request ID middleware."""

    def test_request_id_in_response_headers(self):
        response = client.get("/api/candidate")
        assert "X-Request-ID" in response.headers
        # Should be a valid UUID
        uuid.UUID(response.headers["X-Request-ID"])

    def test_request_id_matches_error_body(self):
        response = client.get("/api/opportunities/nonexistent-xyz")
        header_id = response.headers["X-Request-ID"]
        body_id = response.json()["request_id"]
        assert header_id == body_id


class TestHealthEndpoints:
    """Verify health check endpoints."""

    def test_liveness_returns_200(self):
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_readiness_returns_200(self):
        response = client.get("/health/ready")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
