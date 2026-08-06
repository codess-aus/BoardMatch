# Rollback Procedure

This document describes how to roll back a failed BoardMatch deployment.

## Current deployment status

The repository contains CI and deployment workflow scaffolding, a production Dockerfile, and SQL migration files. Some deployment commands are placeholders until the target hosting platform is wired in. Treat this document as the operational checklist to follow once staging/production deployment jobs are connected to real infrastructure.

## Prerequisites

- Access to the deployment environment (staging or production)
- GitHub repository write access
- Knowledge of the last known good release SHA or image tag
- Access to deployment logs and GitHub Actions runs
- A backup/restore process for production data before rolling back schema-affecting releases

## Quick rollback steps

### 1. Identify the last good release

```bash
# List recent deployments and workflow runs
gh run list --workflow=deploy.yml --limit=10

# Or inspect recent commits/tags locally
git log --oneline -10
```

Record the last known good SHA before making changes.

### 2. Revert or redeploy the application

**Option A: Fix forward or create a revert commit**

Use this when the safest path is to undo the faulty application change on `main`:

```bash
# Replace <BAD_SHA> with the commit to revert
git revert <BAD_SHA>
git push origin main
```

The push triggers the deployment workflow for the reverted application state.

**Option B: Redeploy a previous container image**

Use this when the deployment platform supports direct image rollback:

```bash
# Replace <PREVIOUS_SHA> with the last known good release identifier
docker pull ghcr.io/codess-aus/boardmatch:<PREVIOUS_SHA>
docker stop boardmatch-app
docker rm boardmatch-app
docker run -d --name boardmatch-app -p 8000:8000 ghcr.io/codess-aus/boardmatch:<PREVIOUS_SHA>
```

Adapt the commands to the actual container platform once production hosting is configured.

### 3. Roll back database changes only when necessary

The current migration helper is a library for applying SQL sections to a provided SQLite connection in tests; it does not expose a production command-line rollback interface. If a release includes incompatible database changes:

1. Confirm whether the application rollback can run against the newer schema.
2. Prefer forward-compatible migrations where possible.
3. Restore from backup or run a carefully reviewed manual down migration only when the schema is incompatible.
4. Use the `-- migrate:down` section in the relevant SQL migration file as the source for manual rollback SQL.

> **Warning:** Only roll back schema changes after confirming the data impact and backup status. Many migrations should be forward-compatible and should not be reversed during an application-only rollback.

### 4. Verify the rollback

```bash
# Check application liveness
curl -f http://<HOST>:8000/health/live

# Check readiness endpoint
curl -f http://<HOST>:8000/health/ready

# Run smoke tests against the repository or a disposable environment
python -m pytest --tb=short -q
```

Also verify the key user workflows affected by the incident, such as sign-in, profile retrieval, opportunity search, application tracking, and coaching draft generation.

## Rollback decision matrix

| Scenario | Action |
|---|---|
| Tests fail in CI | Block the PR; no production rollback needed |
| Staging deploy fails | Fix forward or revert the change before production |
| Production deploy fails with no schema change | Revert commit or redeploy previous image |
| Production deploy fails after schema change | Assess schema compatibility; restore backup or manually apply reviewed down SQL only if required |
| Data corruption detected | Stop writes if possible, restore from backup, then redeploy a known good application version |

## Communication

1. Notify the operations channel or incident contact.
2. Create a GitHub issue documenting the incident, impact, and rollback decision.
3. Record the release SHA, rollback SHA/image, verification checks, and follow-up actions.
4. After stabilization, conduct a post-mortem.

## Prevention

- Keep schema changes backward-compatible where possible.
- Use feature flags for risky changes.
- Monitor error rates and health checks after each deployment.
- Keep production deployment protected with GitHub Environment approval rules.
- Test rollback paths in staging before relying on them in production.
