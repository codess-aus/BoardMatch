"""Tests for GovBoardVacancySource and the ingestion runner."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from boardmatch.ingestion.base import (
    SourceAuthError,
    SourceError,
    SourceRateLimitError,
    SourceTimeoutError,
)
from boardmatch.ingestion.gov_source import GovBoardVacancySource
from boardmatch.ingestion.models import IngestionStatus
from boardmatch.ingestion.runner import run_ingestion
from boardmatch.models import Opportunity, Remuneration

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_VACANCY = {
    "id": "gov-001",
    "title": "Board Member – Health Advisory Council",
    "organisation": "Department of Health",
    "sector": "Health",
    "location": "Canberra, ACT",
    "url": "https://boardvacancies.gov.au/gov-001",
    "remuneration": "paid",
    "fee_aud": 45000,
    "closes_on": "2025-09-30",
    "summary": "Seeking experienced health professionals.",
    "required_skills": ["governance", "health policy"],
    "desirable_skills": ["finance"],
}

SAMPLE_VACANCY_2 = {
    "id": "gov-002",
    "title": "Non-Executive Director – Transport Authority",
    "organisation": "Transport Authority",
    "sector": "Transport",
    "location": "Sydney, NSW",
    "url": "https://boardvacancies.gov.au/gov-002",
    "remuneration": "voluntary",
    "closes_on": "2025-10-15",
    "summary": "Community representative role.",
    "required_skills": ["community engagement"],
    "desirable_skills": [],
}


def _mock_response(
    status_code: int = 200,
    json_data: object = None,
    raise_for_status: bool = False,
) -> MagicMock:
    """Create a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    if raise_for_status:
        resp.raise_for_status.side_effect = requests.HTTPError()
    return resp


@pytest.fixture
def source() -> GovBoardVacancySource:
    """A source instance with a test URL."""
    return GovBoardVacancySource(
        url="https://test.example.com/vacancies", timeout=5
    )


# ---------------------------------------------------------------------------
# Tests: GovBoardVacancySource.fetch()
# ---------------------------------------------------------------------------


class TestGovBoardVacancySourceFetch:
    """Tests for the HTTP source adapter."""

    @patch("boardmatch.ingestion.gov_source.requests.get")
    def test_fetch_creates_opportunity_records(self, mock_get, source):
        """Fixture response is normalised into Opportunity objects."""
        mock_get.return_value = _mock_response(
            json_data=[SAMPLE_VACANCY, SAMPLE_VACANCY_2]
        )

        results = source.fetch()

        assert len(results) == 2
        assert all(isinstance(r, Opportunity) for r in results)
        assert results[0].id == "gov-001"
        assert results[0].title == "Board Member – Health Advisory Council"
        assert results[0].remuneration == Remuneration.PAID
        assert results[0].fee_aud == 45000
        assert results[0].source == "gov_board_vacancies"
        assert results[1].id == "gov-002"
        assert results[1].remuneration == Remuneration.VOLUNTARY

    @patch("boardmatch.ingestion.gov_source.requests.get")
    def test_fetch_handles_envelope_response(self, mock_get, source):
        """Supports {"results": [...]} envelope format."""
        mock_get.return_value = _mock_response(
            json_data={"results": [SAMPLE_VACANCY]}
        )

        results = source.fetch()

        assert len(results) == 1
        assert results[0].id == "gov-001"

    @patch("boardmatch.ingestion.gov_source.requests.get")
    def test_fetch_timeout_raises_source_timeout_error(self, mock_get, source):
        """HTTP timeout raises SourceTimeoutError."""
        mock_get.side_effect = requests.exceptions.Timeout("timed out")

        with pytest.raises(SourceTimeoutError, match="Timed out"):
            source.fetch()

    @patch("boardmatch.ingestion.gov_source.requests.get")
    def test_fetch_rate_limit_raises_source_rate_limit_error(
        self, mock_get, source
    ):
        """HTTP 429 raises SourceRateLimitError."""
        mock_get.return_value = _mock_response(status_code=429)

        with pytest.raises(SourceRateLimitError, match="429"):
            source.fetch()

    @patch("boardmatch.ingestion.gov_source.requests.get")
    def test_fetch_auth_error_401(self, mock_get, source):
        """HTTP 401 raises SourceAuthError."""
        mock_get.return_value = _mock_response(status_code=401)

        with pytest.raises(SourceAuthError, match="401"):
            source.fetch()

    @patch("boardmatch.ingestion.gov_source.requests.get")
    def test_fetch_auth_error_403(self, mock_get, source):
        """HTTP 403 raises SourceAuthError."""
        mock_get.return_value = _mock_response(status_code=403)

        with pytest.raises(SourceAuthError, match="403"):
            source.fetch()

    @patch("boardmatch.ingestion.gov_source.requests.get")
    def test_fetch_malformed_response_skips_bad_records(
        self, mock_get, source
    ):
        """Malformed individual records are skipped, valid ones kept."""
        malformed = {"bad": "data"}  # missing required fields
        mock_get.return_value = _mock_response(
            json_data=[SAMPLE_VACANCY, malformed, SAMPLE_VACANCY_2]
        )

        results = source.fetch()

        assert len(results) == 2
        assert results[0].id == "gov-001"
        assert results[1].id == "gov-002"

    @patch("boardmatch.ingestion.gov_source.requests.get")
    def test_fetch_completely_malformed_json(self, mock_get, source):
        """Non-JSON response raises SourceError."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("No JSON")
        mock_get.return_value = resp

        with pytest.raises(SourceError, match="Invalid JSON"):
            source.fetch()

    @patch("boardmatch.ingestion.gov_source.requests.get")
    def test_fetch_sends_api_key_header(self, mock_get):
        """API key is sent as Bearer token when configured."""
        src = GovBoardVacancySource(
            url="https://test.example.com/vacancies",
            api_key="secret-key",
        )
        mock_get.return_value = _mock_response(json_data=[])

        src.fetch()

        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["headers"]["Authorization"] == "Bearer secret-key"


# ---------------------------------------------------------------------------
# Tests: Ingestion runner
# ---------------------------------------------------------------------------


class TestIngestionRunner:
    """Tests for run_ingestion orchestrator."""

    @patch("boardmatch.ingestion.gov_source.requests.get")
    def test_successful_run_creates_ingestion_run(self, mock_get, source):
        """Successful fetch produces SUCCEEDED IngestionRun."""
        mock_get.return_value = _mock_response(
            json_data=[SAMPLE_VACANCY, SAMPLE_VACANCY_2]
        )

        run = run_ingestion(source)

        assert run.status == IngestionStatus.SUCCEEDED
        assert run.source_key == "gov_board_vacancies"
        assert run.records_fetched == 2
        assert run.records_stored == 2
        assert run.records_created == 2
        assert run.records_updated == 0
        assert run.records_deactivated == 0
        assert run.started_at is not None
        assert run.completed_at is not None

    @patch("boardmatch.ingestion.gov_source.requests.get")
    def test_existing_record_update(self, mock_get, source):
        """Records already in existing dict are counted as updated."""
        mock_get.return_value = _mock_response(
            json_data=[SAMPLE_VACANCY, SAMPLE_VACANCY_2]
        )
        existing_opp = Opportunity(
            id="gov-001",
            title="Old Title",
            organisation="Department of Health",
            sector="Health",
            location="Canberra, ACT",
            source="gov_board_vacancies",
            url="https://example.com/gov-001",
            remuneration=Remuneration.PAID,
        )

        run = run_ingestion(source, existing={"gov-001": existing_opp})

        assert run.records_created == 1  # gov-002 is new
        assert run.records_updated == 1  # gov-001 existed
        assert run.records_deactivated == 0

    @patch("boardmatch.ingestion.gov_source.requests.get")
    def test_withdrawn_record_handling(self, mock_get, source):
        """Records absent from fetch but in existing are deactivated."""
        mock_get.return_value = _mock_response(
            json_data=[SAMPLE_VACANCY]  # only gov-001
        )
        existing_opp_1 = Opportunity(
            id="gov-001",
            title="Board Member",
            organisation="Dept Health",
            sector="Health",
            location="Canberra",
            source="gov_board_vacancies",
            url="https://example.com/gov-001",
            remuneration=Remuneration.PAID,
        )
        existing_opp_2 = Opportunity(
            id="gov-002",
            title="Old Position",
            organisation="Transport",
            sector="Transport",
            location="Sydney",
            source="gov_board_vacancies",
            url="https://example.com/gov-002",
            remuneration=Remuneration.VOLUNTARY,
        )

        run = run_ingestion(
            source,
            existing={"gov-001": existing_opp_1, "gov-002": existing_opp_2},
        )

        assert run.records_updated == 1  # gov-001
        assert run.records_deactivated == 1  # gov-002 withdrawn
        assert run.records_created == 0

    @patch("boardmatch.ingestion.gov_source.requests.get")
    def test_source_timeout_creates_failed_run(self, mock_get, source):
        """Timeout produces a FAILED IngestionRun, no data lost."""
        mock_get.side_effect = requests.exceptions.Timeout("timed out")

        run = run_ingestion(source)

        assert run.status == IngestionStatus.FAILED
        assert "Timed out" in run.error_message
        assert run.records_fetched == 0

    @patch("boardmatch.ingestion.gov_source.requests.get")
    def test_rate_limit_creates_failed_run(self, mock_get, source):
        """Rate limit produces a FAILED IngestionRun."""
        mock_get.return_value = _mock_response(status_code=429)

        run = run_ingestion(source)

        assert run.status == IngestionStatus.FAILED
        assert "429" in run.error_message

    @patch("boardmatch.ingestion.gov_source.requests.get")
    def test_malformed_response_partial_run(self, mock_get, source):
        """Partially malformed data still yields a successful run."""
        malformed = {"no": "good_fields"}
        mock_get.return_value = _mock_response(
            json_data=[SAMPLE_VACANCY, malformed]
        )

        run = run_ingestion(source)

        assert run.status == IngestionStatus.SUCCEEDED
        assert run.records_fetched == 1  # only valid record
        assert run.records_created == 1

    @patch("boardmatch.ingestion.gov_source.requests.get")
    def test_idempotent_ingestion(self, mock_get, source):
        """Running ingestion twice with same data produces same counts."""
        mock_get.return_value = _mock_response(
            json_data=[SAMPLE_VACANCY, SAMPLE_VACANCY_2]
        )

        run1 = run_ingestion(source)

        # Simulate existing state from first run
        existing = {opp.id: opp for opp in run1.opportunities}
        run2 = run_ingestion(source, existing=existing)

        assert run2.records_created == 0
        assert run2.records_updated == 2
        assert run2.records_deactivated == 0

    @patch("boardmatch.ingestion.gov_source.requests.get")
    def test_source_failure_preserves_existing_data(self, mock_get, source):
        """Source failure does NOT delete existing records."""
        mock_get.side_effect = requests.exceptions.ConnectionError("down")
        existing_opp = Opportunity(
            id="gov-001",
            title="Board Member",
            organisation="Dept Health",
            sector="Health",
            location="Canberra",
            source="gov_board_vacancies",
            url="https://example.com/gov-001",
            remuneration=Remuneration.PAID,
        )

        run = run_ingestion(
            source, existing={"gov-001": existing_opp}
        )

        # Run failed — existing data NOT touched
        assert run.status == IngestionStatus.FAILED
        assert run.records_fetched == 0
        assert run.records_deactivated == 0
