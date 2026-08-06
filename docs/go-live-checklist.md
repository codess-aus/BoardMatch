# Go-Live Checklist

This is the sign-off checklist a human operator must work through before
routing real production traffic to BoardMatch. It is written to be honest
about what's actually been validated by this engineering effort (code,
tests, CI) versus what still requires real Azure resources and human
judgment calls that no amount of code review can substitute for.

Treat unchecked items as **blocking** unless you explicitly accept and
document the risk (e.g. in your own change-management record). Do not treat
this list as a rubber stamp — verify each item, don't just skim it.

## 0. Before you start

- [ ] Read `README.md`'s "Current implementation status" section for the
      current honest picture of what's production-ready vs. known follow-up.
- [ ] Read `docs/deployment.md` in full — it documents exactly what
      `.github/workflows/deploy.yml` needs and what has/hasn't been
      validated (static/structural only, no live Azure run yet).
- [ ] Confirm the status of [issue #106](https://github.com/codess-aus/BoardMatch/issues/106)
      (per-router in-memory repo instance inconsistency). It's a
      local/test-mode-only issue that does not affect Postgres-backed
      production behavior, so it is **not** a go-live blocker — but check
      whether it has since merged and update this line accordingly:
      `Status as of this checklist: OPEN, not yet merged.`

## 1. Azure resource provisioning

None of the following has been created as part of this engineering effort —
provisioning is explicitly out of scope for the application repository and
must happen first, via your organization's normal IaC process (Bicep/
Terraform/`az` scripts) or manually for an initial go-live.

- [ ] **Resource groups** for staging and production (or your naming
      convention).
- [ ] **Azure Database for PostgreSQL – Flexible Server** (staging and
      production), with automated backups and point-in-time restore (PITR)
      enabled. Confirm the SKU/storage tier meets expected load (see
      `docs/capacity-planning.md`).
- [ ] **Azure Storage Account** (staging and production) for document
      blobs, with a container named to match `AZURE_STORAGE_CONTAINER`
      (default `documents`). Enable soft-delete and/or blob versioning per
      `docs/disaster-recovery.md`'s recommendations, and choose a
      redundancy tier (LRS/ZRS/GRS) matching your RPO requirements.
- [ ] **Azure Key Vault** (staging and production), with an access policy
      or RBAC role granting the application's managed identity/service
      principal `get`/`list` on secrets. Populate at minimum
      `AZURE-OPENAI-API-KEY` (see `KEY_VAULT_SECRET_ENV_NAMES` in
      `boardmatch/config.py` — extend this list and the vault if you also
      want Document Intelligence/Graph credentials vault-sourced).
- [ ] **Azure Container Registry (ACR)** to hold the built production image.
- [ ] **Azure Container Apps environment** with two Container Apps
      (staging, production), each with outbound network access to the
      Postgres server, Storage account, and Key Vault above.
- [ ] **Azure AI Document Intelligence** resource (for CV/document text
      extraction — optional at the `Settings` validation layer, but a real
      production deployment should have it configured rather than relying
      on the deterministic keyword-based fallback).
- [ ] **Microsoft Entra ID (Azure AD) app registration** for bearer-token
      authentication — set `AUTH_ISSUER` to
      `https://login.microsoftonline.com/<tenant-id>/v2.0` and
      `AUTH_AUDIENCE` to the registration's application ID URI.
- [ ] **Second Entra app registration (or the same one) with Microsoft
      Graph delegated permissions** for the network-sync integration
      (`MS_GRAPH_CLIENT_ID`/`MS_GRAPH_CLIENT_SECRET`/`MS_GRAPH_TENANT_ID`/
      `MS_GRAPH_REDIRECT_URI`) — optional; leave blank to keep the
      simulated/fixture-based network sync if Graph integration isn't
      needed at initial go-live.

## 2. Application configuration

- [ ] Fill in a real `.env`/Container App configuration using
      `docs/production-config-example.md` as the template — **do not
      commit real secret values to source control**. Prefer Container Apps
      secret references or Key Vault (see above) over plain environment
      variables for anything sensitive.
- [ ] Confirm `Settings(app_env="production", ...)` validates against your
      *actual* values (not just the documented example) — e.g. by running
      the app once locally with your real staging config exported as
      environment variables and confirming it starts without a
      `ValidationError`.
- [ ] Set `CORS_ALLOWED_ORIGINS` to the real frontend origin(s) — do not
      leave it blank if any browser-based client will call the API
      cross-origin.
- [ ] Review `RATE_LIMIT_MAX_REQUESTS`/`RATE_LIMIT_WINDOW_SECONDS` and the
      retention windows (`DOCUMENT_RETENTION_DAYS`,
      `EXTRACTED_TEXT_RETENTION_DAYS`, `AUDIT_LOG_RETENTION_DAYS`,
      `NETWORK_DATA_RETENTION_DAYS`) against your organization's actual
      compliance/data-handling requirements — the defaults are reasonable
      starting points, not a substitute for that review.

## 3. GitHub Actions / CI-CD configuration

Per `docs/deployment.md`:

- [ ] Repository secret `AZURE_CREDENTIALS` (service principal with
      `AcrPush` on the registry and `Contributor`/`Container Apps
      Contributor` on both resource groups).
- [ ] Repository variables `ACR_NAME`, `ACR_LOGIN_SERVER`.
- [ ] GitHub Environments named exactly `staging` and `production` created,
      with `production` configured to **require reviewer approval** before
      its jobs run.
- [ ] Environment secret `DATABASE_URL` set on both `staging` and
      `production` Environments, pointing at the respective Postgres
      servers above (never a `sqlite:` URL).
- [ ] Environment variables `RESOURCE_GROUP`, `CONTAINER_APP_NAME`,
      `HEALTH_CHECK_URL` set on both Environments.
- [ ] `GITHUB_TOKEN` permissions/branch protection confirmed so the
      `gitleaks`/CodeQL/`ruff`/`pip-audit` CI gates in `.github/workflows/ci.yml`
      are actually required (branch protection rule on `main` requiring the
      `test` job, and CodeQL, to pass before merge).

## 4. First real deploy — smoke test the pipeline itself

This has **not** been done yet as part of this effort — everything above
was validated by static inspection, unit/contract tests, and (for the
database layer) a real Postgres *service container* in CI, not a live Azure
subscription.

- [ ] Run `.github/workflows/deploy.yml` once against a real **staging**
      environment end to end (build → push to ACR → migrate → deploy →
      smoke test) and confirm it succeeds.
- [ ] Deliberately break the staging health check once (e.g. temporarily
      misconfigure `HEALTH_CHECK_URL` or deploy a bad image) to confirm the
      **automatic rollback** path actually restores the previous image and
      fails the job loudly, as designed in `docs/deployment.md` /
      `docs/ROLLBACK.md`.
- [ ] Confirm the service principal in `AZURE_CREDENTIALS` genuinely has
      sufficient (but least-privilege) RBAC — the deploy should not require
      subscription-level Owner/Contributor.
- [ ] Only after a clean staging run, promote to `production` (which
      requires the reviewer-approval gate you configured in step 3).

## 5. Disaster recovery — rehearse the Azure-specific steps

`docs/disaster-recovery.md` states plainly that its Azure-specific
procedures (Postgres PITR, Blob Storage recovery) are **documented but
unrehearsed**; only the general backup/restore *procedure shape* was
rehearsed locally against SQLite.

- [ ] Run a real "game day": trigger `az postgres flexible-server restore`
      against a non-production copy of the staging database and verify the
      restored data is queryable via the application.
- [ ] Verify Blob Storage soft-delete/versioning recovery works against the
      real staging Storage account.
- [ ] Update `docs/disaster-recovery.md` with the actual commands/output
      observed and remove its "unrehearsed" status note once done.
- [ ] Confirm who is paged and how (see Monitoring below) if a disaster
      recovery event actually occurs.

## 6. Monitoring, alerting, and on-call

- [ ] Set `ALERT_WEBHOOK_URL` to a **real** incoming webhook for your
      on-call/incident channel (Teams, Slack, PagerDuty, etc.) — not a
      placeholder. Confirm the webhook actually delivers a test
      notification end to end (the application redacts the webhook URL
      from its own failure logs — see PR #102 — so don't rely on logs to
      debug webhook delivery; test it directly).
- [ ] Confirm `/metrics` (Prometheus-format) is actually scraped by your
      observability stack (Azure Monitor managed Prometheus, Grafana Agent,
      etc.) — see `docs/operational-dashboards.md` for suggested panels.
- [ ] Confirm `/health/live` and `/health/ready` are wired into your
      platform's own health probes (Container Apps liveness/readiness
      probes), not just the deploy pipeline's one-off smoke test.
- [ ] Confirm someone owns triaging alerts fired by the scheduled
      alert-evaluation loop (`docs/scheduled-jobs.md`) — a webhook with
      nobody watching it is not real alerting.
- [ ] Schedule `scripts/run_retention_cleanup.py` (e.g. as an Azure
      Container Apps job or equivalent scheduled task) rather than relying
      on someone to run it manually.

## 7. DNS / TLS

- [ ] Custom domain configured and verified on the production Container
      App (if using one instead of the default
      `*.azurecontainerapps.io` hostname).
- [ ] TLS certificate provisioned (Container Apps managed certificate or
      your own) and confirmed to auto-renew.
- [ ] `AUTH_AUDIENCE`/redirect URIs (`MS_GRAPH_REDIRECT_URI`, any frontend
      OAuth redirect) updated to match the final production hostname
      before go-live — Entra app registrations are picky about exact
      redirect URI matches.

## 8. Final sign-off

- [ ] All boxes above are checked, or any unchecked box has an explicit,
      documented, accepted-risk justification from whoever owns the
      go-live decision.
- [ ] `python -m pytest`, `ruff check .`, `ruff format --check .`, and
      `pip-audit -r requirements.txt` all pass clean on the exact commit
      being deployed (re-verify — don't rely on a stale CI run from days
      earlier).
- [ ] This checklist itself has been reviewed by someone other than the
      person who ran through it.

---

*Last validated against `main` at commit `41ca233` (2026-08-06): full test
suite (902 passed, 2 skipped, 1 known pre-existing failure in
`tests/test_account.py`), `ruff check .` clean, `ruff format --check .`
clean, `pip-audit -r requirements.txt` clean (no known vulnerabilities in
the resolved dependency set). Issue #106 was open and not yet merged at
that time.*
