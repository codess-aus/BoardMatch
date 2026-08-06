"""Tests for provenance and trust indicators (BM-036)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from boardmatch.models import Opportunity, Remuneration
from boardmatch.provenance import (
    STALE_THRESHOLD_DAYS,
    OpportunityStatus,
    RemunerationConfidence,
    build_provenance,
    compute_remuneration_confidence,
    compute_status,
    is_stale,
)


def _make_opportunity(
    *,
    source: str = "BoardVacancies",
    url: str = "https://example.com/opp/1",
    remuneration: Remuneration = Remuneration.PAID,
    fee_aud: int | None = 45000,
    closes_on: str | None = "2025-12-31",
) -> Opportunity:
    return Opportunity(
        id="opp-1",
        title="Non-Executive Director",
        organisation="Acme Corp",
        sector="Technology",
        location="Sydney",
        source=source,
        url=url,
        remuneration=remuneration,
        fee_aud=fee_aud,
        closes_on=closes_on,
        summary="A board opportunity at Acme Corp.",
        required_skills=("governance", "finance"),
        desirable_skills=("technology",),
    )


class TestProvenanceRendering:
    """Test that provenance info is correctly built and rendered."""

    def test_full_provenance_with_all_fields(self):
        opp = _make_opportunity()
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        first_seen = datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
        last_verified = datetime(2025, 6, 14, 10, 0, 0, tzinfo=timezone.utc)

        prov = build_provenance(
            opp,
            first_seen=first_seen,
            last_verified=last_verified,
            now=now,
        )

        assert prov.source_name == "BoardVacancies"
        assert prov.source_url == "https://example.com/opp/1"
        assert prov.first_seen == first_seen
        assert prov.last_verified == last_verified
        assert prov.closing_date == date(2025, 12, 31)
        assert prov.status == OpportunityStatus.ACTIVE
        assert prov.remuneration_confidence == RemunerationConfidence.HIGH
        assert prov.is_stale is False

    def test_provenance_includes_status_active(self):
        opp = _make_opportunity()
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        last_verified = now - timedelta(days=1)

        prov = build_provenance(opp, last_verified=last_verified, now=now)
        assert prov.status == OpportunityStatus.ACTIVE

    def test_provenance_includes_remuneration_confidence(self):
        opp = _make_opportunity(remuneration=Remuneration.PAID, fee_aud=50000)
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        last_verified = now - timedelta(days=1)

        prov = build_provenance(opp, last_verified=last_verified, now=now)
        assert prov.remuneration_confidence == RemunerationConfidence.HIGH


class TestMissingSourceUrl:
    """Test handling when source URL is missing."""

    def test_missing_url_results_in_none_source_url(self):
        opp = _make_opportunity(url="")
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        last_verified = now - timedelta(days=1)

        prov = build_provenance(opp, last_verified=last_verified, now=now)
        assert prov.source_url is None

    def test_empty_url_does_not_crash(self):
        opp = _make_opportunity(url="")
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        prov = build_provenance(opp, now=now)
        assert prov.source_name == "BoardVacancies"
        assert prov.source_url is None


class TestStaleRecordWarning:
    """Test stale record detection and warnings."""

    def test_record_not_verified_is_stale(self):
        assert is_stale(None) is True

    def test_record_verified_recently_is_not_stale(self):
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        last_verified = now - timedelta(days=5)
        assert is_stale(last_verified, now=now) is False

    def test_record_verified_beyond_threshold_is_stale(self):
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        last_verified = now - timedelta(days=STALE_THRESHOLD_DAYS + 1)
        assert is_stale(last_verified, now=now) is True

    def test_stale_provenance_has_warning_message(self):
        opp = _make_opportunity()
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        last_verified = now - timedelta(days=STALE_THRESHOLD_DAYS + 5)

        prov = build_provenance(opp, last_verified=last_verified, now=now)
        assert prov.is_stale is True
        assert prov.stale_warning is not None
        assert "not been verified recently" in prov.stale_warning

    def test_fresh_provenance_has_no_warning(self):
        opp = _make_opportunity()
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        last_verified = now - timedelta(days=1)

        prov = build_provenance(opp, last_verified=last_verified, now=now)
        assert prov.is_stale is False
        assert prov.stale_warning is None

    def test_stale_threshold_boundary(self):
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        exactly_threshold = now - timedelta(days=STALE_THRESHOLD_DAYS)
        assert is_stale(exactly_threshold, now=now) is False

        just_beyond = now - timedelta(days=STALE_THRESHOLD_DAYS + 1)
        assert is_stale(just_beyond, now=now) is True


class TestUnknownRemuneration:
    """Test handling of unknown remuneration."""

    def test_unknown_remuneration_gives_unknown_confidence(self):
        opp = _make_opportunity(remuneration=Remuneration.UNKNOWN, fee_aud=None)
        confidence = compute_remuneration_confidence(opp)
        assert confidence == RemunerationConfidence.UNKNOWN

    def test_paid_without_fee_gives_medium_confidence(self):
        opp = _make_opportunity(remuneration=Remuneration.PAID, fee_aud=None)
        confidence = compute_remuneration_confidence(opp)
        assert confidence == RemunerationConfidence.MEDIUM

    def test_paid_with_fee_gives_high_confidence(self):
        opp = _make_opportunity(remuneration=Remuneration.PAID, fee_aud=45000)
        confidence = compute_remuneration_confidence(opp)
        assert confidence == RemunerationConfidence.HIGH

    def test_voluntary_gives_high_confidence(self):
        opp = _make_opportunity(remuneration=Remuneration.VOLUNTARY, fee_aud=None)
        confidence = compute_remuneration_confidence(opp)
        assert confidence == RemunerationConfidence.HIGH

    def test_unknown_remuneration_in_provenance(self):
        opp = _make_opportunity(remuneration=Remuneration.UNKNOWN, fee_aud=None)
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        last_verified = now - timedelta(days=1)

        prov = build_provenance(opp, last_verified=last_verified, now=now)
        assert prov.remuneration_confidence == RemunerationConfidence.UNKNOWN


class TestWithdrawnOpportunity:
    """Test withdrawn status handling."""

    def test_withdrawn_status_computed(self):
        opp = _make_opportunity()
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        last_verified = now - timedelta(days=1)

        status = compute_status(opp, last_verified=last_verified, withdrawn=True)
        assert status == OpportunityStatus.WITHDRAWN

    def test_withdrawn_overrides_other_statuses(self):
        # Even if expired, withdrawn takes priority
        opp = _make_opportunity(closes_on="2020-01-01")
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        last_verified = now - timedelta(days=1)

        status = compute_status(
            opp, last_verified=last_verified, withdrawn=True, now=now.date()
        )
        assert status == OpportunityStatus.WITHDRAWN

    def test_withdrawn_in_provenance(self):
        opp = _make_opportunity()
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        last_verified = now - timedelta(days=1)

        prov = build_provenance(
            opp, last_verified=last_verified, withdrawn=True, now=now
        )
        assert prov.status == OpportunityStatus.WITHDRAWN


class TestDuplicateSourceIndicator:
    """Test duplicate source detection and display."""

    def test_single_source_no_duplicates(self):
        opp = _make_opportunity(source="BoardVacancies")
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        last_verified = now - timedelta(days=1)

        prov = build_provenance(opp, last_verified=last_verified, now=now)
        assert prov.duplicate_sources is None

    def test_explicit_duplicate_sources_list(self):
        opp = _make_opportunity(source="BoardVacancies")
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        last_verified = now - timedelta(days=1)

        prov = build_provenance(
            opp,
            last_verified=last_verified,
            duplicate_sources=["BoardVacancies", "GovSource", "ASXListings"],
            now=now,
        )
        assert prov.duplicate_sources is not None
        assert len(prov.duplicate_sources) == 3
        assert "GovSource" in prov.duplicate_sources

    def test_merged_source_string_detected(self):
        # When sources are merged, they appear semicolon-separated
        opp = _make_opportunity(source="BoardVacancies; GovSource")
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        last_verified = now - timedelta(days=1)

        prov = build_provenance(opp, last_verified=last_verified, now=now)
        # The duplicate_sources are not automatically parsed from source string
        # They must be explicitly provided
        assert prov.duplicate_sources is None


class TestStatusComputation:
    """Test various status computations."""

    def test_active_when_verified_and_not_expired(self):
        opp = _make_opportunity(closes_on="2025-12-31")
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        last_verified = now - timedelta(days=1)

        status = compute_status(opp, last_verified=last_verified, now=now.date())
        assert status == OpportunityStatus.ACTIVE

    def test_expired_when_past_closing_date(self):
        opp = _make_opportunity(closes_on="2025-01-01")
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        last_verified = now - timedelta(days=1)

        status = compute_status(opp, last_verified=last_verified, now=now.date())
        assert status == OpportunityStatus.EXPIRED

    def test_unverified_when_never_verified(self):
        opp = _make_opportunity()
        status = compute_status(opp, last_verified=None)
        assert status == OpportunityStatus.UNVERIFIED

    def test_active_with_no_closing_date(self):
        opp = _make_opportunity(closes_on=None)
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        last_verified = now - timedelta(days=1)

        status = compute_status(opp, last_verified=last_verified, now=now.date())
        assert status == OpportunityStatus.ACTIVE


class TestApiProvenanceIntegration:
    """Test provenance through the API layer."""

    def test_opportunity_response_includes_provenance(self):
        """Verify the API schema includes the provenance field."""
        from boardmatch.api.v1.schemas import OpportunityResponse

        # Verify model schema has provenance
        fields = OpportunityResponse.model_fields
        assert "provenance" in fields

    def test_provenance_response_schema_fields(self):
        """Verify all required fields exist in ProvenanceResponse."""
        from boardmatch.api.v1.schemas import ProvenanceResponse

        fields = ProvenanceResponse.model_fields
        assert "source_name" in fields
        assert "source_url" in fields
        assert "first_seen" in fields
        assert "last_verified" in fields
        assert "closing_date" in fields
        assert "status" in fields
        assert "remuneration_confidence" in fields
        assert "is_stale" in fields
        assert "stale_warning" in fields
        assert "duplicate_sources" in fields

    def test_provenance_response_serialization(self):
        """Verify ProvenanceResponse serializes correctly."""
        from boardmatch.api.v1.schemas import ProvenanceResponse

        prov = ProvenanceResponse(
            source_name="TestSource",
            source_url="https://example.com",
            first_seen=datetime(2025, 1, 1, tzinfo=timezone.utc),
            last_verified=datetime(2025, 6, 1, tzinfo=timezone.utc),
            closing_date=date(2025, 12, 31),
            status="active",
            remuneration_confidence="high",
            is_stale=False,
            stale_warning=None,
            duplicate_sources=None,
        )
        data = prov.model_dump()
        assert data["source_name"] == "TestSource"
        assert data["status"] == "active"
        assert data["remuneration_confidence"] == "high"
        assert data["is_stale"] is False
