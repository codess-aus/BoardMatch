"""Small SQL migration helpers for disposable BoardMatch databases."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


def migration_files(migrations_dir: Path = MIGRATIONS_DIR) -> list[Path]:
    """Return migration files in the order they should be applied."""
    return sorted(migrations_dir.glob("*.sql"))


def _migration_section(sql: str, direction: str) -> str:
    marker = f"-- migrate:{direction}"
    other_marker = "-- migrate:down" if direction == "up" else "-- migrate:up"
    if marker not in sql:
        raise ValueError(f"Missing migration section: {marker}")
    section = sql.split(marker, 1)[1]
    if other_marker in section:
        section = section.split(other_marker, 1)[0]
    return section.strip()


def migration_sql(path: Path, direction: str = "up") -> str:
    """Read one migration section."""
    if direction not in {"up", "down"}:
        raise ValueError("direction must be 'up' or 'down'")
    return _migration_section(path.read_text(encoding="utf-8"), direction)


def apply_migrations(
    connection: sqlite3.Connection,
    *,
    direction: str = "up",
    migrations: Iterable[Path] | None = None,
) -> None:
    """Apply SQL migrations to a SQLite-compatible disposable database."""
    files = list(migration_files() if migrations is None else migrations)
    if direction == "down":
        files.reverse()
    elif direction != "up":
        raise ValueError("direction must be 'up' or 'down'")

    connection.execute("PRAGMA foreign_keys = ON")
    with connection:
        for path in files:
            connection.executescript(migration_sql(path, direction))
