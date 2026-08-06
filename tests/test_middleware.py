"""Tests for security headers, CORS, and rate-limiting middleware."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from boardmatch.api.middleware import (
    RateLimitMiddleware,
    register_middleware,
)
from boardmatch.api.v1.rate_limit import RateLimiter
from boardmatch.config import AppEnvironment, Settings


def _build_app(**settings_kwargs) -> FastAPI:
    app = FastAPI()

    @app.get("/api/v1/coaching/ping")
    def coaching_ping() -> dict:
        return {"ok": True}

    @app.get("/api/v1/opportunities/ping")
    def opportunities_ping() -> dict:
        return {"ok": True}

    settings = Settings(**settings_kwargs)
    register_middleware(app, settings=settings)
    return app


class TestSecurityHeaders:
    def test_adds_baseline_security_headers(self):
        app = _build_app(app_env=AppEnvironment.LOCAL)
        client = TestClient(app)

        resp = client.get("/api/v1/opportunities/ping")

        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    def test_hsts_only_added_in_production(self):
        local_app = _build_app(app_env=AppEnvironment.LOCAL)
        local_resp = TestClient(local_app).get("/api/v1/opportunities/ping")
        assert "Strict-Transport-Security" not in local_resp.headers

        prod_app = _build_app(
            app_env=AppEnvironment.PRODUCTION,
            database_url="postgresql://db.example/boardmatch",
            auth_issuer="https://login.example.com/tenant",
            auth_audience="api://boardmatch",
            azure_openai_endpoint="https://boardmatch.openai.azure.com",
            azure_openai_api_key="secret-key",
            azure_openai_deployment="gpt-4o",
            azure_storage_account="boardmatchdata",
        )
        prod_resp = TestClient(prod_app).get("/api/v1/opportunities/ping")
        assert "max-age" in prod_resp.headers["Strict-Transport-Security"]


class TestCORS:
    def test_disallows_unconfigured_origin(self):
        app = _build_app(app_env=AppEnvironment.LOCAL)
        client = TestClient(app)

        resp = client.get(
            "/api/v1/opportunities/ping", headers={"Origin": "https://evil.example.com"}
        )

        assert "access-control-allow-origin" not in resp.headers

    def test_allows_configured_origin(self):
        app = _build_app(
            app_env=AppEnvironment.LOCAL,
            cors_allowed_origins=("https://app.example.com",),
        )
        client = TestClient(app)

        resp = client.get(
            "/api/v1/opportunities/ping", headers={"Origin": "https://app.example.com"}
        )

        assert resp.headers["access-control-allow-origin"] == "https://app.example.com"


class TestRateLimitMiddleware:
    def test_blocks_requests_over_threshold_on_sensitive_paths(self):
        app = _build_app(
            app_env=AppEnvironment.LOCAL,
            rate_limit_max_requests=2,
            rate_limit_window_seconds=60,
        )
        client = TestClient(app)

        assert client.get("/api/v1/coaching/ping").status_code == 200
        assert client.get("/api/v1/coaching/ping").status_code == 200
        third = client.get("/api/v1/coaching/ping")
        assert third.status_code == 429

    def test_does_not_limit_non_sensitive_paths(self):
        app = _build_app(
            app_env=AppEnvironment.LOCAL,
            rate_limit_max_requests=1,
            rate_limit_window_seconds=60,
        )
        client = TestClient(app)

        for _ in range(5):
            resp = client.get("/api/v1/opportunities/ping")
            assert resp.status_code == 200

    def test_limits_are_keyed_per_user(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        app = FastAPI()

        @app.get("/api/v1/coaching/ping")
        def ping() -> dict:
            return {"ok": True}

        app.add_middleware(RateLimitMiddleware, limiter=limiter)
        client = TestClient(app)

        alice_first = client.get(
            "/api/v1/coaching/ping", headers={"X-Dev-User-Id": "alice"}
        )
        alice_second = client.get(
            "/api/v1/coaching/ping", headers={"X-Dev-User-Id": "alice"}
        )
        bob_first = client.get(
            "/api/v1/coaching/ping", headers={"X-Dev-User-Id": "bob"}
        )

        assert alice_first.status_code == 200
        assert alice_second.status_code == 429
        assert bob_first.status_code == 200
