"""Baseline: fold in the legacy opportunity/source-tracking schema.

Revision ID: 0001_baseline
Revises:
Create Date: 2025-01-01

This revision replays ``migrations/0001_opportunity_source_schema.sql`` so
Alembic becomes the single migration tool going forward while preserving
the original schema intent for the ingestion/source-tracking pipeline
(``ingestion_sources``, ``ingestion_runs``, ``opportunities``,
``opportunity_source_records``, ``opportunity_skills``,
``opportunity_verifications``).

The legacy SQL was originally authored for disposable SQLite-compatible test
databases (see ``boardmatch/infrastructure/db/migrations.py`` and the
project README). It is replayed verbatim for SQLite, but is adapted for
Postgres-specific syntax differences (currently: boolean literal handling —
SQLite's dynamic typing accepts integer literals for BOOLEAN columns, while
Postgres enforces the declared type strictly) via ``_adapt_for_postgres``
below, keyed off ``op.get_bind().dialect.name``.
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


def _adapt_for_postgres(sql: str) -> str:
    """Rewrite SQLite-flavoured syntax in the legacy schema for Postgres.

    SQLite's dynamic typing accepts integer literals (``0``/``1``) for
    ``BOOLEAN`` columns, but Postgres enforces the declared column type
    strictly — ``DEFAULT 1`` against a ``BOOLEAN`` column raises
    ``column "is_enabled" is of type boolean but default expression is of
    type integer``. Rewrite the schema's one boolean default
    (``ingestion_sources.is_enabled``) to use a native boolean literal; the
    redundant ``CHECK (... IN (0, 1))`` is dropped since the ``BOOLEAN``
    column type already constrains the value to true/false.
    """
    return sql.replace(
        "is_enabled BOOLEAN NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1))",
        "is_enabled BOOLEAN NOT NULL DEFAULT TRUE",
    )


def _statements(direction: str, dialect_name: str) -> list[str]:
    from boardmatch.infrastructure.db.migrations import migration_sql

    sql = migration_sql(_LEGACY_SQL_FILE, direction)
    if dialect_name == "postgresql":
        sql = _adapt_for_postgres(sql)
    # Split on statement terminators — the DBAPI drivers used here (sqlite3,
    # psycopg) do not support executing multiple statements in one call.
    return [stmt.strip() for stmt in sql.split(";") if stmt.strip()]


def upgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    for statement in _statements("up", dialect_name):
        op.execute(statement)


def downgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    for statement in _statements("down", dialect_name):
        op.execute(statement)
