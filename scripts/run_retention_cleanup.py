#!/usr/bin/env python
"""Standalone retention cleanup job.

Runs the same cleanup logic as ``POST /api/v1/privacy/cleanup``, but for every
known user in one invocation, so it can be scheduled by an external
scheduler (cron, Azure Container Apps Jobs, Kubernetes CronJob, etc.)
instead of relying on a user hitting the API on demand.

Usage
-----

    python scripts/run_retention_cleanup.py
    python scripts/run_retention_cleanup.py --user-id alice --user-id bob

User discovery
--------------

This script does not have direct access to a production database (BoardMatch
currently uses in-memory/local repositories for most data — see the
persistence-layer workstream). User IDs to clean up are resolved in this
order:

1. Explicit ``--user-id`` CLI arguments (repeatable).
2. The comma-separated ``RETENTION_USER_IDS`` environment variable.
3. Best-effort introspection of the configured document repository, if it
   exposes a ``list_all_users()`` method or an in-memory ``_store`` of
   documents (covers the bundled ``InMemoryDocumentRepository``).

When a real database-backed repository is introduced, prefer adding a
``list_all_users()`` method to it and this script will pick it up
automatically without changes.

Scheduling in production
-------------------------

See docs/scheduled-jobs.md for cron / Azure Container Apps Jobs examples.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boardmatch.config import get_settings
from boardmatch.documents import InMemoryDocumentRepository
from boardmatch.monitoring import (
    configure_structured_logging,
    record_retention_cleanup,
)
from boardmatch.retention import (
    InMemoryExtractedTextRepository,
    RetentionPolicy,
    RetentionService,
)
from boardmatch.storage import LocalStorageBackend

logger = logging.getLogger("boardmatch.retention_cleanup_job")


def _discover_user_ids(document_repo: object) -> list[str]:
    """Best-effort discovery of user ids to clean up (see module docstring)."""
    env_ids = os.getenv("RETENTION_USER_IDS", "")
    if env_ids.strip():
        return [uid.strip() for uid in env_ids.split(",") if uid.strip()]

    list_all = getattr(document_repo, "list_all_users", None)
    if callable(list_all):
        return list(list_all())

    store = getattr(document_repo, "_store", None)
    if isinstance(store, dict):
        return sorted({doc.user_id for doc in store.values()})

    return []


def run(
    user_ids: list[str] | None = None,
    *,
    document_repo: object | None = None,
    extracted_text_repo: object | None = None,
    storage_backend: object | None = None,
) -> int:
    """Run the retention cleanup job. Returns a process exit code.

    Repository arguments are injectable for testing; production/cron usage
    relies on the defaults (in-memory repositories, matching the rest of the
    app until a database-backed repository layer lands).
    """
    settings = get_settings()
    policy = RetentionPolicy(
        document_retention_days=settings.document_retention_days,
        extracted_text_retention_days=settings.extracted_text_retention_days,
        audit_log_retention_days=settings.audit_log_retention_days,
        network_data_retention_days=settings.network_data_retention_days,
    )

    document_repo = (
        document_repo if document_repo is not None else InMemoryDocumentRepository()
    )
    extracted_text_repo = (
        extracted_text_repo
        if extracted_text_repo is not None
        else InMemoryExtractedTextRepository()
    )
    storage_backend = (
        storage_backend if storage_backend is not None else LocalStorageBackend()
    )

    service = RetentionService(
        policy=policy,
        document_repo=document_repo,
        extracted_text_repo=extracted_text_repo,
        storage_backend=storage_backend,
    )

    resolved_user_ids = user_ids if user_ids else _discover_user_ids(document_repo)
    if not resolved_user_ids:
        logger.warning(
            "retention_cleanup_skipped reason=no_users_discovered "
            "hint=pass --user-id or set RETENTION_USER_IDS"
        )
        record_retention_cleanup(0, 0, success=True)
        return 0

    results = service.run_cleanup_for_users(resolved_user_ids)

    total_documents = sum(r.documents_deleted for r in results.values())
    total_texts = sum(r.extracted_texts_deleted for r in results.values())

    for user_id, result in results.items():
        logger.info(
            "retention_cleanup_user user_id=%s documents_deleted=%d texts_deleted=%d",
            user_id,
            result.documents_deleted,
            result.extracted_texts_deleted,
        )

    logger.info(
        "retention_cleanup_complete users_processed=%d total_documents_deleted=%d "
        "total_texts_deleted=%d",
        len(results),
        total_documents,
        total_texts,
    )
    record_retention_cleanup(total_documents, total_texts, success=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user-id",
        action="append",
        dest="user_ids",
        default=None,
        help="User id to clean up. Repeatable. Overrides RETENTION_USER_IDS.",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        help="Log level (default: INFO, or $LOG_LEVEL)",
    )
    args = parser.parse_args(argv)

    configure_structured_logging(args.log_level)

    try:
        return run(args.user_ids)
    except Exception:
        logger.exception("retention_cleanup_failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
