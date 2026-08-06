"""Contract tests for repository implementations.

These tests verify that in-memory repositories conform to the protocol
contracts defined in boardmatch.domain.repositories.
"""

from __future__ import annotations

import pytest

from boardmatch.domain.repositories import CandidateRepository, OpportunityRepository
from boardmatch.infrastructure.repositories.memory import (
    InMemoryCandidateRepository,
    InMemoryOpportunityRepository,
)
from boardmatch.models import Candidate, Opportunity, Remuneration

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def candidate_repo() -> InMemoryCandidateRepository:
    return InMemoryCandidateRepository()


@pytest.fixture
def opportunity_repo() -> InMemoryOpportunityRepository:
    return InMemoryOpportunityRepository()


def _make_candidate(name: str = "Alice Smith") -> Candidate:
    return Candidate(
        name=name,
        headline="CFO turned board director",
        years_experience=15,
        skills=["Finance", "Governance"],
        sectors=["Healthcare"],
    )


def _make_opportunity(
    id: str = "opp-1",
    sector: str = "Healthcare",
    location: str = "Sydney",
    remuneration: Remuneration = Remuneration.PAID,
    fee_aud: int | None = 50_000,
) -> Opportunity:
    return Opportunity(
        id=id,
        title="Non-Executive Director",
        organisation="HealthCorp",
        sector=sector,
        location=location,
        source="aicd",
        url="https://example.com/opp",
        remuneration=remuneration,
        fee_aud=fee_aud,
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """Verify in-memory implementations satisfy Protocol structural typing."""

    def test_candidate_repo_satisfies_protocol(self) -> None:
        repo: CandidateRepository = InMemoryCandidateRepository()
        assert hasattr(repo, "get_for_user")
        assert hasattr(repo, "save_for_user")

    def test_opportunity_repo_satisfies_protocol(self) -> None:
        repo: OpportunityRepository = InMemoryOpportunityRepository()
        assert hasattr(repo, "get_by_id")
        assert hasattr(repo, "search")


# ---------------------------------------------------------------------------
# CandidateRepository contract tests
# ---------------------------------------------------------------------------


class TestCandidateRepository:
    """Contract tests for CandidateRepository implementations."""

    def test_get_for_user_returns_none_when_empty(
        self, candidate_repo: InMemoryCandidateRepository
    ) -> None:
        assert candidate_repo.get_for_user("user-1") is None

    def test_save_and_retrieve(
        self, candidate_repo: InMemoryCandidateRepository
    ) -> None:
        candidate = _make_candidate()
        saved = candidate_repo.save_for_user("user-1", candidate)
        assert saved is candidate
        assert candidate_repo.get_for_user("user-1") is candidate

    def test_save_overwrites_existing(
        self, candidate_repo: InMemoryCandidateRepository
    ) -> None:
        first = _make_candidate("First")
        second = _make_candidate("Second")
        candidate_repo.save_for_user("user-1", first)
        candidate_repo.save_for_user("user-1", second)
        assert candidate_repo.get_for_user("user-1") is second

    def test_user_isolation(self, candidate_repo: InMemoryCandidateRepository) -> None:
        """Each user can only see their own profile."""
        alice = _make_candidate("Alice")
        bob = _make_candidate("Bob")
        candidate_repo.save_for_user("user-alice", alice)
        candidate_repo.save_for_user("user-bob", bob)

        assert candidate_repo.get_for_user("user-alice") is alice
        assert candidate_repo.get_for_user("user-bob") is bob
        assert candidate_repo.get_for_user("user-unknown") is None

    def test_different_users_independent(
        self, candidate_repo: InMemoryCandidateRepository
    ) -> None:
        """Saving for one user does not affect another."""
        alice = _make_candidate("Alice")
        candidate_repo.save_for_user("user-alice", alice)

        updated_alice = _make_candidate("Alice Updated")
        candidate_repo.save_for_user("user-alice", updated_alice)

        # Other users are unaffected
        assert candidate_repo.get_for_user("user-bob") is None


# ---------------------------------------------------------------------------
# OpportunityRepository contract tests
# ---------------------------------------------------------------------------


class TestOpportunityRepository:
    """Contract tests for OpportunityRepository implementations."""

    def test_get_by_id_returns_none_when_empty(
        self, opportunity_repo: InMemoryOpportunityRepository
    ) -> None:
        assert opportunity_repo.get_by_id("nonexistent") is None

    def test_get_by_id_returns_opportunity(
        self, opportunity_repo: InMemoryOpportunityRepository
    ) -> None:
        opp = _make_opportunity()
        opportunity_repo.add(opp)
        assert opportunity_repo.get_by_id("opp-1") is opp

    def test_get_by_id_wrong_id_returns_none(
        self, opportunity_repo: InMemoryOpportunityRepository
    ) -> None:
        opp = _make_opportunity(id="opp-1")
        opportunity_repo.add(opp)
        assert opportunity_repo.get_by_id("opp-2") is None

    def test_search_returns_all_when_no_filters(
        self, opportunity_repo: InMemoryOpportunityRepository
    ) -> None:
        opp1 = _make_opportunity(id="opp-1")
        opp2 = _make_opportunity(id="opp-2", sector="Finance")
        opportunity_repo.add(opp1)
        opportunity_repo.add(opp2)
        results = opportunity_repo.search()
        assert len(results) == 2

    def test_search_filter_by_sector(
        self, opportunity_repo: InMemoryOpportunityRepository
    ) -> None:
        opp_health = _make_opportunity(id="opp-1", sector="Healthcare")
        opp_finance = _make_opportunity(id="opp-2", sector="Finance")
        opportunity_repo.add(opp_health)
        opportunity_repo.add(opp_finance)

        results = opportunity_repo.search(sector="Healthcare")
        assert len(results) == 1
        assert results[0].id == "opp-1"

    def test_search_filter_by_sector_case_insensitive(
        self, opportunity_repo: InMemoryOpportunityRepository
    ) -> None:
        opp = _make_opportunity(id="opp-1", sector="Healthcare")
        opportunity_repo.add(opp)

        results = opportunity_repo.search(sector="healthcare")
        assert len(results) == 1

    def test_search_filter_by_location(
        self, opportunity_repo: InMemoryOpportunityRepository
    ) -> None:
        opp_syd = _make_opportunity(id="opp-1", location="Sydney")
        opp_mel = _make_opportunity(id="opp-2", location="Melbourne")
        opportunity_repo.add(opp_syd)
        opportunity_repo.add(opp_mel)

        results = opportunity_repo.search(location="Melbourne")
        assert len(results) == 1
        assert results[0].id == "opp-2"

    def test_search_filter_by_remuneration(
        self, opportunity_repo: InMemoryOpportunityRepository
    ) -> None:
        opp_paid = _make_opportunity(id="opp-1", remuneration=Remuneration.PAID)
        opp_vol = _make_opportunity(id="opp-2", remuneration=Remuneration.VOLUNTARY)
        opportunity_repo.add(opp_paid)
        opportunity_repo.add(opp_vol)

        results = opportunity_repo.search(remuneration="paid")
        assert len(results) == 1
        assert results[0].id == "opp-1"

    def test_search_filter_by_min_fee(
        self, opportunity_repo: InMemoryOpportunityRepository
    ) -> None:
        opp_high = _make_opportunity(id="opp-1", fee_aud=80_000)
        opp_low = _make_opportunity(id="opp-2", fee_aud=20_000)
        opp_none = _make_opportunity(id="opp-3", fee_aud=None)
        opportunity_repo.add(opp_high)
        opportunity_repo.add(opp_low)
        opportunity_repo.add(opp_none)

        results = opportunity_repo.search(min_fee=50_000)
        assert len(results) == 1
        assert results[0].id == "opp-1"

    def test_search_multiple_filters(
        self, opportunity_repo: InMemoryOpportunityRepository
    ) -> None:
        opp1 = _make_opportunity(id="opp-1", sector="Healthcare", location="Sydney")
        opp2 = _make_opportunity(id="opp-2", sector="Healthcare", location="Melbourne")
        opp3 = _make_opportunity(id="opp-3", sector="Finance", location="Sydney")
        opportunity_repo.add(opp1)
        opportunity_repo.add(opp2)
        opportunity_repo.add(opp3)

        results = opportunity_repo.search(sector="Healthcare", location="Sydney")
        assert len(results) == 1
        assert results[0].id == "opp-1"

    def test_search_no_matches(
        self, opportunity_repo: InMemoryOpportunityRepository
    ) -> None:
        opp = _make_opportunity(id="opp-1", sector="Healthcare")
        opportunity_repo.add(opp)

        results = opportunity_repo.search(sector="Energy")
        assert results == []

    def test_constructor_with_initial_opportunities(self) -> None:
        opps = [_make_opportunity(id="a"), _make_opportunity(id="b")]
        repo = InMemoryOpportunityRepository(opportunities=opps)
        assert repo.get_by_id("a") is opps[0]
        assert repo.get_by_id("b") is opps[1]
