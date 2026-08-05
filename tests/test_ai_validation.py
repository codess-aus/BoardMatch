"""Tests for AI output validation and safety controls (BM-028)."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from boardmatch.validation import (
    ValidationResult,
    validate_bio,
    validate_board_cv,
    validate_draft,
    validate_generated_label,
    validate_length,
    validate_outreach,
)
from boardmatch.api.v1.rate_limit import RateLimiter


class TestValidationResult:
    def test_valid_result(self):
        result = ValidationResult(valid=True, errors=[])
        assert result.valid is True
        assert result.errors == []

    def test_invalid_result(self):
        result = ValidationResult(valid=False, errors=["Something wrong"])
        assert result.valid is False
        assert "Something wrong" in result.errors


class TestValidateBoardCv:
    def test_valid_board_cv(self):
        content = (
            "# Jane Doe\n\n"
            "## Board value proposition\n"
            "Summary of experience.\n\n"
            "## Governance experience\n"
            "- Board member at Acme Corp\n"
        )
        result = validate_board_cv(content)
        assert result.valid is True
        assert result.errors == []

    def test_empty_content(self):
        result = validate_board_cv("")
        assert result.valid is False
        assert any("empty" in e.lower() for e in result.errors)

    def test_whitespace_only(self):
        result = validate_board_cv("   \n\n  ")
        assert result.valid is False

    def test_missing_summary(self):
        content = "## Governance experience\n- Board member\n"
        result = validate_board_cv(content)
        assert result.valid is False
        assert any("summary" in e.lower() or "proposition" in e.lower() for e in result.errors)

    def test_missing_experience(self):
        content = "## Board value proposition\nSummary of strengths.\n"
        result = validate_board_cv(content)
        assert result.valid is False
        assert any("experience" in e.lower() for e in result.errors)

    def test_accepts_value_proposition_as_summary(self):
        content = (
            "## Board value proposition\n"
            "Strong leader.\n\n"
            "## Governance experience\n"
            "- Director at XYZ\n"
        )
        result = validate_board_cv(content)
        assert result.valid is True


class TestValidateBio:
    def test_valid_bio(self):
        content = (
            "Jane Doe is a non-executive director candidate with 15 years "
            "experience across technology and finance. She brings board-level "
            "strength in strategy, governance, and risk management."
        )
        result = validate_bio(content)
        assert result.valid is True

    def test_empty_bio(self):
        result = validate_bio("")
        assert result.valid is False
        assert any("empty" in e.lower() for e in result.errors)

    def test_no_sentence(self):
        result = validate_bio("Just some words without a period and more words here to fill up")
        assert result.valid is False
        assert any("paragraph" in e.lower() or "sentence" in e.lower() for e in result.errors)

    def test_too_short(self):
        result = validate_bio("Short.")
        assert result.valid is False
        assert any("short" in e.lower() for e in result.errors)


class TestValidateOutreach:
    def test_valid_outreach(self):
        content = (
            "Subject: Board Director\n\n"
            "Dear Nominations Committee,\n\n"
            "I am writing to express interest in the role. "
            "I would welcome a conversation.\n\n"
            "Kind regards,\nJane Doe\n"
        )
        result = validate_outreach(content)
        assert result.valid is True

    def test_empty_outreach(self):
        result = validate_outreach("")
        assert result.valid is False

    def test_missing_greeting(self):
        content = (
            "I am writing to express interest. "
            "I would welcome a conversation."
        )
        result = validate_outreach(content)
        assert result.valid is False
        assert any("greeting" in e.lower() for e in result.errors)

    def test_missing_ask(self):
        content = "Dear Nominations Committee,\nHere is my CV.\n"
        result = validate_outreach(content)
        assert result.valid is False
        assert any("ask" in e.lower() or "call-to-action" in e.lower() for e in result.errors)


class TestValidateLength:
    def test_within_limit(self):
        result = validate_length("Hello world", max_chars=5000)
        assert result.valid is True

    def test_at_limit(self):
        result = validate_length("x" * 5000, max_chars=5000)
        assert result.valid is True

    def test_exceeds_limit(self):
        result = validate_length("x" * 5001, max_chars=5000)
        assert result.valid is False
        assert any("exceeds" in e.lower() for e in result.errors)

    def test_empty_content(self):
        result = validate_length("", max_chars=5000)
        assert result.valid is True

    def test_custom_limit(self):
        result = validate_length("abc", max_chars=2)
        assert result.valid is False


class TestValidateGeneratedLabel:
    def test_template_engine(self):
        result = validate_generated_label("template")
        assert result.valid is True

    def test_azure_openai_engine(self):
        result = validate_generated_label("azure-openai")
        assert result.valid is True

    def test_invalid_engine(self):
        result = validate_generated_label("unknown-engine")
        assert result.valid is False

    def test_empty_engine(self):
        result = validate_generated_label("")
        assert result.valid is False


class TestValidateDraft:
    def test_valid_board_cv_draft(self):
        content = (
            "## Board value proposition\n"
            "Strong in governance.\n\n"
            "## Governance experience\n"
            "- Director at Corp\n"
        )
        result = validate_draft(content, "board_cv", "template")
        assert result.valid is True

    def test_invalid_draft_type(self):
        result = validate_draft("Some content.", "unknown_type", "template")
        assert result.valid is False
        assert any("unknown" in e.lower() for e in result.errors)

    def test_multiple_errors(self):
        result = validate_draft("", "board_cv", "bad-engine")
        assert result.valid is False
        assert len(result.errors) >= 2

    def test_length_exceeded_with_valid_content(self):
        content = (
            "## Board value proposition\nSummary.\n\n"
            "## Governance experience\n" + "x" * 5000
        )
        result = validate_draft(content, "board_cv", "template")
        assert result.valid is False
        assert any("exceeds" in e.lower() for e in result.errors)


class TestRateLimiter:
    def test_allows_within_limit(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        assert limiter.is_allowed("user1") is True
        limiter.record("user1")
        assert limiter.is_allowed("user1") is True
        limiter.record("user1")
        assert limiter.is_allowed("user1") is True
        limiter.record("user1")
        assert limiter.is_allowed("user1") is False

    def test_different_users_independent(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        limiter.record("user1")
        assert limiter.is_allowed("user1") is False
        assert limiter.is_allowed("user2") is True

    def test_window_expiration(self):
        limiter = RateLimiter(max_requests=1, window_seconds=1)
        limiter.record("user1")
        assert limiter.is_allowed("user1") is False
        time.sleep(1.1)
        assert limiter.is_allowed("user1") is True

    def test_remaining_count(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        assert limiter.remaining("user1") == 5
        limiter.record("user1")
        limiter.record("user1")
        assert limiter.remaining("user1") == 3

    def test_reset(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        limiter.record("user1")
        assert limiter.is_allowed("user1") is False
        limiter.reset("user1")
        assert limiter.is_allowed("user1") is True

    def test_default_10_per_hour(self):
        limiter = RateLimiter()
        assert limiter.max_requests == 10
        assert limiter.window_seconds == 3600


class TestEndpointValidation:
    """Test that coaching endpoints enforce validation and rate limits."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from boardmatch.api.v1.coaching import router
        from boardmatch.api.v1.rate_limit import draft_rate_limiter
        from boardmatch.auth import CurrentUser, get_required_user
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)

        mock_user = CurrentUser(user_id="test-user-id", display_name="Test User")
        app.dependency_overrides[get_required_user] = lambda: mock_user

        draft_rate_limiter.reset("test-user-id")

        yield TestClient(app)

        app.dependency_overrides.clear()

    def test_board_cv_passes_validation(self, client):
        resp = client.post("/coaching/board-cv")
        assert resp.status_code == 200
        data = resp.json()
        assert data["engine"] in ("template", "azure-openai")

    def test_director_bio_passes_validation(self, client):
        resp = client.post("/coaching/director-bio")
        assert resp.status_code == 200

    def test_rate_limit_returns_429(self, client):
        from boardmatch.api.v1.rate_limit import draft_rate_limiter

        for _ in range(10):
            draft_rate_limiter.record("test-user-id")

        resp = client.post("/coaching/board-cv")
        assert resp.status_code == 429
        assert "rate limit" in resp.json()["detail"].lower()

    def test_validation_failure_returns_422(self, client):
        from boardmatch.coach import Draft as CoachDraft

        bad_draft = CoachDraft(kind="board_cv", content="", engine="template")
        with patch("boardmatch.api.v1.coaching.coach.board_cv", return_value=bad_draft):
            resp = client.post("/coaching/board-cv")
            assert resp.status_code == 422
            assert "validation failed" in resp.json()["detail"].lower()
