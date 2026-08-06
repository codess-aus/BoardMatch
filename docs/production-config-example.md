# Example Production Configuration

This document is a **worked example** of an environment configuration that
satisfies every check in `Settings.validate_production_settings`
(`boardmatch/config.py`). It is not a real `.env` file and contains **no real
secrets** — every value below is a placeholder illustrating the *shape*
required (correct scheme, HTTPS, non-SQLite, etc.). Copy `.env.example` for
the full list of variables (including optional ones); this document only
calls out what production specifically requires and why.

It is verified by `tests/test_config.py::test_production_config_example_matches_documentation`,
which constructs a `Settings(app_env="production", ...)` instance from the
exact values below and asserts it validates without raising. If you change
either this document or `validate_production_settings`, keep both in sync —
the test will fail otherwise.

## Required settings and why

`validate_production_settings` (in `boardmatch/config.py`) runs only when
`APP_ENV=production` and enforces the following, as of the current code:

| Setting | Requirement | Reason |
|---|---|---|
| `DATABASE_URL` | Must be set, and must **not** start with `sqlite:` | Production requires a durable, concurrent-safe database (Postgres). In-memory/SQLite is a local/test-only default. |
| `AUTH_ISSUER` | Must be set, must start with `https://` | Bearer-token authentication requires a real Microsoft Entra (or OIDC-compatible) issuer over TLS; the local development-header bypass is rejected outside `local`/`test`. |
| `AUTH_AUDIENCE` | Must be set | Identifies the expected token audience for validation. |
| `AZURE_OPENAI_ENDPOINT` | Must be set, must start with `https://` | Positioning-coach generation requires a real Azure OpenAI resource; template-only fallback is not acceptable in production. |
| `AZURE_OPENAI_API_KEY` | Must be set (non-empty secret) | Credential for the Azure OpenAI resource above. Prefer sourcing this from Key Vault (see below) rather than a plain environment variable. |
| `AZURE_OPENAI_DEPLOYMENT` | Must be set | Names the deployed model to call. |
| `AZURE_STORAGE_ACCOUNT` | Must be set (and must be a valid Azure Storage account name — lowercase alphanumeric, 3-24 chars) | Document uploads are stored in Azure Blob Storage in production; also required whenever `STORAGE_ENCRYPTION_REQUIRED=true` (the default). |
| `LOG_LEVEL` | Must not be `DEBUG` | Avoids leaking verbose/sensitive request detail into production logs. |

Every other setting (`AZURE_DOC_INTELLIGENCE_*`, `MS_GRAPH_*`, `KEY_VAULT_URL`,
`CORS_ALLOWED_ORIGINS`, rate limiting, retention windows, alerting) is
optional at the `Settings` validation layer — BoardMatch degrades gracefully
(deterministic extraction, simulated network sync, no CORS, etc.) when they
are blank. That said, a real production deployment should configure the
Document Intelligence and Microsoft Graph integrations too; see the go-live
checklist (`docs/go-live-checklist.md`) for what "actually ready for real
traffic" requires beyond what `Settings` alone can enforce.

**Note:** `KEY_VAULT_URL` is optional, not one of the hard-required
production settings above — omitting it is valid (secrets then come from
plain environment variables/secret store injection instead of Key Vault).
Configuring it is still **strongly recommended** for production (see below).

## Example values

```env
# --- Core ---
APP_ENV=production
DATABASE_URL=postgresql+psycopg://boardmatch_app:REPLACE_ME@boardmatch-prod.postgres.database.azure.com:5432/boardmatch?sslmode=require
LOG_LEVEL=WARNING

# --- Authentication (Microsoft Entra ID) ---
AUTH_ISSUER=https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/v2.0
AUTH_AUDIENCE=api://boardmatch-prod

# --- Azure OpenAI (positioning coach) ---
AZURE_OPENAI_ENDPOINT=https://boardmatch-prod.openai.azure.com
AZURE_OPENAI_API_KEY=REPLACE_ME_OR_SOURCE_FROM_KEY_VAULT
AZURE_OPENAI_DEPLOYMENT=gpt-4o

# --- Azure Storage (document uploads) ---
AZURE_STORAGE_ACCOUNT=boardmatchprodstorage
AZURE_STORAGE_CONTAINER=documents
STORAGE_ENCRYPTION_REQUIRED=true

# --- Azure AI Document Intelligence (recommended, not enforced by Settings) ---
AZURE_DOC_INTELLIGENCE_ENDPOINT=https://boardmatch-prod-di.cognitiveservices.azure.com
AZURE_DOC_INTELLIGENCE_KEY=REPLACE_ME_OR_SOURCE_FROM_KEY_VAULT

# --- Microsoft Graph (recommended, not enforced by Settings) ---
MS_GRAPH_CLIENT_ID=00000000-0000-0000-0000-000000000000
MS_GRAPH_CLIENT_SECRET=REPLACE_ME_OR_SOURCE_FROM_KEY_VAULT
MS_GRAPH_TENANT_ID=00000000-0000-0000-0000-000000000000
MS_GRAPH_REDIRECT_URI=https://boardmatch.example.com/api/v1/integrations/graph/callback

# --- Key Vault (strongly recommended for production secret sourcing) ---
KEY_VAULT_URL=https://boardmatch-prod-kv.vault.azure.net

# --- CORS / rate limiting / retention (tune per real deployment) ---
CORS_ALLOWED_ORIGINS=https://boardmatch.example.com
RATE_LIMIT_MAX_REQUESTS=30
RATE_LIMIT_WINDOW_SECONDS=60
DOCUMENT_RETENTION_DAYS=365
EXTRACTED_TEXT_RETENTION_DAYS=90
AUDIT_LOG_RETENTION_DAYS=90

# --- Alerting ---
ALERT_WEBHOOK_URL=https://REPLACE_ME.example.com/incoming-webhook
ALERT_EVALUATION_INTERVAL_SECONDS=60
```

## Sourcing secrets from Key Vault instead of plain environment variables

When `KEY_VAULT_URL` is set, `Settings.from_key_vault_and_environment` (used
by the running application, not by the plain-`Settings(...)` constructor
used in tests) fetches secret-bearing values named in
`boardmatch.config.KEY_VAULT_SECRET_ENV_NAMES` from the vault via
`DefaultAzureCredential`, falling back to the environment variable if the
secret isn't found in the vault. Today that list contains
`AZURE_OPENAI_API_KEY` only; extend `KEY_VAULT_SECRET_ENV_NAMES` if/when
Document Intelligence and Graph credentials should also be vault-sourced.

## Validating this example yourself

```powershell
python -m pytest tests/test_config.py -q
```

`test_valid_production_configuration_loads` and
`test_production_config_example_matches_documentation` both construct a
`Settings(app_env="production", ...)` and assert it validates cleanly using
values equivalent to (a subset of, in the first case) the example above.
