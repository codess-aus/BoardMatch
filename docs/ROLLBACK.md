# Rollback Procedure

This document describes how to roll back a failed deployment for the BoardMatch application.

## Prerequisites

- Access to the deployment environment (staging or production)
- GitHub repository write access
- Knowledge of the last known good release SHA

## Quick Rollback Steps

### 1. Identify the Last Good Release

```bash
# List recent deployments (check GitHub Actions runs)
gh run list --workflow=deploy.yml --limit=10

# Or check git tags / deploy logs for the last successful SHA
git log --oneline -10
```

### 2. Revert the Application

**Option A: Re-deploy a previous commit**

```bash
# Trigger a deployment of the last known good SHA
git revert HEAD --no-edit
git push origin main
```

This creates a revert commit that triggers the deploy pipeline with the previous application state.

**Option B: Manual container rollback**

If using container-based deployment, redeploy the previous image tag:

```bash
# Replace <PREVIOUS_SHA> with the last known good release identifier
docker pull ghcr.io/codess-aus/boardmatch:<PREVIOUS_SHA>
docker stop boardmatch-app
docker run -d --name boardmatch-app ghcr.io/codess-aus/boardmatch:<PREVIOUS_SHA>
```

### 3. Roll Back Database Migrations

If the deployment included database schema changes that must be reverted:

```bash
# Run migrations in the "down" direction
python -m boardmatch.infrastructure.db.migrations --direction down
```

Or connect directly and run the down migration SQL:

```bash
# Each migration file contains a "-- migrate:down" section
# Apply the relevant down migration manually if needed
```

> **Warning:** Only roll back migrations if the new schema is incompatible with the
> previous application version. Many migrations are forward-compatible.

### 4. Verify the Rollback

```bash
# Check application health
curl -f http://<HOST>:8000/health/live

# Run smoke tests against the rolled-back environment
python -m pytest tests/ -k "not slow" --tb=short
```

## Rollback Decision Matrix

| Scenario | Action |
|----------|--------|
| Tests fail in CI | PR is blocked; no rollback needed |
| Staging deploy fails | Fix forward or revert commit |
| Production deploy fails (no migration) | Revert commit → auto-redeploy |
| Production deploy fails (with migration) | Revert commit + run down migration |
| Data corruption detected | Restore from backup + revert commit |

## Communication

1. Notify the team in the `#boardmatch-ops` channel
2. Create a GitHub issue documenting the incident
3. After stabilization, conduct a post-mortem

## Prevention

- All schema changes should be backward-compatible when possible
- Use feature flags for risky changes
- Monitor error rates after each deployment
- The `deploy-production` job requires manual approval via GitHub Environment protection rules
