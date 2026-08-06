"""CLI entry point for importing demo data into development storage.

Usage:
    python -m boardmatch.infrastructure.import_cli
"""

from __future__ import annotations

import logging
import sys

from boardmatch.config import Settings
from boardmatch.infrastructure.importer import (
    ProductionImportError,
    import_demo_data,
)
from boardmatch.infrastructure.repositories.memory import (
    InMemoryCandidateRepository,
    InMemoryOpportunityRepository,
)

logger = logging.getLogger(__name__)


def main() -> int:
    """Run the demo data import and print a summary."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    settings = Settings.from_environment()

    opportunity_repo = InMemoryOpportunityRepository()
    candidate_repo = InMemoryCandidateRepository()

    try:
        result = import_demo_data(opportunity_repo, candidate_repo, settings)
    except ProductionImportError as exc:
        logger.error(str(exc))
        return 1

    print(
        f"Imported {result.opportunities_imported} opportunities "
        f"({result.opportunities_skipped} skipped)"
    )
    print(
        f"Imported {result.candidates_imported} candidates "
        f"({result.candidates_skipped} skipped)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
