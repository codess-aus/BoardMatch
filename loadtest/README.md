# Load testing

This directory contains a lightweight async load-testing script for
BoardMatch's key API endpoints. It uses `httpx` (already a project
dependency — see `requirements-dev.txt`), so no new dependency was added.

## What it tests

`run_load_test.py` simulates many concurrent virtual users, each with a
unique `X-Dev-User-Id` (dev/local auth header), exercising:

- `GET  /api/v1/opportunities` — list/filter (read-heavy, highest weight)
- `POST /api/v1/fit-evaluations` — fit scoring (read + write)
- `POST /api/v1/coaching/board-cv` — coaching draft generation (most
  expensive endpoint; smallest weight, since it is also the most tightly
  rate-limited: 10 drafts/hour/user route-specific limit plus a shared
  30 req/60s sensitive-path limit covering all of `/api/v1/coaching`)

Each virtual user creates a candidate profile first (`PUT /api/v1/profile`)
so fit-evaluations have something to score against, then issues a
configurable number of weighted-random requests across the three endpoints.
The script talks to a running instance over plain HTTP — it does not import
the FastAPI app in-process — so it works unmodified against a local dev
server or a real deployed environment.

## Running locally

1. Start the app (SQLite is the default local backend — no Postgres/Docker
   required):

   ```powershell
   $env:APP_ENV = "local"
   $env:DATABASE_URL = "sqlite:///./boardmatch.db"
   python -m uvicorn boardmatch.api:app --host 127.0.0.1 --port 8000
   ```

2. In another shell, run the load test:

   ```powershell
   python loadtest/run_load_test.py --base-url http://127.0.0.1:8000 --concurrency 20 --iterations 25
   ```

   Useful flags:
   - `--concurrency N` — number of simulated concurrent virtual users (default 20)
   - `--iterations N` — requests per virtual user after setup (default 25)
   - `--json-out path.json` — dump raw per-request results for further analysis

## Running against a real deployment

Point `--base-url` at the deployed instance. Because the dev-header auth
provider (`X-Dev-User-Id`) is disabled outside `APP_ENV=local`/`test`
(see `boardmatch/auth.py`), you will need to adapt the script's request
headers to present real bearer tokens from your Entra ID auth provider
instead of the dev header, and confirm the target environment's rate-limit
settings (`RATE_LIMIT_MAX_REQUESTS`/`RATE_LIMIT_WINDOW_SECONDS`) and the
coaching-specific 10/hour limit won't make results meaningless for a single
low-cardinality set of test identities — use enough distinct test accounts
to spread across those per-user limits, the same way this script uses many
distinct `X-Dev-User-Id` values locally.

**Run this against a staging environment backed by a real Azure Database for
PostgreSQL instance and realistic `WEB_CONCURRENCY`/gunicorn worker counts
before drawing any production capacity conclusions** — see the important
caveat below.

## ⚠️ Important limitation of the numbers below

**These results were captured against a single-process local `uvicorn` dev
server backed by SQLite, not against a real Postgres deployment behind
gunicorn with multiple workers.** They validate that the application:

- Handles concurrent requests across all three endpoints without crashing
- Does not leak memory/connections in an obviously fatal way over a short
  burst of load
- Degrades in latency (not correctness) under saturation

They say **nothing reliable** about:

- Real Postgres connection-pool behavior under load (SQLite in local/dev
  mode bypasses the pooling code path in
  `boardmatch/infrastructure/db/engine.py` entirely — see
  `docs/capacity-planning.md`)
- Multi-worker (gunicorn/`WEB_CONCURRENCY`) behavior — this was a single
  process, single event loop
- Real-world network latency, Azure OpenAI coaching-generation latency
  (deterministic templates were used locally since no
  `AZURE_OPENAI_*` credentials are configured), or Azure Blob Storage
  latency
- Sustained/soak behavior over minutes or hours

**All pool-sizing and worker-count conclusions must be re-validated against
a real Postgres instance and realistic `WEB_CONCURRENCY` before being relied
upon for production capacity planning.**

### A known local-mode-only quirk this surfaced

In local/SQLite mode, each API router module builds its **own independent**
in-memory repository instance (see
`boardmatch/infrastructure/repositories/factory.py`); they are not
automatically shared across routers the way DB-backed (Postgres) repositories
are (which share one SQLAlchemy session factory/table). Concretely,
`boardmatch/api/v1/opportunities.py`'s opportunity repo (seeded from
`discovery.discover()`) is a different in-memory object than
`boardmatch/api/v1/fit_evaluations.py`'s. The existing test suite works
around this by reaching into the private module-level store directly (see
`tests/test_fit_evaluations.py`). A load test that only calls the public
HTTP API therefore cannot exercise `POST /api/v1/fit-evaluations` end-to-end
in local/SQLite mode unless the target opportunity/candidate happen to exist
in that router's own repo — the numbers below were captured after seeding
those private repos directly (see the comment at the top of the script for
how). **This isolation does not occur against a real Postgres-backed
deployment**, where every router shares the same underlying database — it is
purely an artifact of the in-memory fallback used for local/dev/test. No
production code was changed to work around this; it's flagged here as an
observation surfaced by writing this load test, not something this
workstream fixed.

## Example results (illustrative only — see caveat above)

Captured on this sandbox against `python -m uvicorn boardmatch.api:app`
(single worker) with SQLite, after seeding the fit-evaluations router's
private repos as described above.

Light load (20 concurrent virtual users × 25 iterations):

```
Wall time:        1.96s
Total requests:   520
Throughput:       ~250 req/s
Transport errors: 0
5xx responses:    0
429 (rate-limit): 0

endpoint                 count   2xx/3xx    4xx    5xx    p50 ms    p95 ms    p99 ms
coaching_board_cv           48        48      0      0      44.5     265.1     288.2
fit_evaluation             161       161      0      0      30.6     190.8     283.0
list_opportunities         311       311      0      0      36.0     232.6     348.1
```

Heavier load (100 concurrent virtual users × 40 iterations — beyond what a
single dev-mode uvicorn process is sized for):

```
Wall time:        23.03s
Total requests:   4100
Throughput:       ~178 req/s
Transport errors: 0
5xx responses:    0
429 (rate-limit): 1   (expected — hit a per-user sensitive-path rate limit)

endpoint                 count   2xx/3xx    4xx    5xx    p50 ms    p95 ms    p99 ms
coaching_board_cv          374       373      1      0     282.1    1447.6    2573.0
fit_evaluation            1176      1176      0      0     301.4    1328.4    2454.2
list_opportunities        2550      2550      0      0     319.6    1697.7    3569.0
```

Takeaways from these two runs:

- No crashes, no unhandled 5xx errors, and no connection leaks observed at
  either concurrency level over these short bursts.
- The single 429 at high concurrency is the rate limiter working as
  designed, not a failure.
- Latency (not correctness) is what degrades under saturation on a
  single-process dev server, as expected — this is exactly the kind of
  signal that needs to be re-measured against a properly sized
  gunicorn/`WEB_CONCURRENCY` deployment and real Postgres, per
  `docs/capacity-planning.md`.
