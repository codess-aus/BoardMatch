"""Tests for the ingestion source adapter interface and implementations."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from boardmatch.ingestion import (
    IngestionRun,
    IngestionStatus,
    OpportunitySource,
    SourceAuthError,
    SourceError,
    SourceRateLimitError,
    SourceRecord,
    SourceTimeoutError,
)
from boardmatch.ingestion.json_source import JsonFileSource, gov_vacancies_source
from boardmatch.models import Opportunity, Remuneration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_RECORD = {
    "id": "test-001",
    "title": "Test Board Position",
    "organisation": "Test Org",
    "sector": "Technology",
    "location": "Sydney, NSW",
    "source": "test_source",
    "url": "https://example.com/test-001",
    "remuneration": "paid",
    "fee_aud": 50000,
    "closes_on": "2025-12-31",
    "summary": "A test opportunity.",
    "required_skills": ["governance", "leadership"],
    "desirable_skills": ["technology"],
}


def _write_json(path: Path, data: list[dict]) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


class FakeSource:
    """A simple fake implementing the OpportunitySource protocol."""

    source_key: str = "fake"

    def __init__(self, opportunities: list[Opportunity] | None = None) -> None:
        self._opportunities = opportunities or []

    def fetch(self) -> list[Opportunity]:
        return self._opportunities


class FailingSource:
    """A source that always raises a SourceError subclass."""

    source_key: str = "failing"

    def __init__(self, error: SourceError) -> None:
        self._error = error

    def fetch(self) -> list[Opportunity]:
        raise self._error


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_fake_source_satisfies_protocol() -> None:
    """FakeSource structurally satisfies OpportunitySource protocol."""
    source: OpportunitySource = FakeSource()
    assert source.source_key == "fake"


def test_json_file_source_satisfies_protocol() -> None:
    """JsonFileSource structurally satisfies OpportunitySource protocol."""
    with tempfile.TemporaryDirectory() as tmp:
        _write_json(Path(tmp) / "empty.json", [])
        source: OpportunitySource = JsonFileSource("empty.json", data_dir=Path(tmp))
        assert source.source_key == "empty"


# ---------------------------------------------------------------------------
# Successful source fetch returns Opportunity objects
# ---------------------------------------------------------------------------


def test_successful_fetch_returns_opportunities() -> None:
    opp = Opportunity(
        id="t-1",
        title="Chair",
        organisation="Org",
        sector="Health",
        location="Melbourne",
        source="test",
        url="https://example.com",
        remuneration=Remuneration.PAID,
        fee_aud=40000,
    )
    source = FakeSource([opp])
    results = source.fetch()
    assert len(results) == 1
    assert isinstance(results[0], Opportunity)
    assert results[0].id == "t-1"


# ---------------------------------------------------------------------------
# Empty source result
# ---------------------------------------------------------------------------


def test_empty_source_returns_empty_list() -> None:
    source = FakeSource([])
    assert source.fetch() == []


def test_json_source_empty_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _write_json(Path(tmp) / "empty.json", [])
        source = JsonFileSource("empty.json", data_dir=Path(tmp))
        result = source.fetch()
        assert result == []


# ---------------------------------------------------------------------------
# Malformed source record handling
# ---------------------------------------------------------------------------


def test_malformed_records_are_skipped() -> None:
    """Malformed records are silently skipped; valid ones still returned."""
    valid = SAMPLE_RECORD.copy()
    malformed = {"id": "bad", "title": "Missing fields"}

    with tempfile.TemporaryDirectory() as tmp:
        _write_json(Path(tmp) / "mixed.json", [valid, malformed])
        source = JsonFileSource("mixed.json", data_dir=Path(tmp))
        results = source.fetch()
        assert len(results) == 1
        assert results[0].id == "test-001"


def test_malformed_json_raises_source_error() -> None:
    """Completely invalid JSON raises SourceError."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bad.json"
        path.write_text("{not valid json", encoding="utf-8")
        source = JsonFileSource("bad.json", data_dir=Path(tmp))
        with pytest.raises(SourceError, match="Malformed JSON"):
            source.fetch()


def test_missing_file_raises_source_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = JsonFileSource("nonexistent.json", data_dir=Path(tmp))
        with pytest.raises(SourceError, match="not found"):
            source.fetch()


# ---------------------------------------------------------------------------
# Typed errors (timeout, rate-limit, auth)
# ---------------------------------------------------------------------------


def test_source_timeout_error() -> None:
    source = FailingSource(SourceTimeoutError("Connection timed out"))
    with pytest.raises(SourceTimeoutError):
        source.fetch()


def test_source_rate_limit_error() -> None:
    source = FailingSource(SourceRateLimitError("429 Too Many Requests"))
    with pytest.raises(SourceRateLimitError):
        source.fetch()


def test_source_auth_error() -> None:
    source = FailingSource(SourceAuthError("401 Unauthorized"))
    with pytest.raises(SourceAuthError):
        source.fetch()


def test_error_hierarchy() -> None:
    """All typed errors are subclasses of SourceError."""
    assert issubclass(SourceTimeoutError, SourceError)
    assert issubclass(SourceRateLimitError, SourceError)
    assert issubclass(SourceAuthError, SourceError)


def test_catching_base_error_catches_subtypes() -> None:
    source = FailingSource(SourceTimeoutError("timeout"))
    with pytest.raises(SourceError):
        source.fetch()


# ---------------------------------------------------------------------------
# JSON source adapter returns correct data
# ---------------------------------------------------------------------------


def test_json_source_returns_correct_data() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _write_json(Path(tmp) / "data.json", [SAMPLE_RECORD])
        source = JsonFileSource("data.json", data_dir=Path(tmp))
        results = source.fetch()
        assert len(results) == 1
        opp = results[0]
        assert opp.id == "test-001"
        assert opp.title == "Test Board Position"
        assert opp.organisation == "Test Org"
        assert opp.sector == "Technology"
        assert opp.remuneration == Remuneration.PAID
        assert opp.fee_aud == 50000
        assert opp.required_skills == ("governance", "leadership")
        assert opp.desirable_skills == ("technology",)


def test_json_source_gov_vacancies_loads() -> None:
    """The gov_vacancies adapter loads the bundled demo data."""
    source = gov_vacancies_source()
    results = source.fetch()
    assert len(results) > 0
    assert all(isinstance(o, Opportunity) for o in results)


# ---------------------------------------------------------------------------
# Source metadata attached to records
# ---------------------------------------------------------------------------


def test_source_metadata_attached_to_records() -> None:
    """fetch_with_metadata returns SourceRecord with each Opportunity."""
    with tempfile.TemporaryDirectory() as tmp:
        _write_json(Path(tmp) / "meta.json", [SAMPLE_RECORD])
        source = JsonFileSource("meta.json", source_key="test_src", data_dir=Path(tmp))
        results = source.fetch_with_metadata()
        assert len(results) == 1
        opp, record = results[0]
        assert isinstance(opp, Opportunity)
        assert isinstance(record, SourceRecord)
        assert record.source_key == "test_src"
        assert record.external_id == "test-001"
        assert record.raw_data == SAMPLE_RECORD
        assert record.fetched_at is not None


def test_source_key_defaults_to_filename_stem() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _write_json(Path(tmp) / "my_data.json", [])
        source = JsonFileSource("my_data.json", data_dir=Path(tmp))
        assert source.source_key == "my_data"


# ---------------------------------------------------------------------------
# Ingestion run model
# ---------------------------------------------------------------------------


def test_ingestion_run_lifecycle() -> None:
    run = IngestionRun(source_key="test")
    assert run.status == IngestionStatus.PENDING
    assert run.started_at is None

    run.start()
    assert run.status == IngestionStatus.RUNNING
    assert run.started_at is not None

    run.succeed(fetched=10, stored=8)
    assert run.status == IngestionStatus.SUCCEEDED
    assert run.records_fetched == 10
    assert run.records_stored == 8
    assert run.completed_at is not None


def test_ingestion_run_failure() -> None:
    run = IngestionRun(source_key="test")
    run.start()
    run.fail("Connection refused")
    assert run.status == IngestionStatus.FAILED
    assert run.error_message == "Connection refused"
    assert run.completed_at is not None
