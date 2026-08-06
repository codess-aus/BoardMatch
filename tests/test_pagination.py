"""Tests for pagination and filtering on GET /api/v1/opportunities (BM-012)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from boardmatch.api import app
from boardmatch.api.v1.schemas import PaginatedOpportunityResponse
from boardmatch.infrastructure.repositories.memory import InMemoryOpportunityRepository
from boardmatch.models import Opportunity, Remuneration

client = TestClient(app)
AUTH_HEADER = {"X-Dev-User-Id": "test-user"}


# ---------------------------------------------------------------------------
# Repository-level pagination tests
# ---------------------------------------------------------------------------


def _make_opp(
    id: str,
    title: str = "Director",
    sector: str = "Healthcare",
    location: str = "Sydney",
    fee_aud: int | None = None,
    closes_on: str | None = None,
    source: str = "aicd",
    remuneration: Remuneration = Remuneration.PAID,
) -> Opportunity:
    return Opportunity(
        id=id,
        title=title,
        organisation="Org",
        sector=sector,
        location=location,
        source=source,
        url="https://example.com",
        remuneration=remuneration,
        fee_aud=fee_aud,
        closes_on=closes_on,
    )


class TestRepositoryPagination:
    """Pagination at the repository level."""

    def _seeded_repo(self, n: int = 25) -> InMemoryOpportunityRepository:
        repo = InMemoryOpportunityRepository()
        for i in range(n):
            repo.add(
                _make_opp(
                    id=f"opp-{i:03d}",
                    title=f"Opp {i:03d}",
                    fee_aud=(n - i) * 1000,
                )
            )
        return repo

    def test_default_page_size(self):
        repo = self._seeded_repo(25)
        result = repo.search_paginated()
        assert result.total == 25
        assert len(result.items) == 20

    def test_page_2(self):
        repo = self._seeded_repo(25)
        result = repo.search_paginated(page=2)
        assert result.total == 25
        assert len(result.items) == 5

    def test_custom_page_size(self):
        repo = self._seeded_repo(10)
        result = repo.search_paginated(page=1, page_size=3)
        assert result.total == 10
        assert len(result.items) == 3

    def test_page_beyond_results(self):
        repo = self._seeded_repo(5)
        result = repo.search_paginated(page=10, page_size=5)
        assert result.total == 5
        assert len(result.items) == 0

    def test_deterministic_sort_fee_desc_then_title(self):
        repo = InMemoryOpportunityRepository()
        repo.add(_make_opp(id="a", title="Zebra", fee_aud=50_000))
        repo.add(_make_opp(id="b", title="Alpha", fee_aud=50_000))
        repo.add(_make_opp(id="c", title="Beta", fee_aud=80_000))

        result = repo.search_paginated(page=1, page_size=10)
        titles = [o.title for o in result.items]
        # Highest fee first, then alphabetical by title for ties
        assert titles == ["Beta", "Alpha", "Zebra"]

    def test_filter_by_sector(self):
        repo = InMemoryOpportunityRepository()
        repo.add(_make_opp(id="a", sector="Healthcare"))
        repo.add(_make_opp(id="b", sector="Finance"))
        repo.add(_make_opp(id="c", sector="Healthcare"))

        result = repo.search_paginated(sector="Healthcare")
        assert result.total == 2
        assert all(o.sector == "Healthcare" for o in result.items)

    def test_filter_by_location(self):
        repo = InMemoryOpportunityRepository()
        repo.add(_make_opp(id="a", location="Sydney"))
        repo.add(_make_opp(id="b", location="Melbourne"))

        result = repo.search_paginated(location="sydney")
        assert result.total == 1
        assert result.items[0].location == "Sydney"

    def test_filter_paid_only(self):
        repo = InMemoryOpportunityRepository()
        repo.add(_make_opp(id="a", remuneration=Remuneration.PAID, fee_aud=50_000))
        repo.add(_make_opp(id="b", remuneration=Remuneration.VOLUNTARY))

        result = repo.search_paginated(paid_only=True)
        assert result.total == 1
        assert result.items[0].id == "a"

    def test_filter_min_fee(self):
        repo = InMemoryOpportunityRepository()
        repo.add(_make_opp(id="a", fee_aud=80_000))
        repo.add(_make_opp(id="b", fee_aud=20_000))
        repo.add(_make_opp(id="c", fee_aud=None))

        result = repo.search_paginated(min_fee=50_000)
        assert result.total == 1
        assert result.items[0].id == "a"

    def test_filter_source(self):
        repo = InMemoryOpportunityRepository()
        repo.add(_make_opp(id="a", source="aicd"))
        repo.add(_make_opp(id="b", source="seek"))

        result = repo.search_paginated(source="aicd")
        assert result.total == 1
        assert result.items[0].id == "a"

    def test_filter_closes_after(self):
        repo = InMemoryOpportunityRepository()
        repo.add(_make_opp(id="a", closes_on="2025-06-01"))
        repo.add(_make_opp(id="b", closes_on="2025-01-01"))
        repo.add(_make_opp(id="c", closes_on=None))

        result = repo.search_paginated(closes_after="2025-03-01")
        assert result.total == 1
        assert result.items[0].id == "a"

    def test_filter_closes_before(self):
        repo = InMemoryOpportunityRepository()
        repo.add(_make_opp(id="a", closes_on="2025-06-01"))
        repo.add(_make_opp(id="b", closes_on="2025-01-01"))

        result = repo.search_paginated(closes_before="2025-03-01")
        assert result.total == 1
        assert result.items[0].id == "b"

    def test_status_open_excludes_expired(self):
        repo = InMemoryOpportunityRepository()
        repo.add(_make_opp(id="a", closes_on="2099-12-31"))
        repo.add(_make_opp(id="b", closes_on="2020-01-01"))
        repo.add(_make_opp(id="c", closes_on=None))

        result = repo.search_paginated(status="open")
        ids = {o.id for o in result.items}
        assert "a" in ids
        assert "c" in ids
        assert "b" not in ids

    def test_no_status_filter_includes_expired(self):
        repo = InMemoryOpportunityRepository()
        repo.add(_make_opp(id="a", closes_on="2099-12-31"))
        repo.add(_make_opp(id="b", closes_on="2020-01-01"))

        result = repo.search_paginated()
        assert result.total == 2

    def test_combined_filters_with_pagination(self):
        repo = InMemoryOpportunityRepository()
        for i in range(10):
            repo.add(
                _make_opp(
                    id=f"h-{i}",
                    title=f"Health {i}",
                    sector="Healthcare",
                    fee_aud=(i + 1) * 10_000,
                )
            )
        for i in range(5):
            repo.add(
                _make_opp(
                    id=f"f-{i}",
                    title=f"Finance {i}",
                    sector="Finance",
                    fee_aud=(i + 1) * 10_000,
                )
            )

        result = repo.search_paginated(page=1, page_size=3, sector="Healthcare")
        assert result.total == 10
        assert len(result.items) == 3
        # All items from Healthcare
        assert all(o.sector == "Healthcare" for o in result.items)


# ---------------------------------------------------------------------------
# API-level pagination tests
# ---------------------------------------------------------------------------


class TestAPIPagination:
    """Tests for the paginated v1 opportunities endpoint."""

    def test_paginated_response_structure(self):
        resp = client.get("/api/v1/opportunities", headers=AUTH_HEADER)
        assert resp.status_code == 200
        body = resp.json()
        parsed = PaginatedOpportunityResponse(**body)
        assert parsed.page == 1
        assert parsed.page_size == 20
        assert parsed.total >= 0
        assert parsed.total_pages >= 0
        assert isinstance(parsed.items, list)
        assert len(parsed.items) <= 20

    def test_custom_page_size(self):
        resp = client.get(
            "/api/v1/opportunities",
            headers=AUTH_HEADER,
            params={"page_size": 5},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["page_size"] == 5
        assert len(body["items"]) <= 5

    def test_page_2(self):
        resp = client.get(
            "/api/v1/opportunities",
            headers=AUTH_HEADER,
            params={"page": 2, "page_size": 2},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["page"] == 2

    def test_page_size_exceeds_max_returns_422(self):
        resp = client.get(
            "/api/v1/opportunities",
            headers=AUTH_HEADER,
            params={"page_size": 101},
        )
        assert resp.status_code == 422

    def test_page_size_zero_returns_422(self):
        resp = client.get(
            "/api/v1/opportunities",
            headers=AUTH_HEADER,
            params={"page_size": 0},
        )
        assert resp.status_code == 422

    def test_page_zero_returns_422(self):
        resp = client.get(
            "/api/v1/opportunities",
            headers=AUTH_HEADER,
            params={"page": 0},
        )
        assert resp.status_code == 422

    def test_filter_paid_only(self):
        resp = client.get(
            "/api/v1/opportunities",
            headers=AUTH_HEADER,
            params={"paid_only": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        for item in body["items"]:
            assert item["remuneration"] == "paid"

    def test_filter_sector(self):
        resp = client.get(
            "/api/v1/opportunities",
            headers=AUTH_HEADER,
            params={"sector": "Healthcare"},
        )
        assert resp.status_code == 200
        body = resp.json()
        for item in body["items"]:
            assert item["sector"].lower() == "healthcare"

    def test_status_open_is_default(self):
        """Default status=open excludes expired opportunities."""
        resp = client.get("/api/v1/opportunities", headers=AUTH_HEADER)
        assert resp.status_code == 200
        body = resp.json()
        # All returned items should not be expired (closes_on >= today or null)
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).date().isoformat()
        for item in body["items"]:
            if item.get("closes_on"):
                assert item["closes_on"] >= today

    def test_status_none_includes_expired(self):
        """Setting status to empty string returns all including expired."""
        resp = client.get(
            "/api/v1/opportunities",
            headers=AUTH_HEADER,
            params={"status": ""},
        )
        assert resp.status_code == 200

    def test_total_pages_calculation(self):
        resp = client.get(
            "/api/v1/opportunities",
            headers=AUTH_HEADER,
            params={"page_size": 2},
        )
        assert resp.status_code == 200
        body = resp.json()
        import math

        expected_pages = math.ceil(body["total"] / 2) if body["total"] > 0 else 0
        assert body["total_pages"] == expected_pages
