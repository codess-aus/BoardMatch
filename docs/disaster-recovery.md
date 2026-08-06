# Disaster Recovery

This document describes how to back up and restore BoardMatch's two stateful
dependencies — the Postgres database and Azure Blob Storage documents — and
records a local rehearsal of the restore *procedure*. It complements
`docs/ROLLBACK.md` (application/deployment rollback) and follows the same
"be explicit about what's real vs. placeholder" style.

## Status of this document

**The cloud-specific steps below (Azure Database for PostgreSQL point-in-time
restore, Blob Storage soft-delete/versioning recovery) are documented but
UNREHEARSED in this sandbox** — there is no real Azure subscription, no
Postgres server, and no Docker available in this environment to actually
exercise `az postgres flexible-server restore` or Blob Storage recovery
against real Azure resources.

What **was** rehearsed locally (see "Local rehearsal" below): a full
backup → simulated disaster → restore → verify cycle against a SQLite
database using the project's own Alembic migrations, to validate that the
*procedure structure* (identify backup → stop writes → restore → verify →
resume) translates correctly. This validates the runbook shape, not
Azure-specific command syntax or Azure's actual RPO/RTO characteristics.

Before relying on this document in a real incident: run through the Azure
sections at least once against a real staging environment (a "game day"),
update this document with the actual commands/output observed, and remove
this status section once that has happened.

## Scope

| Component | Backup mechanism | Covered below |
|---|---|---|
| Azure Database for PostgreSQL (`DATABASE_URL`) | Automated backups + point-in-time restore (PITR) | Yes |
| Azure Blob Storage (`AZURE_STORAGE_ACCOUNT`/`AZURE_STORAGE_CONTAINER`) | Soft-delete, blob versioning, redundancy (LRS/ZRS/GRS) | Yes |
| Application/container image | Redeploy previous image | See `docs/ROLLBACK.md` |
| Azure Key Vault secrets | Vault-level soft-delete/purge protection | Briefly noted below |

## RTO / RPO targets and assumptions

**These are working assumptions for a small team running this application,
not contractually committed SLAs.** State them explicitly rather than
picking numbers that sound precise but aren't backed by a tested incident:

| Scenario | RPO target (max acceptable data loss) | RTO target (max acceptable downtime) | Assumption basis |
|---|---|---|---|
| Database corruption/accidental delete, restore from PITR | ≤ 15 minutes (Azure's PITR restore granularity) | ≤ 2 hours | Azure Database for PostgreSQL Flexible Server supports PITR to any point within the backup retention window at a few minutes' granularity; 2-hour RTO assumes the restore itself (minutes to ~1 hour depending on DB size) plus time to re-point the app, validate, and re-open traffic — **unverified against this app's actual data volume** |
| Full regional Postgres outage | ≤ 5 minutes if geo-redundant backup/replica configured, otherwise "best available backup" | ≤ 4 hours | Depends on whether geo-redundant backup or a read replica in a second region is actually provisioned — **not yet confirmed as configured**; treat as aspirational until infrastructure is verified |
| Blob Storage accidental delete/overwrite | 0 (soft-delete/versioning should make this fully recoverable) if within the retention window | ≤ 30 minutes | Requires soft-delete and versioning to actually be enabled on the storage account — **verify this is turned on**, this document assumes it should be, not that it is |
| Full storage account loss | Depends on redundancy tier (LRS = same-zone only, ZRS = zone-redundant, GRS/GZRS = geo-redundant) | Hours, if failing over to geo-redundant secondary | GRS/GZRS failover is a manual customer-initiated process for read access; write access after a Microsoft-managed or customer-managed failover has its own timeline — **not tested here** |

If your organization has a different, formally agreed RTO/RPO, replace this
table with those figures and cite the source (e.g., an SLA document or
incident postmortem commitment) rather than the assumptions above.

## (a) Azure Database for PostgreSQL: backup and point-in-time restore

Azure Database for PostgreSQL Flexible Server takes automated backups
(full + differential + transaction log) and supports point-in-time restore
into a **new server** (it does not restore in place).

### Restore procedure

1. **Identify the target restore point** (a timestamp before the
   corruption/incident, within the configured backup retention window):

   ```bash
   az postgres flexible-server show \
     --resource-group <RESOURCE_GROUP> \
     --name <SERVER_NAME> \
     --query "{earliestRestoreDate:backup.earliestRestoreDate, retentionDays:backup.backupRetentionDays}"
   ```

2. **Restore to a new server** at the chosen point in time:

   ```bash
   az postgres flexible-server restore \
     --resource-group <RESOURCE_GROUP> \
     --name <NEW_SERVER_NAME> \
     --source-server <SERVER_NAME> \
     --restore-time "2026-01-01T03:00:00Z"
   ```

   For a full regional outage, restore to a **geo-redundant backup** in a
   paired region instead (requires geo-redundant backup to have been
   enabled at server-creation time — this cannot be turned on
   retroactively):

   ```bash
   az postgres flexible-server geo-restore \
     --resource-group <RESOURCE_GROUP> \
     --name <NEW_SERVER_NAME> \
     --source-server <SERVER_RESOURCE_ID> \
     --location <PAIRED_REGION>
   ```

3. **Validate the restored server** before cutting traffic over:
   - Confirm expected tables/row counts:
     `candidates`, `board_opportunities`, `applications`,
     `application_events`, `fit_evaluations` (see
     `alembic/versions/0002_core_repository_tables.py` for the full schema).
   - Confirm the Alembic migration version matches what's expected:
     `python -m alembic current` against the restored server's connection
     string.
   - Spot-check a handful of known records against application-level
     expectations (e.g., a candidate profile you know should exist).

4. **Re-point the application** at the restored server: update
   `DATABASE_URL` (via Key Vault or the app's configured secret source — see
   `boardmatch/config.py`) to the new server's connection string, then
   redeploy/restart the application tier so it picks up the new engine
   (`get_engine`/`get_session_factory` in
   `boardmatch/infrastructure/db/engine.py` cache one engine per process, so
   a full process restart — not just a config reload — is required).

5. **Run `python -m alembic upgrade head`** against the restored server if
   any migrations were applied after the restore point but before the
   incident, so the schema is current.

6. **Resume traffic** and monitor `/metrics` and `/health/ready` closely for
   the first 15-30 minutes (see `docs/operational-dashboards.md`).

7. **Decommission the old (corrupted) server** only after confirming the
   new one is stable and after any required incident/forensic retention
   period.

### Notes

- PITR restores create a **new server name** — plan DNS/connection-string
  cutover as part of the runbook, not as an afterthought.
- Regularly confirm `backup.backupRetentionDays` matches your RPO/compliance
  requirements; the Azure default may be shorter than what's assumed above.
- Geo-redundant backup must be enabled at server creation — audit whether
  the production server actually has this enabled; if not, a full regional
  outage may only be recoverable from same-region backups (i.e., not
  recoverable if the region itself is down).

## (b) Azure Blob Storage: backup, redundancy, and recovery

BoardMatch stores uploaded documents in Azure Blob Storage
(`AZURE_STORAGE_ACCOUNT`/`AZURE_STORAGE_CONTAINER`, see `.env.example` and
the storage integration module). Relevant protections, all configured at
the storage-account/container level (outside application code):

- **Soft-delete** (blob and container level): deleted blobs are retained
  for a configurable retention period and can be undeleted. Enable via:

  ```bash
  az storage blob service-properties delete-policy update \
    --account-name <STORAGE_ACCOUNT> \
    --enable true --days-retained 30
  ```

- **Blob versioning**: keeps prior versions of a blob on overwrite, so an
  accidental overwrite (not just delete) is recoverable:

  ```bash
  az storage account blob-service-properties update \
    --account-name <STORAGE_ACCOUNT> --resource-group <RESOURCE_GROUP> \
    --enable-versioning true
  ```

- **Redundancy tier**: choose based on the RTO/RPO table above —
  `LRS` (same datacenter, cheapest, no geo protection), `ZRS` (zone-redundant
  within a region), `GRS`/`RA-GRS` (geo-redundant, async-replicated to a
  paired region, read access to the secondary with `RA-GRS`), or `GZRS` (both
  zone- and geo-redundant). Check/set with:

  ```bash
  az storage account show --name <STORAGE_ACCOUNT> --query sku.name
  az storage account update --name <STORAGE_ACCOUNT> --sku Standard_GZRS
  ```

  Changing redundancy tier on an existing account is a supported live
  operation for most transitions, but confirm the specific from/to
  combination is supported before relying on it during an incident.

### Recovery procedure (accidental delete/overwrite)

1. **List soft-deleted blobs** in the affected container:

   ```bash
   az storage blob list \
     --account-name <STORAGE_ACCOUNT> --container-name <CONTAINER> \
     --include d --query "[?deleted].{name:name, deletedTime:properties.deletedTime}"
   ```

2. **Undelete** the specific blob(s):

   ```bash
   az storage blob undelete \
     --account-name <STORAGE_ACCOUNT> --container-name <CONTAINER> --name <BLOB_NAME>
   ```

3. **For an overwritten (not deleted) blob with versioning enabled**, list
   and restore the prior version:

   ```bash
   az storage blob list \
     --account-name <STORAGE_ACCOUNT> --container-name <CONTAINER> \
     --include v --query "[?name=='<BLOB_NAME>']"

   az storage blob copy start \
     --account-name <STORAGE_ACCOUNT> --destination-container <CONTAINER> \
     --destination-blob <BLOB_NAME> \
     --source-uri "<VERSIONED_BLOB_URL_WITH_versionid_QUERY_PARAM>"
   ```

4. **For a full storage-account loss** with `GRS`/`GZRS` configured: initiate
   an account failover to the secondary region (Microsoft-managed or
   customer-initiated depending on the outage type), then re-point
   `AZURE_STORAGE_ACCOUNT` if the endpoint changes.

### Notes

- Soft-delete and versioning must be enabled **before** an incident — they
  cannot recover data deleted before they were turned on. Audit that these
  are actually enabled on the production account; this document assumes
  they should be, not that they currently are.
- Document retention settings in the application
  (`DOCUMENT_RETENTION_DAYS`, `EXTRACTED_TEXT_RETENTION_DAYS` in
  `.env.example`) run independently of storage-account soft-delete/versioning
  — the scheduled retention job (`docs/scheduled-jobs.md`) permanently
  removes data past its retention window, and that deletion is intentional,
  not a disaster. Restoring soft-deleted blobs does not un-expire
  legitimately-retention-expired data.

## (c) Azure Key Vault (brief note)

Secrets (currently `AZURE_OPENAI_API_KEY`, per
`boardmatch/config.py:KEY_VAULT_SECRET_ENV_NAMES`) are loaded from Key Vault
when `KEY_VAULT_URL` is set. Key Vault has its own soft-delete (default,
non-optional in most configurations) and optional purge protection.
Recovering a deleted secret/vault:

```bash
az keyvault secret list-deleted --vault-name <VAULT_NAME>
az keyvault secret recover --vault-name <VAULT_NAME> --name <SECRET_NAME>
```

## Local rehearsal (actually performed in this sandbox)

Since no Docker or real Azure/Postgres access was available, the
*procedure shape* (not the Azure-specific commands) was rehearsed against a
local SQLite database built from the project's own Alembic migrations, to
validate that the runbook's steps hold up in practice:

1. Built a schema-correct database via the project's real migration tooling:
   ```bash
   $env:DATABASE_URL = "sqlite:///./dr_rehearsal/primary.db"
   python -m alembic upgrade head
   ```
   This ran both `0001_baseline` and `0002_core_repository_tables` cleanly
   against SQLite — confirming the migrations are portable (no
   Postgres-only types were used in the schema).
2. Inserted sample rows into `candidates` and `board_opportunities`.
3. **"Backup"**: copied the database file
   (`primary.db` → `backup_20260806.db`) and confirmed via `Get-FileHash`
   that the backup is byte-identical to the source at backup time.
4. **Simulated disaster**: deleted `primary.db` outright (harsher than a
   typical corruption scenario, to prove worst-case recoverability).
5. **Restore**: copied the backup file back to the primary path.
6. **Verify**: reconnected and confirmed both row counts and the exact
   sample row (`user-1`, `Jane Director`) were intact post-restore.

Result: **the backup → disaster → restore → verify cycle succeeded
end-to-end** — zero data loss for the rehearsed scenario, because the
"disaster" occurred strictly after the "backup" was taken (as expected;
this is not evidence about Azure PITR's actual RPO granularity, which
depends on Azure's transaction-log shipping behavior, not file copying).

This is a deliberately simplified analog for Azure PITR (which restores
from continuous transaction-log backups, not a point-in-time file copy) and
for Blob Storage soft-delete/versioning (which are storage-service
features, not filesystem copies). It validates the *runbook's structure and
sequencing* — identify recovery point, stop/isolate, restore, validate
schema/data, re-point the app, resume — translates correctly, not the
specific Azure commands or Azure's actual timing characteristics. Those
remain unrehearsed pending real Azure access (see "Status of this document"
above).

## Communication and post-incident

Follow the same communication and post-mortem steps as
`docs/ROLLBACK.md` ("Communication" section) — notify the incident channel,
open a tracking issue with the incident timeline and data-loss window (if
any), and record actual RTO/RPO achieved against the targets in this
document so the assumptions above can be replaced with measured data over
time.
