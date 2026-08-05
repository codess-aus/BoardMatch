#!/usr/bin/env bash
# scripts/migrate.sh — Run database migrations as an explicit deployment step.
#
# Usage:
#   ./scripts/migrate.sh [up|down]
#
# Requires DATABASE_URL to be set in the environment.
set -euo pipefail

DIRECTION="${1:-up}"
MIGRATIONS_DIR="$(cd "$(dirname "$0")/../migrations" && pwd)"

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL is not set." >&2
    exit 1
fi

if [ "$DIRECTION" != "up" ] && [ "$DIRECTION" != "down" ]; then
    echo "ERROR: Direction must be 'up' or 'down'." >&2
    exit 1
fi

echo "Running migrations ($DIRECTION) from $MIGRATIONS_DIR..."

python -c "
import sys
from pathlib import Path

sys.path.insert(0, str(Path('$MIGRATIONS_DIR').parent))
from boardmatch.infrastructure.db.migrations import migration_files, migration_sql

direction = '$DIRECTION'
files = sorted(Path('$MIGRATIONS_DIR').glob('*.sql'))
if direction == 'down':
    files.reverse()

for f in files:
    print(f'  Applying {f.name} ({direction})...')
    sql = migration_sql(f, direction)
    print(f'    SQL length: {len(sql)} chars')

print(f'Prepared {len(files)} migration(s) for execution.')
print('NOTE: Connect to DATABASE_URL and execute the SQL above.')
print('For PostgreSQL, use psql or a migration runner.')
"

# If psql is available and DATABASE_URL is a postgres URL, apply directly
if command -v psql &>/dev/null && [[ "$DATABASE_URL" == postgres* ]]; then
    echo "Applying migrations via psql..."
    for migration in $(ls "$MIGRATIONS_DIR"/*.sql | sort); do
        if [ "$DIRECTION" = "up" ]; then
            echo "  Applying $(basename "$migration") (up)..."
            # Extract the up section and execute
            python -c "
from pathlib import Path
import sys
sys.path.insert(0, '$(dirname "$0")/..')
from boardmatch.infrastructure.db.migrations import migration_sql
sql = migration_sql(Path('$migration'), '$DIRECTION')
print(sql)
" | psql "$DATABASE_URL"
        else
            echo "  Reverting $(basename "$migration") (down)..."
            python -c "
from pathlib import Path
import sys
sys.path.insert(0, '$(dirname "$0")/..')
from boardmatch.infrastructure.db.migrations import migration_sql
sql = migration_sql(Path('$migration'), '$DIRECTION')
print(sql)
" | psql "$DATABASE_URL"
        fi
    done
    echo "Migrations complete."
else
    echo "psql not found or DATABASE_URL is not PostgreSQL."
    echo "Please apply migrations manually or install postgresql-client."
fi
