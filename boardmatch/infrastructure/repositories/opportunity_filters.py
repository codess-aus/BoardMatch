"""Shared filtering/sorting logic for OpportunityRepository implementations.

Extracted so both the in-memory and DB-backed repositories apply identical
filter semantics and ordering guarantees (behavioral parity).
"""

from __future__ import annotations

from datetime import date

from boardmatch.models import Opportunity


def apply_filters(
    results: list[Opportunity], filters: dict[str, object]
) -> list[Opportunity]:
    """Apply composable filters to a list of opportunities."""
    if "sector" in filters:
        sector = str(filters["sector"]).lower()
        results = [o for o in results if o.sector.lower() == sector]

    if "location" in filters:
        location = str(filters["location"]).lower()
        results = [o for o in results if o.location.lower() == location]

    if "remuneration" in filters:
        rem = str(filters["remuneration"])
        results = [o for o in results if o.remuneration.value == rem]

    if "paid_only" in filters and filters["paid_only"]:
        results = [o for o in results if o.is_paid]

    if "min_fee" in filters:
        min_fee = int(str(filters["min_fee"]))
        results = [o for o in results if o.fee_aud is not None and o.fee_aud >= min_fee]

    if "source" in filters:
        source = str(filters["source"]).lower()
        results = [o for o in results if o.source.lower() == source]

    if "closes_after" in filters:
        after = str(filters["closes_after"])
        results = [
            o for o in results if o.closes_on is not None and o.closes_on >= after
        ]

    if "closes_before" in filters:
        before = str(filters["closes_before"])
        results = [
            o for o in results if o.closes_on is not None and o.closes_on <= before
        ]

    if "status" in filters and str(filters["status"]).lower() == "open":
        today = date.today().isoformat()
        results = [o for o in results if o.closes_on is None or o.closes_on >= today]

    return results


def sort_deterministic(results: list[Opportunity]) -> list[Opportunity]:
    """Sort results deterministically by fee descending, then title ascending."""
    return sorted(
        results,
        key=lambda o: (-(o.fee_aud if o.fee_aud is not None else -1), o.title),
    )
