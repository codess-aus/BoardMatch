"""Tests for the development data importer."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from boardmatch.config import AppEnvironment, Settings
from boardmatch.infrastructure.importer import (
    SYNTHETIC_TAG,
    SYNTHETIC_USER_ID,
    ProductionImportError,
    import_demo_data,
)
from boardmatch.infrastructure.repositories.memory import (
    InMemoryCandidateRepository,
    InMemoryOpportunityRepository,
)


@pytest.fixture
def opportunity_repo() -> InMemoryOpportunityRepository:
    return InMemoryOpportunityRepository()


@pytest.fixture
def candidate_repo() -> InMemoryCandidateRepository:
    return InMemoryCandidateRepository()


@pytest.fixture
def local_settings() -> Settings:
    return Settings(app_env=AppEnvironment.LOCAL)


@pytest.fixture
def test_settings() -> Settings:
    return Settings(app_env=AppEnvironment.TEST)


class TestImportIntoEmptyRepository:
    """Verify that importing into fresh repositories works correctly."""

    def test_imports_all_opportunities(
        self,
        opportunity_repo: InMemoryOpportunityRepository,
        candidate_repo: InMemoryCandidateRepository,
        local_settings: Settings,
    ) -> None:
        result = import_demo_data(opportunity_repo, candidate_repo, local_settings)

        # gov_vacancies.json has 5, mock_sources.json has 6 = 11 total
        assert result.opportunities_imported == 11
        assert result.opportunities_skipped == 0

    def test_imports_candidate(
        self,
        opportunity_repo: InMemoryOpportunityRepository,
        candidate_repo: InMemoryCandidateRepository,
        local_settings: Settings,
    ) -> None:
        result = import_demo_data(opportunity_repo, candidate_repo, local_settings)

        assert result.candidates_imported == 1
        assert result.candidates_skipped == 0

        candidate = candidate_repo.get_for_user(SYNTHETIC_USER_ID)
        assert candidate is not None
        assert "Priya Raman" in candidate.name

    def test_opportunities_are_retrievable(
        self,
        opportunity_repo: InMemoryOpportunityRepository,
        candidate_repo: InMemoryCandidateRepository,
        local_settings: Settings,
    ) -> None:
        import_demo_data(opportunity_repo, candidate_repo, local_settings)

        opp = opportunity_repo.get_by_id("gov-001")
        assert opp is not None
        assert opp.organisation == "Australian Digital Health Agency"

    def test_opportunities_retain_source_metadata(
        self,
        opportunity_repo: InMemoryOpportunityRepository,
        candidate_repo: InMemoryCandidateRepository,
        local_settings: Settings,
    ) -> None:
        import_demo_data(opportunity_repo, candidate_repo, local_settings)

        gov_opp = opportunity_repo.get_by_id("gov-001")
        assert gov_opp is not None
        assert gov_opp.source == "Government board vacancies register"

        mock_opp = opportunity_repo.get_by_id("asx-101")
        assert mock_opp is not None
        assert "ASX" in mock_opp.source


class TestIdempotency:
    """Verify that repeated imports don't create duplicates."""

    def test_second_import_skips_existing_opportunities(
        self,
        opportunity_repo: InMemoryOpportunityRepository,
        candidate_repo: InMemoryCandidateRepository,
        local_settings: Settings,
    ) -> None:
        first = import_demo_data(opportunity_repo, candidate_repo, local_settings)
        second = import_demo_data(opportunity_repo, candidate_repo, local_settings)

        assert first.opportunities_imported == 11
        assert second.opportunities_imported == 0
        assert second.opportunities_skipped == 11

    def test_second_import_skips_existing_candidate(
        self,
        opportunity_repo: InMemoryOpportunityRepository,
        candidate_repo: InMemoryCandidateRepository,
        local_settings: Settings,
    ) -> None:
        first = import_demo_data(opportunity_repo, candidate_repo, local_settings)
        second = import_demo_data(opportunity_repo, candidate_repo, local_settings)

        assert first.candidates_imported == 1
        assert second.candidates_imported == 0
        assert second.candidates_skipped == 1

    def test_total_records_unchanged_after_repeat(
        self,
        opportunity_repo: InMemoryOpportunityRepository,
        candidate_repo: InMemoryCandidateRepository,
        local_settings: Settings,
    ) -> None:
        import_demo_data(opportunity_repo, candidate_repo, local_settings)
        import_demo_data(opportunity_repo, candidate_repo, local_settings)

        all_opps = opportunity_repo.search()
        assert len(all_opps) == 11


class TestSyntheticTagging:
    """Verify that imported records are visibly labelled as synthetic."""

    def test_opportunity_titles_tagged(
        self,
        opportunity_repo: InMemoryOpportunityRepository,
        candidate_repo: InMemoryCandidateRepository,
        local_settings: Settings,
    ) -> None:
        import_demo_data(opportunity_repo, candidate_repo, local_settings)

        for opp in opportunity_repo.search():
            assert opp.title.startswith(SYNTHETIC_TAG), (
                f"Opportunity '{opp.id}' title not tagged synthetic"
            )

    def test_candidate_name_tagged(
        self,
        opportunity_repo: InMemoryOpportunityRepository,
        candidate_repo: InMemoryCandidateRepository,
        local_settings: Settings,
    ) -> None:
        import_demo_data(opportunity_repo, candidate_repo, local_settings)

        candidate = candidate_repo.get_for_user(SYNTHETIC_USER_ID)
        assert candidate is not None
        assert candidate.name.startswith(SYNTHETIC_TAG)

    def test_works_in_test_environment(
        self,
        opportunity_repo: InMemoryOpportunityRepository,
        candidate_repo: InMemoryCandidateRepository,
        test_settings: Settings,
    ) -> None:
        result = import_demo_data(opportunity_repo, candidate_repo, test_settings)
        assert result.opportunities_imported == 11


class TestProductionGuard:
    """Verify that import refuses to run in production."""

    def test_raises_in_production(
        self,
        opportunity_repo: InMemoryOpportunityRepository,
        candidate_repo: InMemoryCandidateRepository,
    ) -> None:
        prod_settings = Settings.model_construct(app_env=AppEnvironment.PRODUCTION)

        with pytest.raises(ProductionImportError, match="production"):
            import_demo_data(opportunity_repo, candidate_repo, prod_settings)

    def test_no_data_imported_in_production(
        self,
        opportunity_repo: InMemoryOpportunityRepository,
        candidate_repo: InMemoryCandidateRepository,
    ) -> None:
        prod_settings = Settings.model_construct(app_env=AppEnvironment.PRODUCTION)

        with pytest.raises(ProductionImportError):
            import_demo_data(opportunity_repo, candidate_repo, prod_settings)

        assert opportunity_repo.search() == []
        assert candidate_repo.get_for_user(SYNTHETIC_USER_ID) is None

    def test_environment_variable_guard(
        self,
        opportunity_repo: InMemoryOpportunityRepository,
        candidate_repo: InMemoryCandidateRepository,
    ) -> None:
        """Confirm the guard works when settings come from environment."""
        prod_settings = Settings.model_construct(app_env=AppEnvironment.PRODUCTION)

        with pytest.raises(ProductionImportError):
            import_demo_data(opportunity_repo, candidate_repo, prod_settings)


class TestInvalidFixtureHandling:
    """Verify graceful handling of malformed fixture data."""

    def test_missing_fixture_file(
        self,
        opportunity_repo: InMemoryOpportunityRepository,
        candidate_repo: InMemoryCandidateRepository,
        local_settings: Settings,
    ) -> None:
        with patch(
            "boardmatch.infrastructure.importer._DATA_DIR",
            Path("/nonexistent/path"),
        ):
            with pytest.raises(FileNotFoundError, match="Fixture file not found"):
                import_demo_data(opportunity_repo, candidate_repo, local_settings)

    def test_opportunity_missing_id_skipped(
        self,
        opportunity_repo: InMemoryOpportunityRepository,
        candidate_repo: InMemoryCandidateRepository,
        local_settings: Settings,
    ) -> None:
        bad_data = [{"title": "No ID field", "organisation": "Test"}]

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "mock_sources.json").write_text("[]")
            (tmp_path / "sample_candidate.json").write_text(
                '{"name": "Test User"}'
            )
            (tmp_path / "gov_vacancies.json").write_text(json.dumps(bad_data))

            with patch("boardmatch.infrastructure.importer._DATA_DIR", tmp_path):
                result = import_demo_data(
                    opportunity_repo, candidate_repo, local_settings
                )

        assert result.opportunities_imported == 0
        assert result.opportunities_skipped == 0

    def test_non_array_opportunity_file_skipped(
        self,
        opportunity_repo: InMemoryOpportunityRepository,
        candidate_repo: InMemoryCandidateRepository,
        local_settings: Settings,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "gov_vacancies.json").write_text('{"not": "an array"}')
            (tmp_path / "mock_sources.json").write_text("[]")
            (tmp_path / "sample_candidate.json").write_text(
                '{"name": "Test User"}'
            )

            with patch("boardmatch.infrastructure.importer._DATA_DIR", tmp_path):
                result = import_demo_data(
                    opportunity_repo, candidate_repo, local_settings
                )

        assert result.opportunities_imported == 0

    def test_invalid_candidate_file_skipped(
        self,
        opportunity_repo: InMemoryOpportunityRepository,
        candidate_repo: InMemoryCandidateRepository,
        local_settings: Settings,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "gov_vacancies.json").write_text("[]")
            (tmp_path / "mock_sources.json").write_text("[]")
            (tmp_path / "sample_candidate.json").write_text('"just a string"')

            with patch("boardmatch.infrastructure.importer._DATA_DIR", tmp_path):
                result = import_demo_data(
                    opportunity_repo, candidate_repo, local_settings
                )

        assert result.candidates_imported == 0
