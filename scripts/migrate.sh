#!/usr/bin/env bash
# scripts/migrate.sh — Run database migrations as an explicit deployment step.
#
# Usage:
#   ./scripts/migrate.sh [up|down] [revision]
#
# Requires DATABASE_URL to be set in the environment. Delegates to Alembic,
# which resolves the target database from DATABASE_URL via
# boardmatch.config.Settings (see alembic/env.py).
set -euo pipefail

DIRECTION="${1:-up}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL is not set." >&2
    exit 1
fi

if [ "$DIRECTION" != "up" ] && [ "$DIRECTION" != "down" ]; then
    echo "ERROR: Direction must be 'up' or 'down'." >&2
    exit 1
fi

cd "$REPO_ROOT"

if [ "$DIRECTION" = "up" ]; then
    REVISION="${2:-head}"
    echo "Running Alembic migrations (upgrade to $REVISION)..."
    python -m alembic upgrade "$REVISION"
else
    REVISION="${2:--1}"
    echo "Running Alembic migrations (downgrade to $REVISION)..."
    python -m alembic downgrade "$REVISION"
fi

echo "Migrations complete."
