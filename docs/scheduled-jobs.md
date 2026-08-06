# Scheduled Jobs

BoardMatch has one scheduled/background job today: retention cleanup. This document explains what it does and how to schedule it in production.

## Retention cleanup (`scripts/run_retention_cleanup.py`)

`POST /api/v1/privacy/cleanup` runs retention cleanup for a single authenticated user, on demand. `scripts/run_retention_cleanup.py` runs the same `RetentionService` logic for every known user in one invocation, so it can be triggered automatically by a scheduler instead of relying on a user calling the API.

It:

1. Builds a `RetentionService` from the configured retention policy (`DOCUMENT_RETENTION_DAYS`, `EXTRACTED_TEXT_RETENTION_DAYS`, etc. — see `.env.example`).
2. Resolves which user ids to clean up (see "User discovery" below).
3. Runs `cleanup_expired_documents` / `cleanup_expired_texts` per user, logging a structured summary line per user and an overall summary line.
4. Emits `retention_cleanup_runs`, `retention_documents_deleted`, and `retention_texts_deleted` metrics via `boardmatch.monitoring.MetricsCollector` (visible on `/metrics` — see `docs/operational-dashboards.md`).

Per-user failures are caught and logged individually; one user's failure does not abort the run for the rest.

### User discovery

BoardMatch's document/extracted-text repositories are in-memory today (see the persistence-layer workstream), so a separate scheduler process has no shared state with the running API process. Until a database-backed repository lands, user ids are resolved in this priority order:

1. Explicit `--user-id` CLI arguments (repeatable): `python scripts/run_retention_cleanup.py --user-id alice --user-id bob`
2. The `RETENTION_USER_IDS` environment variable (comma-separated).
3. Best-effort introspection of the document repository (works automatically once a real repository exposes `list_all_users()`).

If none of these produce any user ids, the script logs a warning and exits `0` (not an error — an empty run is a valid outcome, e.g. in a fresh environment).

### Running it manually

```bash
python scripts/run_retention_cleanup.py --user-id alice --user-id bob
```

or

```bash
RETENTION_USER_IDS="alice,bob" python scripts/run_retention_cleanup.py
```

### Scheduling with cron

```cron
# Run retention cleanup daily at 02:00
0 2 * * * cd /app && RETENTION_USER_IDS="$(cat /etc/boardmatch/retention-users.txt)" \
    /app/.venv/bin/python scripts/run_retention_cleanup.py >> /var/log/boardmatch/retention.log 2>&1
```

### Scheduling with Azure Container Apps Jobs

```bash
az containerapp job create \
  --name boardmatch-retention-cleanup \
  --resource-group <resource-group> \
  --environment <container-apps-environment> \
  --trigger-type Schedule \
  --cron-expression "0 2 * * *" \
  --image <your-registry>/boardmatch:latest \
  --command "python" "scripts/run_retention_cleanup.py" \
  --env-vars "RETENTION_USER_IDS=secretref:retention-user-ids" \
  --replica-timeout 600
```

Store `RETENTION_USER_IDS` (or, once available, database credentials for a real `list_all_users()`-capable repository) as a Container Apps Job secret rather than a plaintext environment variable.

### Scheduling with a Kubernetes CronJob

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: boardmatch-retention-cleanup
spec:
  schedule: "0 2 * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: retention-cleanup
              image: <your-registry>/boardmatch:latest
              command: ["python", "scripts/run_retention_cleanup.py"]
              envFrom:
                - secretRef:
                    name: boardmatch-retention-secrets
          restartPolicy: OnFailure
```

## Follow-ups

- Once a database-backed document repository is available, add a `list_all_users()` method to it; `_discover_user_ids()` in `scripts/run_retention_cleanup.py` will pick it up automatically without further changes.
- Consider extending the job to also purge expired audit log entries and stale network data once those retention flows have persistent-store equivalents.
