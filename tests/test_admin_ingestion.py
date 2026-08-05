"""Tests for BM-021: Admin ingestion operations."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from boardmatch.api.v1.admin import (
    IngestionRunResponse,
    SyncResponse,
    _SOURCE_REGISTRY,
    _active_sources,
    _runs,
    require_admin,
    reset_state,
    router,
)
from boardmatch.auth import CurrentUser, get_current_user
from boardmatch.config import AppEnvironment, Settings, get_settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    """Create a minimal FastAPI app with admin routes for testing."""
    from fastapi import APIRouter

    app = FastAPI()
    v1 = APIRouter(prefix="/api/v1")
    v1.include_router(router)
    app.include_router(v1)

    # Override settings for test environment
    def _override_settings() -> Settings:
        return Settings(app_env=AppEnvironment.TEST)

    app.dependency_overrides[get_settings] = _override_settings
    return app


def _admin_user() -> CurrentUser:
    return CurrentUser(
        user_id="admin-001",
        email="admin@boardmatch.local",
        display_name="Admin User",
        roles=["user", "admin"],
    )


def _regular_user() -> CurrentUser:
    return CurrentUser(
        user_id="user-001",
        email="user@boardmatch.local",
        display_name="Regular User",
        roles=["user"],
    )


@pytest.fixture
def admin_client():
    """Test client authenticated as admin."""
    app = _make_app()
    app.dependency_overrides[get_current_user] = _admin_user
    reset_state()
    yield TestClient(app)
    reset_state()


@pytest.fixture
def user_client():
    """Test client authenticated as regular user."""
    app = _make_app()
    app.dependency_overrides[get_current_user] = _regular_user
    reset_state()
    yield TestClient(app)
    reset_state()


# ---------------------------------------------------------------------------
# Authorization tests
# ---------------------------------------------------------------------------


class TestAdminAuthorization:
    """Admin routes require admin role."""

    def test_sync_requires_admin(self, user_client: TestClient):
        """POST /sync returns 403 for non-admin users."""
        resp = user_client.post("/api/v1/admin/sources/gov_vacancies/sync")
        assert resp.status_code == 403
        assert "Admin access required" in resp.json()["detail"]

    def test_list_runs_requires_admin(self, user_client: TestClient):
        """GET /ingestion-runs returns 403 for non-admin users."""
        resp = user_client.get("/api/v1/admin/ingestion-runs")
        assert resp.status_code == 403

    def test_get_run_requires_admin(self, user_client: TestClient):
        """GET /ingestion-runs/{id} returns 403 for non-admin users."""
        resp = user_client.get("/api/v1/admin/ingestion-runs/some-id")
        assert resp.status_code == 403

    def test_admin_can_access_sync(self, admin_client: TestClient):
        """POST /sync succeeds for admin users."""
        resp = admin_client.post("/api/v1/admin/sources/gov_vacancies/sync")
        assert resp.status_code == 202

    def test_admin_can_list_runs(self, admin_client: TestClient):
        """GET /ingestion-runs succeeds for admin users."""
        resp = admin_client.get("/api/v1/admin/ingestion-runs")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Sync trigger tests
# ---------------------------------------------------------------------------


class TestTriggerSync:
    """Tests for POST /admin/sources/{source_id}/sync."""

    def test_trigger_sync_success(self, admin_client: TestClient):
        """Triggering sync for a known source returns 202 with run details."""
        resp = admin_client.post("/api/v1/admin/sources/gov_vacancies/sync")
        assert resp.status_code == 202
        data = resp.json()
        assert "run_id" in data
        assert data["source_id"] == "gov_vacancies"
        assert data["status"] in ("succeeded", "failed")

    def test_trigger_sync_unknown_source(self, admin_client: TestClient):
        """Triggering sync for unknown source returns 404."""
        resp = admin_client.post("/api/v1/admin/sources/nonexistent/sync")
        assert resp.status_code == 404
        assert "Unknown source" in resp.json()["detail"]

    def test_trigger_sync_concurrent_prevention(self, admin_client: TestClient):
        """Concurrent runs for same source are prevented with 409."""
        # Simulate an active source
        _active_sources.add("gov_vacancies")
        try:
            resp = admin_client.post("/api/v1/admin/sources/gov_vacancies/sync")
            assert resp.status_code == 409
            assert "already has a running ingestion" in resp.json()["detail"]
        finally:
            _active_sources.discard("gov_vacancies")

    def test_trigger_sync_records_run(self, admin_client: TestClient):
        """A completed sync stores the run in memory."""
        resp = admin_client.post("/api/v1/admin/sources/gov_vacancies/sync")
        assert resp.status_code == 202
        run_id = resp.json()["run_id"]
        assert run_id in _runs


# ---------------------------------------------------------------------------
# Ingestion run listing tests
# ---------------------------------------------------------------------------


class TestListIngestionRuns:
    """Tests for GET /admin/ingestion-runs."""

    def test_empty_list(self, admin_client: TestClient):
        """Returns empty list when no runs exist."""
        resp = admin_client.get("/api/v1/admin/ingestion-runs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_after_sync(self, admin_client: TestClient):
        """Lists runs after a sync has been triggered."""
        admin_client.post("/api/v1/admin/sources/gov_vacancies/sync")
        resp = admin_client.get("/api/v1/admin/ingestion-runs")
        assert resp.status_code == 200
        runs = resp.json()
        assert len(runs) == 1
        assert runs[0]["source_id"] == "gov_vacancies"

    def test_list_multiple_runs(self, admin_client: TestClient):
        """Lists multiple runs from different sources."""
        admin_client.post("/api/v1/admin/sources/gov_vacancies/sync")
        admin_client.post("/api/v1/admin/sources/mock_sources/sync")
        resp = admin_client.get("/api/v1/admin/ingestion-runs")
        assert resp.status_code == 200
        runs = resp.json()
        assert len(runs) == 2


# ---------------------------------------------------------------------------
# Get specific run tests
# ---------------------------------------------------------------------------


class TestGetIngestionRun:
    """Tests for GET /admin/ingestion-runs/{run_id}."""

    def test_get_existing_run(self, admin_client: TestClient):
        """Returns full details for an existing run."""
        sync_resp = admin_client.post("/api/v1/admin/sources/gov_vacancies/sync")
        run_id = sync_resp.json()["run_id"]

        resp = admin_client.get(f"/api/v1/admin/ingestion-runs/{run_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == run_id
        assert data["source_id"] == "gov_vacancies"
        assert data["status"] in ("succeeded", "failed")
        assert "started_at" in data
        assert "records_created" in data
        assert "records_updated" in data
        assert "records_deactivated" in data

    def test_get_nonexistent_run(self, admin_client: TestClient):
        """Returns 404 for unknown run_id."""
        resp = admin_client.get("/api/v1/admin/ingestion-runs/no-such-run")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Status tracking tests
# ---------------------------------------------------------------------------


class TestStatusTracking:
    """Tests for run status transitions and metrics."""

    def test_successful_run_has_metrics(self, admin_client: TestClient):
        """A successful run records metrics."""
        sync_resp = admin_client.post("/api/v1/admin/sources/gov_vacancies/sync")
        run_id = sync_resp.json()["run_id"]

        resp = admin_client.get(f"/api/v1/admin/ingestion-runs/{run_id}")
        data = resp.json()

        if data["status"] == "succeeded":
            assert data["records_created"] >= 0
            assert data["records_updated"] >= 0
            assert data["records_deactivated"] >= 0
            assert data["started_at"] is not None
            assert data["completed_at"] is not None
            assert data["error_message"] is None

    def test_failed_run_has_error(self, admin_client: TestClient):
        """A failed run records error details."""
        from boardmatch.ingestion.base import SourceError
        from boardmatch.ingestion.json_source import JsonFileSource

        # Register a source that will fail
        _SOURCE_REGISTRY["broken_source"] = lambda: JsonFileSource(
            "nonexistent_file.json", source_key="broken_source"
        )
        try:
            sync_resp = admin_client.post("/api/v1/admin/sources/broken_source/sync")
            run_id = sync_resp.json()["run_id"]

            resp = admin_client.get(f"/api/v1/admin/ingestion-runs/{run_id}")
            data = resp.json()
            assert data["status"] == "failed"
            assert data["error_message"] is not None
            assert len(data["error_message"]) > 0
        finally:
            del _SOURCE_REGISTRY["broken_source"]
