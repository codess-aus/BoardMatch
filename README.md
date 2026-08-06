# BoardMatch

**An AI-assisted product for finding, evaluating, and progressing paid board seats.**

Paid board and NED (non-executive director) positions are often an *invisible market* — rarely advertised, heavily network-driven, and opaque about remuneration. BoardMatch helps candidates discover opportunities, understand fit, prepare board-specific materials, track applications, and manage profile, document, network, privacy, and readiness workflows.

## What it does

| Capability | Primary modules | Current behavior |
|---|---|---|
| **Opportunity discovery** | `boardmatch/discovery.py`, `boardmatch/ingestion/` | Loads demo and source-backed vacancies, normalises records, supports filtering and pagination through `/api/v1/opportunities` |
| **Fit & gap analysis** | `boardmatch/fit.py`, `boardmatch/api/v1/fit_evaluations.py` | Scores a candidate against opportunity requirements and records reproducible fit evaluations |
| **Positioning coach** | `boardmatch/coach.py`, `boardmatch/api/v1/coaching.py` | Drafts board CVs, director bios, and outreach messages using templates or Azure OpenAI when configured |
| **Candidate profile** | `boardmatch/profile_api.py`, `boardmatch/profiles.py` | Provides authenticated profile read/update routes for skills, credentials, experience, and profile details |
| **Document workflow** | `boardmatch/documents.py`, `boardmatch/document_processing.py` | Stores uploaded document metadata and extracts reviewable profile suggestions from document text |
| **Application tracking** | `boardmatch/api/v1/applications.py`, `boardmatch/readiness.py` | Persists application records, status changes, events, and readiness history in the current app process |
| **Network path-finder** | `boardmatch/network.py`, `boardmatch/api/v1/network.py` | Manages user-approved connections and ranks warm introduction routes |
| **Privacy and account controls** | `boardmatch/retention.py`, `boardmatch/audit.py`, `boardmatch/api/v1/privacy.py`, `boardmatch/api/v1/account.py` | Supports retention policies, cleanup, token revocation, network-data deletion, account deletion, audit events, and export |
| **Operations** | `boardmatch/monitoring.py`, `boardmatch/api/health.py`, `boardmatch/api/metrics.py` | Emits in-process metrics, structured JSON logs, alert-rule definitions with a scheduled webhook notifier, a Prometheus-format `/metrics` endpoint, and liveness/readiness endpoints |

## Current implementation status

BoardMatch is between demo and production-ready product:

- The app runs locally with FastAPI and a deterministic CLI demo.
- The stable public API surface is under `/api/v1`.
- A DB-backed repository layer now exists in `boardmatch/infrastructure/repositories/db.py`,
  built on SQLAlchemy 2.x. It provides Postgres-compatible parity for the four repositories
  previously implemented only in-memory in `boardmatch/infrastructure/repositories/memory.py`
  (candidate profiles, opportunities, applications/events, fit evaluations). A factory
  (`boardmatch/infrastructure/repositories/factory.py`) selects memory vs DB-backed repositories
  based on the `DATABASE_URL` scheme: SQLite keeps using in-memory repositories (local/test
  default), while `postgresql://`/`postgresql+psycopg://` URLs use the SQLAlchemy-backed classes.
- Alembic manages schema migrations end to end (`alembic/`), including a baseline revision that
  folds in the original `migrations/0001_opportunity_source_schema.sql` schema. `scripts/migrate.sh`
  and the CI/deploy migration step (`python -m boardmatch.infrastructure.db.migrations`) both run
  the real Alembic upgrade chain against `DATABASE_URL`.
- Draft, document, network-connection, integration/audit-event, and retention-state storage still
  use their own module-local in-memory implementations (`boardmatch/drafts.py`,
  `boardmatch/documents.py`, `boardmatch/api/v1/network.py`, `boardmatch/integrations.py`,
  `boardmatch/audit.py`, `boardmatch/retention.py`) and are not yet wired to the DB-backed layer —
  tracked as follow-up work.
- The DB-backed repositories are covered by contract tests (`tests/repositories/`) run against an
  ephemeral in-memory SQLite database (via SQLAlchemy) rather than a live PostgreSQL server, since
  no Postgres instance is available in this environment; validating against real Postgres in CI is
  tracked as follow-up work.
- Authentication is environment-aware: local/test can use development headers, while production
  requires configured issuer/audience settings and rejects the development bypass.
- Demo source data remains available in `boardmatch/data/`; production startup is designed not to
  automatically import synthetic fixtures.

## How to Use Guide

### 1. Prepare your environment

Use Python 3.12 or another current Python version supported by the dependencies.

```bash
cd /home/runner/work/BoardMatch/BoardMatch
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

For tests and local development tools, also install:

```bash
pip install -r requirements-dev.txt
```

### 2. Configure local settings

Copy the example environment file and adjust values as needed:

```bash
cp .env.example .env
```

For a simple local run, the defaults are enough. The most commonly changed values are:

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `local` | Runtime environment: `local`, `test`, or `production` |
| `DATABASE_URL` | `sqlite:///./boardmatch.db` | Database URL used by configuration and future persistence work |
| `LOG_LEVEL` | `INFO` | Python log level |
| `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_DEPLOYMENT` | empty | Enables Azure OpenAI draft generation when all three are set |
| `AUTH_ISSUER` / `AUTH_AUDIENCE` | empty | Enables Microsoft Entra bearer-token authentication when both are set |
| `AZURE_STORAGE_ACCOUNT` | empty | Required in production when storage encryption is enforced |
| `DOCUMENT_RETENTION_DAYS` | `365` | Uploaded document retention period |
| `EXTRACTED_TEXT_RETENTION_DAYS` | `90` | Extracted CV text retention period |
| `AUDIT_LOG_RETENTION_DAYS` | `90` | Audit log retention period |
| `STORAGE_ENCRYPTION_REQUIRED` | `true` | Requires Azure Storage configuration in production |
| `KEY_VAULT_URL` | empty | When set, secrets (currently `AZURE_OPENAI_API_KEY`) are loaded from Azure Key Vault via `DefaultAzureCredential`, falling back to the plain environment variable if a secret is not found |
| `CORS_ALLOWED_ORIGINS` | empty | Comma-separated list of allowed CORS origins; empty disables cross-origin requests |
| `RATE_LIMIT_MAX_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS` | `30` / `60` | In-process rate limit applied to sensitive routes (coaching/AI generation, document upload) |
| `ALERT_WEBHOOK_URL` | empty | Webhook URL that receives firing-alert notifications; unset means log-only (see `docs/operational-dashboards.md`) |
| `ALERT_EVALUATION_INTERVAL_SECONDS` | `60` | How often the background alert-evaluation loop runs |

### 3. Run the command-line demo

```bash
python -m boardmatch
```

Useful options:

```bash
python -m boardmatch --all
python -m boardmatch --limit 3
```

The CLI prints matched opportunities, fit bands, gap actions, warm-introduction paths, a tailored board CV, outreach copy, and a readiness score.

### 4. Start the web app and API

```bash
uvicorn boardmatch.api:app --reload
```

Open:

- Web UI: <http://127.0.0.1:8000/>
- OpenAPI docs: <http://127.0.0.1:8000/docs>
- Liveness check: <http://127.0.0.1:8000/health/live>
- Readiness check: <http://127.0.0.1:8000/health/ready>
- Metrics (Prometheus format): <http://127.0.0.1:8000/metrics>

### 5. Authenticate local API requests

Most `/api/v1` user routes require an explicit local development identity header when `APP_ENV=local` or `APP_ENV=test`:

```bash
curl -H "X-Dev-User-Id: alice" http://127.0.0.1:8000/api/v1/profile
```

To call admin routes locally, include an admin role:

```bash
curl \
  -H "X-Dev-User-Id: admin" \
  -H "X-Dev-User-Roles: admin" \
  http://127.0.0.1:8000/api/v1/admin/ingestion-runs
```

In production, development headers are ignored. Configure `APP_ENV=production`, `AUTH_ISSUER`, and `AUTH_AUDIENCE`, then call protected routes with a valid Microsoft Entra bearer token.

### 6. Try the main API workflows

List opportunities:

```bash
curl "http://127.0.0.1:8000/api/v1/opportunities?page=1&page_size=10&paid_only=true"
```

Get one opportunity:

```bash
curl http://127.0.0.1:8000/api/v1/opportunities/<opportunity_id>
```

Read and update a profile:

```bash
curl -H "X-Dev-User-Id: alice" http://127.0.0.1:8000/api/v1/profile

curl -X PATCH \
  -H "X-Dev-User-Id: alice" \
  -H "Content-Type: application/json" \
  -d '{"skills":["governance","finance","risk management"]}' \
  http://127.0.0.1:8000/api/v1/profile/skills
```

Create an application:

```bash
curl -X POST \
  -H "X-Dev-User-Id: alice" \
  -H "Content-Type: application/json" \
  -d '{"opportunity_id":"<opportunity_id>","stage":"saved","notes":"Interesting fit"}' \
  http://127.0.0.1:8000/api/v1/applications
```

Check readiness:

```bash
curl -H "X-Dev-User-Id: alice" http://127.0.0.1:8000/api/v1/readiness
curl -H "X-Dev-User-Id: alice" http://127.0.0.1:8000/api/v1/readiness/history
```

Generate coaching drafts:

```bash
curl -X POST \
  -H "X-Dev-User-Id: alice" \
  -H "Content-Type: application/json" \
  -d '{"opportunity_id":"<opportunity_id>"}' \
  http://127.0.0.1:8000/api/v1/coaching/board-cv

curl -X POST \
  -H "X-Dev-User-Id: alice" \
  http://127.0.0.1:8000/api/v1/coaching/director-bio
```

Manage privacy controls:

```bash
curl -H "X-Dev-User-Id: alice" http://127.0.0.1:8000/api/v1/privacy/retention-policies
curl -X POST -H "X-Dev-User-Id: alice" http://127.0.0.1:8000/api/v1/privacy/cleanup
curl -X DELETE -H "X-Dev-User-Id: alice" http://127.0.0.1:8000/api/v1/privacy/network-data
```

### 7. Run tests

```bash
python -m pytest
```

CI also runs Ruff linting and formatting checks:

```bash
ruff check .
ruff format --check .
```

CI additionally runs `pip-audit` (dependency vulnerability scanning), a `gitleaks` secret scan, and a scheduled CodeQL analysis (`.github/workflows/codeql.yml`) as blocking/advisory security gates.

### 8. Optional: run with Docker Compose

```bash
docker compose up --build
```

This starts PostgreSQL and the FastAPI app. The current app still uses in-memory/local implementations for many workflows, so PostgreSQL is primarily useful for local infrastructure and migration experiments until the database repository layer is completed.

#### Production-style run

The `app` container runs `gunicorn` with `uvicorn.workers.UvicornWorker` in the
default `docker-compose.yml`, not plain `uvicorn` — this gives worker
supervision (auto-restart on crash) and graceful, timeout-bounded shutdowns
suitable for rolling deploys. It's controlled by environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `WEB_CONCURRENCY` | `4` | Number of gunicorn/uvicorn worker processes. Size to your CPU allocation, e.g. `2 * cores + 1`. |
| `GUNICORN_TIMEOUT` | `30` | Seconds a worker may spend on a single request before being killed. |
| `GUNICORN_GRACEFUL_TIMEOUT` | `30` | Seconds gunicorn waits for in-flight requests to finish after receiving a shutdown signal. |
| `GUNICORN_KEEPALIVE` | `5` | Seconds to keep idle keep-alive connections open. |

For a more production-realistic local run — resource limits, `restart:
always`, JSON-file log rotation, and a read-only root filesystem for the
`app` container — layer the staging override file:

```bash
docker compose -f docker-compose.yml -f docker-compose.staging.yml up --build
```

See the comments in `Dockerfile` and `docker-compose.staging.yml` for the
full rationale (writable-path requirements under a read-only root
filesystem, least-privilege container flags, and base-image pinning /
Dependabot update strategy).

## API overview

### Legacy demo routes

These routes remain available for backwards-compatible demo usage:

| Endpoint | Purpose |
|---|---|
| `GET /api/candidate` | Demo candidate |
| `GET /api/opportunities` | Discovery + fit + gaps + warm intro path |
| `GET /api/opportunities/{id}` | Single opportunity with full analysis |
| `GET /api/opportunities/{id}/intro-paths` | Ranked introduction routes |
| `POST /api/coach/board-cv` | Tailored board CV |
| `POST /api/coach/bio` | Director bio |
| `POST /api/coach/outreach` | Outreach message |
| `POST /api/tracker` | Demo application stage update |
| `GET /api/readiness` | Demo readiness snapshot |

### Versioned API routes

Use `/api/v1` for current product workflows:

| Area | Routes |
|---|---|
| Profile | `GET /profile`, `PUT /profile`, `PATCH /profile/skills`, `PATCH /profile/credentials`, `PATCH /profile/experience` |
| Opportunities | `GET /opportunities`, `GET /opportunities/{opportunity_id}`, `GET /opportunities/{opportunity_id}/intro-paths` |
| Applications | `GET /applications`, `POST /applications`, `GET /applications/{application_id}`, `PATCH /applications/{application_id}`, `DELETE /applications/{application_id}`, `POST /applications/{application_id}/events`, `GET /applications/{application_id}/events` |
| Readiness | `GET /readiness`, `GET /readiness/history` |
| Fit evaluations | `POST /fit-evaluations`, `GET /fit-evaluations`, `GET /fit-evaluations/{evaluation_id}` |
| Coaching | `POST /coaching/board-cv`, `POST /coaching/director-bio`, `POST /coaching/outreach`, `GET /coaching/drafts`, `GET /coaching/drafts/{draft_id}`, `DELETE /coaching/drafts/{draft_id}` |
| Documents | `POST /profile/documents`, `GET /profile/documents`, `GET /profile/documents/{document_id}`, `DELETE /profile/documents/{document_id}`, `POST /documents/{document_id}/process`, `GET /documents/{document_id}/processing-status`, `GET /documents/{document_id}/extracted-fields` |
| Profile suggestions | `GET /profile/suggestions`, `POST /profile/suggestions/{suggestion_id}/accept`, `POST /profile/suggestions/{suggestion_id}/reject` |
| Integrations | `GET /integrations`, `POST /integrations/microsoft/authorize`, `GET /integrations/microsoft/callback`, `DELETE /integrations/microsoft` |
| Network | `GET /network/connections`, `POST /network/sync`, `PATCH /network/connections/{connection_id}`, `DELETE /network/connections/{connection_id}` |
| Privacy | `GET /privacy/retention-policies`, `POST /privacy/cleanup`, `DELETE /privacy/network-data`, `POST /privacy/revoke-token/{provider}`, `DELETE /privacy/all-data` |
| Account | `GET /account/audit-events`, `POST /account/export`, `DELETE /account` |
| Admin | `POST /admin/sources/{source_id}/sync`, `GET /admin/ingestion-runs`, `GET /admin/ingestion-runs/{run_id}`, duplicate-review routes under `/admin/duplicates` |

For exact request and response schemas, use the generated OpenAPI docs at `/docs`.

## Microsoft and Azure integration points

- **Azure OpenAI / Azure AI Agent Service / Semantic Kernel** — set `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, and `AZURE_OPENAI_DEPLOYMENT` to generate board CVs, bios, and outreach with a model instead of templates. The outbound call is wrapped in retry-with-backoff and a circuit breaker (`boardmatch/resilience.py`).
- **Azure Blob Storage** — set `AZURE_STORAGE_ACCOUNT` (and optionally `AZURE_STORAGE_CONTAINER`, default `documents`) to store uploaded CVs/documents in Azure Blob Storage instead of local disk. Authenticates via `DefaultAzureCredential` — a managed identity in Azure, or `az login`/environment credentials locally. Azure Storage encrypts all data at rest by default (SSE), satisfying `STORAGE_ENCRYPTION_REQUIRED`. The blob client is configured with a bounded retry policy (`retry_total=3`) and explicit connection/read timeouts. **To activate**: grant the app's managed identity (or your local principal) the `Storage Blob Data Contributor` role on the storage account.
- **Azure AI Document Intelligence** — set `AZURE_DOC_INTELLIGENCE_ENDPOINT` (and `AZURE_DOC_INTELLIGENCE_KEY`, or rely on managed identity if omitted) to OCR uploaded CVs/documents via the prebuilt-read model before mapping the recognized text onto profile fields. The outbound SDK call has retry-with-backoff (`boardmatch/resilience.py`) layered underneath the existing circuit breaker, which falls back to the deterministic template extractor after repeated failures (and immediately when not configured), so document processing never crashes because of a Document Intelligence outage. **To activate**: provision an Azure AI Document Intelligence (Form Recognizer) resource and supply its endpoint/key, or grant the app's managed identity the `Cognitive Services User` role.
- **Microsoft Entra ID** — bearer-token authentication is available when `AUTH_ISSUER` and `AUTH_AUDIENCE` are configured.
- **Microsoft Graph** — set `MS_GRAPH_CLIENT_ID`and `MS_GRAPH_CLIENT_SECRET` (plus `MS_GRAPH_TENANT_ID` and `MS_GRAPH_REDIRECT_URI` as needed) to perform a real OAuth authorization-code exchange against Microsoft identity platform on consent, and a real `GET /me/people` call against Microsoft Graph on network sync. Both outbound calls (`exchange_code_for_token`, `fetch_graph_people`) have retry-with-backoff and a circuit breaker (`boardmatch/resilience.py`) layered around them. Without `MS_GRAPH_CLIENT_ID`/`MS_GRAPH_CLIENT_SECRET`, the consent flow and sync use deterministic simulated/fixture data so local development and tests stay fully offline. Real access tokens are held only in memory for the life of the process (never persisted in the current in-memory store) and are cleared on revocation; only a one-way hash is retained for audit. **To activate**: register an Entra ID app with `User.Read`, `Calendars.Read`, `Mail.Read`, and `People.Read` delegated permissions, add a client secret, and set the redirect URI to match your deployment.
- **Azure Key Vault** — set `KEY_VAULT_URL` to load production secrets (currently `AZURE_OPENAI_API_KEY`) via `DefaultAzureCredential` instead of plain environment variables; unset secrets fall back to the environment automatically so local/test usage is unaffected.
- **Power BI / Fabric** — readiness and monitoring responses provide data that can be adapted into dashboards.
- **Copilot Studio** — the REST API can be used as the tool surface for a conversational front end.

## Scoring model

Fit score (0–100): required skills 60, desirable skills 20, sector match 10, governance credentials 10. Governance credentials award full marks for the AICD Company Directors Course and partial credit for existing board experience.

Board-readiness score (0–100): credentials 40, core governance skill coverage 30, pipeline momentum 30.

## Data handling and privacy

BoardMatch handles personal data responsibly. Current controls include:

| Control | Detail |
|---|---|
| **Document retention** | Uploaded documents are automatically selected for deletion after a configurable period, defaulting to 365 days. Set `DOCUMENT_RETENTION_DAYS` to adjust. |
| **Extracted text retention** | Text extracted from CVs/documents is selected for deletion after a shorter period, defaulting to 90 days. Set `EXTRACTED_TEXT_RETENTION_DAYS` to adjust. |
| **Scheduled cleanup** | `scripts/run_retention_cleanup.py` runs the same cleanup logic for every known user on a schedule (cron / Azure Container Apps Jobs / Kubernetes CronJob) instead of relying on the on-demand API call — see `docs/scheduled-jobs.md`. |
| **Network data deletion** | Users can request deletion of all their network/connection data via `DELETE /api/v1/privacy/network-data`. |
| **Token revocation** | OAuth integration tokens can be revoked via `POST /api/v1/privacy/revoke-token/{provider}`, which clears the stored token hash for active integrations. |
| **Right to erasure** | `DELETE /api/v1/privacy/all-data` removes available user documents, extracted text, network records, and active integration tokens from the current repositories. |
| **Log redaction** | Privacy-sensitive fields are redacted by utilities such as `redact_sensitive_data` in `boardmatch/retention.py` and `redact_pii` in `boardmatch/monitoring.py`. |
| **Storage encryption validation** | Production settings require `AZURE_STORAGE_ACCOUNT` when storage encryption is enforced. |
| **Secrets management** | Production secrets can be sourced from Azure Key Vault (`KEY_VAULT_URL`) instead of plain environment variables. |
| **CORS** | Cross-origin requests are disabled by default; configure `CORS_ALLOWED_ORIGINS` to allow specific origins. |
| **Security headers** | Responses include `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and (in production) `Strict-Transport-Security`. |
| **Rate limiting** | Sensitive routes (coaching/AI generation, document upload) are rate-limited in-process; limits are configurable via `RATE_LIMIT_MAX_REQUESTS`/`RATE_LIMIT_WINDOW_SECONDS`. |

## Data note

The bundled opportunities, sample candidate, and sample network are synthetic demo data. No real person, organisation, or vacancy is represented by the bundled fixtures.
