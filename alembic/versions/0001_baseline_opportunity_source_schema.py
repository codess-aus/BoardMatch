"""Baseline: fold in the legacy opportunity/source-tracking schema.

Revision ID: 0001_baseline
Revises:
Create Date: 2025-01-01

This revision replays ``migrations/0001_opportunity_source_schema.sql`` verbatim
so Alembic becomes the single migration tool going forward while preserving
the original schema intent for the ingestion/source-tracking pipeline
(``ingestion_sources``, ``ingestion_runs``, ``opportunities``,
``opportunity_source_records``, ``opportunity_skills``,
``opportunity_verifications``).

Note: this SQL was originally authored for disposable SQLite-compatible test
databases (see ``boardmatch/infrastructure/db/migrations.py`` and the
project README). It has not been adapted for Postgres-specific syntax
differences (e.g. boolean literals); doing so is tracked as follow-up work
before this baseline is applied to a real Postgres environment.
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

_LEGACY_SQL_FILE = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "0001_opportunity_source_schema.sql"
)


def _statements(direction: str) -> list[str]:
    from boardmatch.infrastructure.db.migrations import migration_sql

    sql = migration_sql(_LEGACY_SQL_FILE, direction)
    # Split on statement terminators — the DBAPI drivers used here (sqlite3,
    # psycopg) do not support executing multiple statements in one call.
    return [stmt.strip() for stmt in sql.split(";") if stmt.strip()]


def upgrade() -> None:
    for statement in _statements("up"):
        op.execute(statement)


def downgrade() -> None:
    for statement in _statements("down"):
        op.execute(statement)
