"""Tests for account management API (BM-030): audit, export, deletion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from boardmatch.api import app
from boardmatch.api.v1.account import (
    _application_repo,
    _audit_logger,
    _candidate_repo,
    _draft_repo,
    _integration_repo,
)
from boardmatch.audit import AuditAction, AuditEvent, AuditLogger
from boardmatch.drafts import Draft
from boardmatch.integrations import Integration, IntegrationStatus
from boardmatch.models import Application, ApplicationStage, Candidate


@pytest.fixture(autouse=True)
def _reset_state():
    """Clear all repository state between tests."""
    _audit_logger.clear()
    _candidate_repo._store.clear()
    _application_repo._store.clear()
    _draft_repo._store.clear()
    _integration_repo._integrations.clear()
    _integration_repo._audit_events.clear()
    yield
    _audit_logger.clear()
    _candidate_repo._store.clear()
    _application_repo._store.clear()
    _draft_repo._store.clear()
    _integration_repo._integrations.clear()
    _integration_repo._audit_events.clear()


@pytest.fixture
def client():
    return TestClient(app)


def _headers(user_id: str = "user-001") -> dict[str, str]:
    return {"X-Dev-User-Id": user_id}


def _seed_profile(user_id: str = "user-001") -> Candidate:
    candidate = Candidate(
        name="Jane Doe",
        headline="Experienced Director",
        years_experience=15,
        skills=["governance", "strategy"],
        sectors=["Technology"],
        credentials=["GAICD"],
        board_experience=["Chair of Acme Inc"],
        achievements=["Led IPO"],
        locations=["Melbourne"],
        connections=[],
    )
    _candidate_repo._store[user_id] = candidate
    return candidate


def _seed_application(user_id: str = "user-001") -> Application:
    app_obj = Application(
        opportunity_id="opp-1",
        stage=ApplicationStage.RESEARCHING,
        notes="Interested",
    )
    _application_repo.create(user_id, app_obj)
    return app_obj


def _seed_draft(user_id: str = "user-001") -> Draft:
    draft = Draft(
        id="draft-1",
        user_id=user_id,
        draft_type="board_cv",
        content="My board CV content",
        engine="template",
    )
    _draft_repo.create(draft)
    return draft


def _seed_integration(user_id: str = "user-001") -> Integration:
    integration = Integration(
        user_id=user_id,
        provider="microsoft",
        status=IntegrationStatus.ACTIVE,
        scopes=["User.Read"],
    )
    _integration_repo.save(integration)
    return integration


class TestAuditEvents:
    """GET /api/v1/account/audit-events"""

    def test_returns_empty_list_initially(self, client):
        resp = client.get("/api/v1/account/audit-events", headers=_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["events"] == []

    def test_returns_logged_events(self, client):
        _audit_logger.log("user-001", AuditAction.LOGIN)
        _audit_logger.log("user-001", AuditAction.PROFILE_UPDATED, "profile")
        resp = client.get("/api/v1/account/audit-events", headers=_headers())
        assert resp.status_code == 200
        events = resp.json()["events"]
        assert len(events) == 2
        assert events[0]["action"] == "profile_updated"
        assert events[1]["action"] == "login"

    def test_user_isolation(self, client):
        _audit_logger.log("user-001", AuditAction.LOGIN)
        _audit_logger.log("user-002", AuditAction.PROFILE_UPDATED)
        resp = client.get("/api/v1/account/audit-events", headers=_headers("user-002"))
        assert resp.status_code == 200
        events = resp.json()["events"]
        assert len(events) == 1
        assert events[0]["action"] == "profile_updated"

    def test_requires_auth(self, client):
        resp = client.get("/api/v1/account/audit-events")
        assert resp.status_code == 401


class TestExport:
    """POST /api/v1/account/export"""

    def test_export_empty_account(self, client):
        resp = client.post("/api/v1/account/export", headers=_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "user-001"
        assert data["profile"] is None
        assert data["applications"] == []
        assert data["drafts"] == []
        assert data["consents"] == []

    def test_export_includes_profile(self, client):
        _seed_profile()
        resp = client.post("/api/v1/account/export", headers=_headers())
        data = resp.json()
        assert data["profile"]["name"] == "Jane Doe"
        assert data["profile"]["skills"] == ["governance", "strategy"]

    def test_export_includes_applications(self, client):
        _seed_application()
        resp = client.post("/api/v1/account/export", headers=_headers())
        data = resp.json()
        assert len(data["applications"]) == 1
        assert data["applications"][0]["opportunity_id"] == "opp-1"

    def test_export_includes_drafts(self, client):
        _seed_draft()
        resp = client.post("/api/v1/account/export", headers=_headers())
        data = resp.json()
        assert len(data["drafts"]) == 1
        assert data["drafts"][0]["draft_type"] == "board_cv"

    def test_export_includes_consents(self, client):
        _seed_integration()
        resp = client.post("/api/v1/account/export", headers=_headers())
        data = resp.json()
        assert len(data["consents"]) == 1
        assert data["consents"][0]["provider"] == "microsoft"

    def test_export_creates_audit_event(self, client):
        client.post("/api/v1/account/export", headers=_headers())
        events = _audit_logger.get_events("user-001")
        assert len(events) == 1
        assert events[0].action == AuditAction.EXPORT_REQUESTED

    def test_requires_auth(self, client):
        resp = client.post("/api/v1/account/export")
        assert resp.status_code == 401


class TestDeleteAccount:
    """DELETE /api/v1/account"""

    def test_delete_empty_account(self, client):
        resp = client.delete("/api/v1/account", headers=_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert "deleted_at" in data

    def test_delete_removes_profile(self, client):
        _seed_profile()
        client.delete("/api/v1/account", headers=_headers())
        assert _candidate_repo.get_for_user("user-001") is None

    def test_delete_removes_applications(self, client):
        _seed_application()
        client.delete("/api/v1/account", headers=_headers())
        assert _application_repo.list_for_user("user-001") == []

    def test_delete_removes_drafts(self, client):
        _seed_draft()
        client.delete("/api/v1/account", headers=_headers())
        assert _draft_repo.list_for_user("user-001") == []

    def test_delete_revokes_integrations(self, client):
        _seed_integration()
        client.delete("/api/v1/account", headers=_headers())
        integration = _integration_repo.get("user-001", "microsoft")
        assert integration.status == IntegrationStatus.REVOKED
        assert integration.token_hash is None

    def test_delete_creates_audit_event(self, client):
        client.delete("/api/v1/account", headers=_headers())
        events = _audit_logger.get_events("user-001")
        assert len(events) == 1
        assert events[0].action == AuditAction.ACCOUNT_DELETED
        assert events[0].details == {"anonymised": True}

    def test_delete_user_isolation(self, client):
        _seed_profile("user-001")
        _seed_profile("user-002")
        client.delete("/api/v1/account", headers=_headers("user-001"))
        assert _candidate_repo.get_for_user("user-002") is not None

    def test_requires_auth(self, client):
        resp = client.delete("/api/v1/account")
        assert resp.status_code == 401


class TestAuditRetention:
    """Audit logger retention policy."""

    def test_purge_removes_old_events(self):
        logger = AuditLogger(retention_days=30)
        old_event = AuditEvent(
            id="old-1",
            user_id="user-001",
            action=AuditAction.LOGIN,
            timestamp=datetime.now(timezone.utc) - timedelta(days=31),
        )
        logger._events.append(old_event)
        logger.log("user-001", AuditAction.PROFILE_UPDATED)

        assert len(logger._events) == 2
        purged = logger.purge_expired()
        assert purged == 1
        assert len(logger._events) == 1

    def test_expired_events_excluded_from_get(self):
        logger = AuditLogger(retention_days=30)
        old_event = AuditEvent(
            id="old-1",
            user_id="user-001",
            action=AuditAction.LOGIN,
            timestamp=datetime.now(timezone.utc) - timedelta(days=31),
        )
        logger._events.append(old_event)
        logger.log("user-001", AuditAction.PROFILE_UPDATED)

        events = logger.get_events("user-001")
        assert len(events) == 1
        assert events[0].action == AuditAction.PROFILE_UPDATED
