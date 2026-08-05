"""Tests for deduplication and canonicalisation (BM-020)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from boardmatch.ingestion.deduplication import (
    DuplicateGroup,
    DuplicateStatus,
    find_duplicates,
    merge_duplicates,
    normalise_org_name,
    normalise_title,
)
from boardmatch.models import Opportunity, Remuneration


def _make_opportunity(
    id: str = "opp-1",
    title: str = "Board Member",
    organisation: str = "Acme Ltd",
    source: str = "gov_vacancies",
    **kwargs,
) -> Opportunity:
    """Helper to create test opportunities with sensible defaults."""
    defaults = dict(
        sector="Technology",
        location="Sydney",
        url="https://example.com/opp",
        remuneration=Remuneration.PAID,
    )
    defaults.update(kwargs)
    return Opportunity(id=id, title=title, organisation=organisation, source=source, **defaults)


class TestNormaliseOrgName:
    def test_strips_whitespace(self):
        assert normalise_org_name("  Acme Corp  ") == "acme corporation"

    def test_lowercases(self):
        assert normalise_org_name("ACME CORPORATION") == "acme corporation"

    def test_ltd_to_limited(self):
        assert normalise_org_name("Acme Ltd") == "acme limited"
        assert normalise_org_name("Acme Ltd.") == "acme limited"

    def test_corp_to_corporation(self):
        assert normalise_org_name("Acme Corp") == "acme corporation"
        assert normalise_org_name("Acme Corp.") == "acme corporation"

    def test_inc_to_incorporated(self):
        assert normalise_org_name("Acme Inc") == "acme incorporated"
        assert normalise_org_name("Acme Inc.") == "acme incorporated"

    def test_pty_to_proprietary(self):
        assert normalise_org_name("Acme Pty") == "acme proprietary"

    def test_removes_punctuation(self):
        assert normalise_org_name("Acme (Australia) Limited") == "acme australia limited"

    def test_collapses_whitespace(self):
        assert normalise_org_name("Acme   Limited") == "acme limited"

    def test_multiple_abbreviations(self):
        result = normalise_org_name("Acme Pty Ltd")
        assert result == "acme proprietary limited"

    def test_empty_string(self):
        assert normalise_org_name("") == ""

    def test_preserves_identity_for_different_orgs(self):
        assert normalise_org_name("Alpha Corp") != normalise_org_name("Beta Corp")


class TestNormaliseTitle:
    def test_strips_whitespace(self):
        assert normalise_title("  Board Member  ") == "board member"

    def test_lowercases(self):
        assert normalise_title("BOARD MEMBER") == "board member"

    def test_collapses_whitespace(self):
        assert normalise_title("Board   Member") == "board member"

    def test_empty_string(self):
        assert normalise_title("") == ""

    def test_mixed_whitespace(self):
        assert normalise_title("Board\t\nMember") == "board member"


class TestFindDuplicates:
    def test_finds_exact_duplicates(self):
        opps = [
            _make_opportunity(id="1", title="Board Member", organisation="Acme Ltd"),
            _make_opportunity(id="2", title="Board Member", organisation="Acme Ltd"),
        ]
        groups = find_duplicates(opps)
        assert len(groups) == 1
        assert len(groups[0].source_records) == 2

    def test_finds_case_insensitive_duplicates(self):
        opps = [
            _make_opportunity(id="1", title="Board Member", organisation="ACME LTD"),
            _make_opportunity(id="2", title="board member", organisation="acme ltd"),
        ]
        groups = find_duplicates(opps)
        assert len(groups) == 1

    def test_finds_abbreviation_duplicates(self):
        opps = [
            _make_opportunity(id="1", title="Director", organisation="Acme Ltd"),
            _make_opportunity(id="2", title="Director", organisation="Acme Limited"),
        ]
        groups = find_duplicates(opps)
        assert len(groups) == 1

    def test_no_duplicates_returns_empty(self):
        opps = [
            _make_opportunity(id="1", title="Board Member", organisation="Acme Ltd"),
            _make_opportunity(id="2", title="Director", organisation="Beta Corp"),
        ]
        groups = find_duplicates(opps)
        assert len(groups) == 0

    def test_multiple_duplicate_groups(self):
        opps = [
            _make_opportunity(id="1", title="Board Member", organisation="Acme Ltd"),
            _make_opportunity(id="2", title="Board Member", organisation="Acme Limited"),
            _make_opportunity(id="3", title="Director", organisation="Beta Corp"),
            _make_opportunity(id="4", title="Director", organisation="Beta Corporation"),
        ]
        groups = find_duplicates(opps)
        assert len(groups) == 2

    def test_single_opportunity_not_duplicate(self):
        opps = [_make_opportunity(id="1")]
        groups = find_duplicates(opps)
        assert len(groups) == 0

    def test_empty_list(self):
        groups = find_duplicates([])
        assert len(groups) == 0

    def test_canonical_id_is_deterministic(self):
        opps = [
            _make_opportunity(id="1", title="Board Member", organisation="Acme Ltd"),
            _make_opportunity(id="2", title="Board Member", organisation="Acme Ltd"),
        ]
        groups1 = find_duplicates(opps)
        groups2 = find_duplicates(opps)
        assert groups1[0].canonical_id == groups2[0].canonical_id

    def test_cross_source_higher_confidence(self):
        opps = [
            _make_opportunity(id="1", title="Board Member", organisation="Acme Ltd", source="source_a"),
            _make_opportunity(id="2", title="Board Member", organisation="Acme Ltd", source="source_b"),
        ]
        groups = find_duplicates(opps)
        assert groups[0].confidence == 1.0

    def test_same_source_lower_confidence(self):
        opps = [
            _make_opportunity(id="1", title="Board Member", organisation="Acme Ltd", source="source_a"),
            _make_opportunity(id="2", title="Board Member", organisation="Acme Ltd", source="source_a"),
        ]
        groups = find_duplicates(opps)
        assert groups[0].confidence == 0.9

    def test_whitespace_variation_detected(self):
        opps = [
            _make_opportunity(id="1", title="Board Member", organisation="Acme  Ltd"),
            _make_opportunity(id="2", title="Board Member", organisation="Acme Ltd"),
        ]
        groups = find_duplicates(opps)
        assert len(groups) == 1


class TestMergeDuplicates:
    def test_merge_picks_most_complete_record(self):
        opps = [
            _make_opportunity(id="1", title="Board Member", organisation="Acme Ltd", summary=""),
            _make_opportunity(id="2", title="Board Member", organisation="Acme Ltd", summary="A detailed summary of the role."),
        ]
        groups = find_duplicates(opps)
        merged = merge_duplicates(groups[0])
        assert merged.summary == "A detailed summary of the role."

    def test_merge_uses_canonical_id(self):
        opps = [
            _make_opportunity(id="1", title="Board Member", organisation="Acme Ltd"),
            _make_opportunity(id="2", title="Board Member", organisation="Acme Ltd"),
        ]
        groups = find_duplicates(opps)
        merged = merge_duplicates(groups[0])
        assert merged.id == groups[0].canonical_id

    def test_merge_combines_sources(self):
        opps = [
            _make_opportunity(id="1", title="Board Member", organisation="Acme Ltd", source="source_a"),
            _make_opportunity(id="2", title="Board Member", organisation="Acme Ltd", source="source_b"),
        ]
        groups = find_duplicates(opps)
        merged = merge_duplicates(groups[0])
        assert "source_a" in merged.source
        assert "source_b" in merged.source

    def test_merge_unions_skills(self):
        opps = [
            _make_opportunity(id="1", title="Board Member", organisation="Acme Ltd", required_skills=("governance", "finance")),
            _make_opportunity(id="2", title="Board Member", organisation="Acme Ltd", required_skills=("governance", "strategy")),
        ]
        groups = find_duplicates(opps)
        merged = merge_duplicates(groups[0])
        assert set(merged.required_skills) == {"governance", "finance", "strategy"}

    def test_merge_fills_missing_fields(self):
        opps = [
            _make_opportunity(id="1", title="Board Member", organisation="Acme Ltd", closes_on=None),
            _make_opportunity(id="1b", title="Board Member", organisation="Acme Ltd", closes_on="2024-06-30"),
        ]
        groups = find_duplicates(opps)
        merged = merge_duplicates(groups[0])
        assert merged.closes_on == "2024-06-30"

    def test_merge_empty_group_raises(self):
        group = DuplicateGroup(canonical_id="test", source_records=[])
        with pytest.raises(ValueError, match="Cannot merge an empty"):
            merge_duplicates(group)

    def test_merge_single_record(self):
        opp = _make_opportunity(id="1", title="Board Member", organisation="Acme Ltd")
        group = DuplicateGroup(canonical_id="test-id", source_records=[opp])
        merged = merge_duplicates(group)
        assert merged.id == "test-id"
        assert merged.title == opp.title

    def test_merge_preserves_provenance(self):
        opps = [
            _make_opportunity(id="1", source="gov_vacancies"),
            _make_opportunity(id="2", source="nfp_boards"),
        ]
        groups = find_duplicates(opps)
        merged = merge_duplicates(groups[0])
        assert "gov_vacancies" in merged.source
        assert "nfp_boards" in merged.source

    def test_merge_is_deterministic(self):
        opps = [
            _make_opportunity(id="1", title="Board Member", organisation="Acme Ltd", source="a"),
            _make_opportunity(id="2", title="Board Member", organisation="Acme Ltd", source="b", summary="Details"),
        ]
        groups1 = find_duplicates(opps)
        groups2 = find_duplicates(opps)
        merged1 = merge_duplicates(groups1[0])
        merged2 = merge_duplicates(groups2[0])
        assert merged1.id == merged2.id
        assert merged1.source == merged2.source
        assert merged1.summary == merged2.summary


class TestDuplicateGroup:
    def test_default_status_is_pending(self):
        group = DuplicateGroup(canonical_id="x", source_records=[])
        assert group.status == DuplicateStatus.PENDING

    def test_normalised_org_property(self):
        opp = _make_opportunity(organisation="Acme Ltd")
        group = DuplicateGroup(canonical_id="x", source_records=[opp])
        assert group.normalised_org == "acme limited"

    def test_normalised_title_property(self):
        opp = _make_opportunity(title="Board Member")
        group = DuplicateGroup(canonical_id="x", source_records=[opp])
        assert group.normalised_title == "board member"


class TestDeduplicationAPI:
    @pytest.fixture
    def client(self):
        from boardmatch.api import app
        return TestClient(app)

    def test_list_duplicates_endpoint(self, client):
        response = client.get("/api/v1/admin/duplicates")
        assert response.status_code == 200
        data = response.json()
        assert "count" in data
        assert "groups" in data
        assert isinstance(data["groups"], list)

    def test_list_duplicates_with_status_filter(self, client):
        response = client.get("/api/v1/admin/duplicates?status=pending")
        assert response.status_code == 200

    def test_list_duplicates_invalid_status(self, client):
        response = client.get("/api/v1/admin/duplicates?status=invalid")
        assert response.status_code == 400

    def test_merge_nonexistent_group(self, client):
        response = client.post("/api/v1/admin/duplicates/nonexistent/merge")
        assert response.status_code == 404

    def test_dismiss_nonexistent_group(self, client):
        response = client.post("/api/v1/admin/duplicates/nonexistent/dismiss")
        assert response.status_code == 404


class TestProvenanceRetention:
    def test_source_records_unchanged_after_find(self):
        opp1 = _make_opportunity(id="orig-1", title="Board Member", organisation="Acme Ltd")
        opp2 = _make_opportunity(id="orig-2", title="Board Member", organisation="Acme Ltd")
        opps = [opp1, opp2]
        find_duplicates(opps)
        assert opps[0].id == "orig-1"
        assert opps[1].id == "orig-2"

    def test_source_records_available_in_group(self):
        opp1 = _make_opportunity(id="orig-1", title="Board Member", organisation="Acme Ltd")
        opp2 = _make_opportunity(id="orig-2", title="Board Member", organisation="Acme Ltd")
        groups = find_duplicates([opp1, opp2])
        ids = {r.id for r in groups[0].source_records}
        assert ids == {"orig-1", "orig-2"}
