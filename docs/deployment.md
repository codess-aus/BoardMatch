# Deployment Pipeline

This document describes the `.github/workflows/deploy.yml` pipeline: what it
does, which Azure resources it assumes exist, and exactly which GitHub
repository/environment secrets and variables must be configured before it can
deploy to a real Azure subscription.

## Pipeline overview

Triggered on every push to `main`. Jobs run in this order:

```
test → build-and-push → migrate-staging → deploy-staging
                                              → migrate-production → deploy-production
```

| Job | Purpose |
|---|---|
| `test` | Lint (`ruff check .`), format check (`ruff format --check .`), and `pytest`. |
| `build-and-push` | Builds the `production` stage of the root `Dockerfile` and pushes it to Azure Container Registry (ACR), tagged with the commit SHA and `latest`. Built once and deployed unchanged to both environments. |
| `migrate-staging` | Runs `python -m boardmatch.infrastructure.db.migrations` (the Alembic upgrade chain — see `boardmatch/infrastructure/db/migrations.py` and `scripts/migrate.sh`) against the staging database, using `DATABASE_URL` from the `staging` GitHub Environment's secrets. |
| `deploy-staging` | Updates the staging Azure Container App to the new image, then polls `GET /health/ready` with retries. On failure it redeploys the image that was running immediately before this deploy and fails the job. Gated by the `staging` GitHub Environment (reviewers can be required). |
| `migrate-production` | Same as `migrate-staging`, against the production database, gated by the `production` Environment. |
| `deploy-production` | Same as `deploy-staging`, against the production Container App. Gated by the `production` Environment, so a manual approval is required before it runs. |

### Why Azure Container Apps

BoardMatch's container is stateless (all durable state is Postgres via
SQLAlchemy/Alembic, plus Azure Blob Storage — see the README's implementation
status). Container Apps gives us consumption-based scaling and first-class
revision management, so `az containerapp update --image` and rollback via a
follow-up `az containerapp update --image <previous>` are simple, built-in
operations — no custom slot-swap scripting needed. Azure App Service for
Containers (`az webapp config container set`) is a reasonable alternative if
the team already standardizes on App Service elsewhere, but was not chosen
here since it adds an always-on plan/VM model this workload doesn't need.

### Rollback

`docs/ROLLBACK.md` describes the manual/incident rollback process. The
pipeline automates the common case:

1. Before updating a Container App, the workflow records the image currently
   running (`az containerapp show --query properties.template.containers[0].image`).
2. After deploying the new image, it polls `/health/ready` (10 attempts, 10s
   apart).
3. If the smoke test never succeeds, the workflow redeploys the previously
   recorded image and fails the job — so the environment is left on the last
   known-good image rather than the broken one, and the failed GitHub Actions
   run makes the incident visible.

This only handles the application-container rollback. Schema-affecting
migrations still require the manual review process in `docs/ROLLBACK.md`
(Alembic migrations should be written to be backward-compatible with the
previous application version whenever possible, precisely because this
automatic rollback does not run `alembic downgrade`).

## Required GitHub configuration

### Repository secrets

| Secret | Used by | Description |
|---|---|---|
| `AZURE_CREDENTIALS` | `build-and-push`, `deploy-staging`, `deploy-production` | JSON output of `az ad sp create-for-rbac --name boardmatch-deploy --sdk-auth --scopes <ACR resource ID> <staging RG ID> <production RG ID>`, i.e. a service principal with `AcrPush` on the registry and `Contributor` (or the narrower `Container Apps Contributor`) on both resource groups. Consumed by `azure/login@v2`. |

### Repository variables

| Variable | Example | Description |
|---|---|---|
| `ACR_NAME` | `boardmatchacr` | Azure Container Registry resource name (used by `az acr login --name`). |
| `ACR_LOGIN_SERVER` | `boardmatchacr.azurecr.io` | Registry's fully-qualified login server, used to build the image tag (`$ACR_LOGIN_SERVER/boardmatch:<sha>`). |

### Environment secrets — `staging` and `production` (configure both, once each)

Set these up as [GitHub Environments](https://docs.github.com/en/actions/deployment/targeting-different-environments/using-environments-for-deployment)
named exactly `staging` and `production`, with required reviewers configured
on `production` (staging can be left unprotected or lightly protected).

| Secret | Description |
|---|---|
| `DATABASE_URL` | Postgres connection string for that environment's database, e.g. `postgresql+psycopg://<user>:<password>@<host>:5432/<db>`. Must not be a `sqlite:` URL — `Settings` rejects that in production (see `boardmatch/config.py`). |

### Environment variables — `staging` and `production`

| Variable | Example | Description |
|---|---|---|
| `RESOURCE_GROUP` | `boardmatch-staging-rg` | Resource group containing that environment's Container App. |
| `CONTAINER_APP_NAME` | `boardmatch-staging` | Name of the Azure Container App to update. |
| `HEALTH_CHECK_URL` | `https://boardmatch-staging.<region>.azurecontainerapps.io` | Public base URL used for the post-deploy smoke test against `/health/ready`. No trailing slash. |

## Prerequisites not managed by this workflow

The workflow assumes the following already exist (e.g. provisioned via
Bicep/Terraform/`az` scripts outside this repo, since infra-as-code for these
resources is out of scope for this change):

- An Azure Container Registry.
- Two Azure Container Apps (staging, production) already running some image
  from that registry, in an environment with outbound access to the
  Postgres/Blob Storage/Key Vault resources those environments use.
- Two Postgres databases (or equivalent), reachable from the Container Apps
  environment, with `DATABASE_URL` pointed at each.
- Any Key Vault-backed secrets the application itself needs at runtime
  (`KEY_VAULT_URL`, etc. — see the README's environment variable table);
  these are runtime configuration on the Container App itself, not GitHub
  Actions secrets, and are unrelated to the deploy pipeline's own secrets
  above.

## What could not be validated in this sandbox

This pipeline was authored and validated by static inspection only — there is
no live Azure subscription, ACR, or Container Apps environment available
here. Validated:

- YAML is syntactically valid (`python -c "import yaml; yaml.safe_load(...)"`).
- Structural checks in `tests/test_ci_workflow.py` (job names, dependency
  chain, environment gating, migration invocation, Azure login/ACR/docker
  steps, smoke-test + rollback wiring).
- Action names/versions and command syntax (`azure/login@v2`, `az acr login`,
  `az containerapp update --image`, `az containerapp show --query ...`)
  cross-checked against Microsoft Learn documentation.

Not validated (requires real Azure credentials/resources):

- That the service principal in `AZURE_CREDENTIALS` actually has sufficient
  RBAC to push to ACR and update the two Container Apps.
- That `az containerapp show`'s query path
  (`properties.template.containers[0].image`) matches the exact JSON shape
  returned by the CLI version running in Actions.
- End-to-end timing of the smoke-test retry loop against real cold-start
  behavior of a newly deployed revision.
- The rollback path actually restoring a healthy revision (i.e. that the
  previously recorded image is still a valid target — it will not be if the
  registry retention policy has already deleted it).
