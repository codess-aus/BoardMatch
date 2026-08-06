# Capacity planning: connection pools and worker sizing

This document gives sizing guidance for the three knobs that most affect
BoardMatch's throughput under load:

- SQLAlchemy `pool_size` / `max_overflow` (`boardmatch/infrastructure/db/engine.py`)
- Gunicorn worker count, `WEB_CONCURRENCY` (`Dockerfile`)
- Azure Database for PostgreSQL's own max-connections limit

**These are starting-point recommendations derived from formulas and code
inspection, refined with a local, single-process, SQLite-backed load test
(see `loadtest/README.md`). They have not been validated against a real
Postgres instance under realistic production load. Treat the numbers below
as a first pass to be re-measured in staging, not a guarantee.**

## How the pieces fit together

Each gunicorn worker process runs its own Python process and therefore its
own SQLAlchemy `Engine` with its own connection pool (`get_engine` in
`engine.py` caches one engine per *process*, not per deployment). So the
total number of Postgres connections a BoardMatch deployment can open is
approximately:

```
total_connections ≈ WEB_CONCURRENCY × (pool_size + max_overflow)
```

Each worker also processes requests one at a time per async event loop, but
FastAPI route handlers in this codebase are synchronous (`def`, not
`async def`) in most places, so a request occupies a worker thread/process
for its full duration including any DB round trips. That makes the
DB pool a hard ceiling on in-flight requests per worker.

## Current defaults (as of this change)

From `boardmatch/infrastructure/db/engine.py`:

| Setting | Default | Notes |
|---|---|---|
| `pool_size` | 5 | Baseline persistent connections per worker |
| `max_overflow` | 10 | Additional burst connections per worker (total 15 max per worker) |
| `pool_timeout` | 30s | How long a request waits for a free connection before erroring |
| `statement_timeout` | 5000ms | Postgres-side statement timeout (SQLite skips this — no pooling/timeout knobs apply for SQLite) |

From `Dockerfile`:

| Setting | Default | Notes |
|---|---|---|
| `WEB_CONCURRENCY` | 4 | Gunicorn worker count, operator-overridable via env var |
| `GUNICORN_TIMEOUT` | 30s | Worker request timeout |

With these defaults: `4 workers × (5 + 10) = 60` max concurrent Postgres
connections from the application tier under full burst — before counting
Alembic migration connections, `psql` admin sessions, or other clients.

## Sizing guidance by expected request rate

Use this as a starting formula, then validate empirically:

1. **Estimate average request duration under load**, `t_avg` (seconds). Our
   local SQLite runs (see `loadtest/README.md`) saw p50 latencies in the
   tens of milliseconds and p95/p99 in the low-hundreds of milliseconds
   under light-to-moderate concurrency, rising into the 1-3 second range
   under intentionally oversaturated load on a single dev-mode process —
   **Postgres round-trip and network latency will change this number**, so
   re-measure `t_avg` against staging before trusting the sizing below.

2. **Estimate target sustained request rate**, `R` (requests/second) for the
   endpoints that hit the database (most do — opportunities, fit-evaluations,
   applications, coaching drafts, etc.).

3. **Required concurrent in-flight DB-bound requests** ≈ `R × t_avg`
   (Little's Law). This is the number of simultaneous DB connections your
   deployment needs available across all workers combined.

4. **Set `WEB_CONCURRENCY × (pool_size + max_overflow)` comfortably above
   that number** — include headroom for latency spikes, retries, and
   background jobs (the scheduled retention jobs and alert-evaluation loop
   in `boardmatch/monitoring.py` also hold connections periodically).

### Example sizing table

Assuming `t_avg ≈ 50ms` (0.05s) once warmed up against a correctly-indexed
Postgres instance in the same region — **re-confirm this assumption in
staging before using this table**:

| Target sustained rate | Concurrent in-flight (R × t_avg) | Suggested total pool capacity (with ~2x headroom) | Suggested `WEB_CONCURRENCY` | Suggested `pool_size` / `max_overflow` per worker |
|---|---|---|---|---|
| 10 req/s | ~0.5 | 4 | 2 | 2 / 2 |
| 50 req/s | ~2.5 | 8 | 4 | 2 / 2 (current default is already generous here) |
| 200 req/s | ~10 | 24 | 4 | 3 / 3 |
| 500 req/s | ~25 | 60 | 6 | 5 / 5 |
| 1000 req/s | ~50 | 120 | 8 | 8 / 7 |

Notes on the table:

- Keep `pool_size` conservative and rely on `max_overflow` for burst
  headroom rather than a permanently large `pool_size` — idle connections
  still consume Postgres server-side resources (`max_connections`).
- **Check the target Azure Database for PostgreSQL Flexible Server SKU's
  `max_connections` limit** before scaling `WEB_CONCURRENCY` or pool sizes
  up — the total across all app instances (if running more than one
  container/replica) must stay under that ceiling with room for
  administrative connections. Consider PgBouncer (transaction pooling) in
  front of Postgres if you need many workers/replicas without raising
  Postgres's own connection limit.
- `WEB_CONCURRENCY` also affects CPU-bound work (fit-scoring, coaching
  draft template rendering); size it against available CPU cores as well
  as DB pool capacity — don't set it far above `2 × cores` for
  a synchronous-handler-heavy app like this one.
- The `pool_timeout` (30s default) and `GUNICORN_TIMEOUT` (30s default)
  should be tuned together: if requests are timing out waiting for a pool
  connection, that's a signal to raise pool capacity or reduce `t_avg`
  (e.g. via query optimization, indexes, or caching), not to blindly raise
  the timeout.

## What still needs real validation

- Actual `t_avg` per endpoint against Azure Database for PostgreSQL in the
  same Azure region as the app, with realistic data volumes (the discovery
  seed data used for local testing is a small fixed fixture, not
  production-scale).
- Behavior under sustained (minutes-to-hours) load, not just short bursts —
  connection pool exhaustion and leaks often only appear over time.
- Multi-worker behavior: the local load test in `loadtest/README.md` ran
  against a single `uvicorn` process, not multiple gunicorn workers.
- Effect of Azure OpenAI coaching-generation latency (when configured) on
  worker occupancy — local testing used the deterministic template
  fallback since no `AZURE_OPENAI_*` credentials were configured.
- Azure Blob Storage document upload/retrieval latency under load.

Re-run the `loadtest/` script against a staging deployment wired to a real
Postgres instance and the production Dockerfile/gunicorn configuration, at
several `WEB_CONCURRENCY`/pool-size combinations, and record actual p50/p95/p99
latencies and error rates before finalizing production sizing.
