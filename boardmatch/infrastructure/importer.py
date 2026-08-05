"""Development-only data importer for BoardMatch demo fixtures.

Reads gov_vacancies.json, mock_sources.json, and sample_candidate.json
and populates in-memory repositories. Idempotent and guarded against
production execution.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from boardmatch.config import AppEnvironment, Settings
from boardmatch.infrastructure.repositories.memory import (
    InMemoryCandidateRepository,
    InMemoryOpportunityRepository,
)
from boardmatch.models import Candidate, Connection, Opportunity, Remuneration

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Sentinel user ID for synthetic demo candidate
SYNTHETIC_USER_ID = "demo-user"

# Tag applied to synthetic records
SYNTHETIC_TAG = "[SYNTHETIC]"


class ProductionImportError(RuntimeError):
    """Raised when an import is attempted in production."""


class ImportResult:
    """Summary of an import run."""

    def __init__(self) -> None:
        self.opportunities_imported: int = 0
        self.opportunities_skipped: int = 0
        self.candidates_imported: int = 0
        self.candidates_skipped: int = 0


def _load_json(filename: str) -> Any:
    """Load and parse a JSON fixture file from the data directory."""
    path = _DATA_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Fixture file not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _parse_opportunity(raw: dict[str, Any]) -> Opportunity:
    """Convert a raw JSON dict into an Opportunity domain model."""
    return Opportunity(
        id=raw["id"],
        title=f"{SYNTHETIC_TAG} {raw['title']}",
        organisation=raw["organisation"],
        sector=raw["sector"],
        location=raw["location"],
        source=raw["source"],
        url=raw["url"],
        remuneration=Remuneration(raw["remuneration"]),
        fee_aud=raw.get("fee_aud"),
        closes_on=raw.get("closes_on"),
        summary=raw.get("summary", ""),
        required_skills=tuple(raw.get("required_skills", ())),
        desirable_skills=tuple(raw.get("desirable_skills", ())),
    )


def _parse_candidate(raw: dict[str, Any]) -> Candidate:
    """Convert a raw JSON dict into a Candidate domain model."""
    connections = [
        Connection(
            name=c["name"],
            relationship=c["relationship"],
            organisations=tuple(c.get("organisations", ())),
            board_seats=tuple(c.get("board_seats", ())),
            strength=c.get("strength", 0.5),
        )
        for c in raw.get("connections", [])
    ]
    return Candidate(
        name=f"{SYNTHETIC_TAG} {raw['name']}",
        headline=raw.get("headline", ""),
        years_experience=raw.get("years_experience", 0),
        skills=raw.get("skills", []),
        sectors=raw.get("sectors", []),
        credentials=raw.get("credentials", []),
        board_experience=raw.get("board_experience", []),
        achievements=raw.get("achievements", []),
        locations=raw.get("locations", []),
        connections=connections,
    )


def import_demo_data(
    opportunity_repo: InMemoryOpportunityRepository,
    candidate_repo: InMemoryCandidateRepository,
    settings: Settings | None = None,
) -> ImportResult:
    """Import demo fixtures into the provided repositories.

    Raises ProductionImportError if APP_ENV is production.
    Idempotent: skips records that already exist (matched by ID).
    """
    if settings is None:
        settings = Settings()

    if settings.app_env == AppEnvironment.PRODUCTION:
        raise ProductionImportError(
            "Refusing to import synthetic fixtures in production environment"
        )

    result = ImportResult()

    # Import opportunities from both vacancy files
    for filename in ("gov_vacancies.json", "mock_sources.json"):
        raw_opportunities = _load_json(filename)
        if not isinstance(raw_opportunities, list):
            logger.warning("Skipping %s: expected a JSON array", filename)
            continue

        for raw in raw_opportunities:
            if not isinstance(raw, dict) or "id" not in raw:
                logger.warning("Skipping invalid record in %s: missing 'id'", filename)
                continue

            opp_id = raw["id"]
            if opportunity_repo.get_by_id(opp_id) is not None:
                result.opportunities_skipped += 1
                continue

            opportunity = _parse_opportunity(raw)
            opportunity_repo.add(opportunity)
            result.opportunities_imported += 1

    # Import candidate
    raw_candidate = _load_json("sample_candidate.json")
    if isinstance(raw_candidate, dict) and "name" in raw_candidate:
        existing = candidate_repo.get_for_user(SYNTHETIC_USER_ID)
        if existing is None:
            candidate = _parse_candidate(raw_candidate)
            candidate_repo.save_for_user(SYNTHETIC_USER_ID, candidate)
            result.candidates_imported += 1
        else:
            result.candidates_skipped += 1
    else:
        logger.warning("Skipping sample_candidate.json: invalid format")

    logger.info(
        "Import complete: %d opportunities imported, %d skipped; "
        "%d candidates imported, %d skipped",
        result.opportunities_imported,
        result.opportunities_skipped,
        result.candidates_imported,
        result.candidates_skipped,
    )

    return result
