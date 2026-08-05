"""Ingestion orchestrator — runs a source adapter and tracks results."""

from __future__ import annotations

from boardmatch.ingestion.base import OpportunitySource, SourceError
from boardmatch.ingestion.models import IngestionRun, IngestionStatus
from boardmatch.models import Opportunity


def run_ingestion(
    source: OpportunitySource,
    *,
    existing: dict[str, Opportunity] | None = None,
) -> IngestionRun:
    """Execute an ingestion run for the given source adapter.

    Parameters
    ----------
    source : OpportunitySource
        An adapter implementing the source protocol.
    existing : dict[str, Opportunity], optional
        Currently stored opportunities keyed by id. Used to compute
        created/updated/deactivated counts. If None, all fetched records
        are treated as new.

    Returns
    -------
    IngestionRun
        Completed run record with status, timing, and record counts.

    Notes
    -----
    Source failures do NOT delete existing data — the run is marked as
    failed and existing records remain untouched.
    """
    run = IngestionRun(source_key=source.source_key)
    run.start()

    existing = existing or {}

    try:
        opportunities = source.fetch()
    except SourceError as exc:
        run.fail(str(exc))
        return run

    records_created = 0
    records_updated = 0
    records_deactivated = 0

    fetched_ids: set[str] = set()

    for opp in opportunities:
        fetched_ids.add(opp.id)
        if opp.id in existing:
            records_updated += 1
        else:
            records_created += 1

    # Records present in existing but absent from fetch are deactivated
    for existing_id in existing:
        if existing_id not in fetched_ids:
            records_deactivated += 1

    stored = records_created + records_updated
    run.succeed(fetched=len(opportunities), stored=stored)
    run.records_created = records_created
    run.records_updated = records_updated
    run.records_deactivated = records_deactivated

    # Attach fetched opportunities to the run for downstream use
    run.opportunities = opportunities  # type: ignore[attr-defined]

    return run
