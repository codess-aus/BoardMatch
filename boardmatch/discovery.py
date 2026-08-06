"""Opportunity discovery.

The demo uses one well-structured "live" source (a government board vacancy
register) plus mocked aggregations of ASX announcements, AICD listings, the
not-for-profit register and LinkedIn postings. Each source is loaded through the
same adapter so a real HTTP/Azure AI Agent Service connector can be dropped in
without changing downstream code.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from .models import Opportunity, Remuneration

DATA_DIR = Path(__file__).parent / "data"
PRIMARY_SOURCE = DATA_DIR / "gov_vacancies.json"
MOCK_SOURCES = DATA_DIR / "mock_sources.json"


def _to_opportunity(raw: dict) -> Opportunity:
    return Opportunity(
        id=raw["id"],
        title=raw["title"],
        organisation=raw["organisation"],
        sector=raw["sector"],
        location=raw["location"],
        source=raw["source"],
        url=raw["url"],
        remuneration=Remuneration(raw.get("remuneration", "unknown")),
        fee_aud=raw.get("fee_aud"),
        closes_on=raw.get("closes_on"),
        summary=raw.get("summary", ""),
        required_skills=tuple(raw.get("required_skills", ())),
        desirable_skills=tuple(raw.get("desirable_skills", ())),
    )


def load_source(path: Path) -> list[Opportunity]:
    """Load and normalise one source file."""
    with path.open(encoding="utf-8") as handle:
        return [_to_opportunity(raw) for raw in json.load(handle)]


def discover(
    *,
    include_mocked: bool = True,
    paid_only: bool = False,
    sector: str | None = None,
    min_fee_aud: int | None = None,
) -> list[Opportunity]:
    """Aggregate board vacancies across the configured sources."""
    opportunities: list[Opportunity] = load_source(PRIMARY_SOURCE)
    if include_mocked:
        opportunities += load_source(MOCK_SOURCES)
    return sorted(
        _filter(
            opportunities,
            paid_only=paid_only,
            sector=sector,
            min_fee_aud=min_fee_aud,
        ),
        key=lambda o: (-(o.fee_aud or 0), o.organisation),
    )


def _filter(
    opportunities: Iterable[Opportunity],
    *,
    paid_only: bool,
    sector: str | None,
    min_fee_aud: int | None,
) -> Iterable[Opportunity]:
    for opportunity in opportunities:
        if paid_only and not opportunity.is_paid:
            continue
        if sector and opportunity.sector.lower() != sector.lower():
            continue
        if min_fee_aud is not None and (opportunity.fee_aud or 0) < min_fee_aud:
            continue
        yield opportunity


def get_opportunity(opportunity_id: str) -> Opportunity | None:
    """Look up a single opportunity by id."""
    for opportunity in discover():
        if opportunity.id == opportunity_id:
            return opportunity
    return None
