Staged implementation plan for turning `codess-aus/BoardMatch` from a single-user demo into a production-ready application.

The plan assumes:

- Repository: `codess-aus/BoardMatch`
- Default branch: `main`
- Current API: FastAPI
- Current persistence: JSON files and in-memory state
- Current tests: `tests/test_api.py` and `tests/test_boardmatch.py`
- Primary language: Python
- Existing domain concepts: opportunities, candidates, fit scores, applications, readiness, coaching, and network paths

## Delivery strategy

Build in this order:

```text
Foundation
    ↓
Persistence
    ↓
User profiles
    ↓
Versioned API
    ↓
Applications and readiness
    ↓
Real vacancy ingestion
    ↓
Document processing
    ↓
Microsoft identity and Graph
    ↓
AI coaching hardening
    ↓
Production deployment
```

The most important architectural rule is to keep the current scoring logic independent from the database. That allows the existing deterministic tests to continue working while storage changes underneath.

---

# Phase 0: Project foundation

## BM-001: Establish production configuration

**Priority:** P0  
**Dependencies:** None  
**Purpose:** Create a reliable configuration system for local, test, and production environments.

### Scope

Add:

```text
boardmatch/config.py
.env.example
tests/test_config.py
```

Configuration categories:

```text
APP_ENV
DATABASE_URL
AUTH_ISSUER
AUTH_AUDIENCE
AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_API_KEY
AZURE_OPENAI_DEPLOYMENT
AZURE_STORAGE_ACCOUNT
LOG_LEVEL
```

Use typed settings with validation. Secrets must come from environment variables or a secret manager, never committed files.

### Acceptance criteria

- The application starts with a documented local configuration.
- Missing mandatory production settings produce a clear error.
- Test configuration can run without Azure credentials.
- `.env.example` documents every supported variable.
- Secrets are excluded from logs.
- Configuration is injectable into services and tests.

### Test coverage

- Valid configuration loads successfully.
- Missing required configuration fails clearly.
- Test environment uses safe defaults.
- Production configuration rejects insecure settings.
- Secret values are not included in exception messages.

---

## BM-002: Establish code quality and CI gates

**Priority:** P0  
**Dependencies:** None  
**Purpose:** Prevent productionisation work from reducing reliability.

### Scope

Add CI checks for:

```text
pytest
ruff
mypy or pyright
dependency audit
coverage reporting
```

Use a supported stable version of each tool, managed centrally rather than installed ad hoc.

### Acceptance criteria

- Pull requests run automated tests.
- Formatting and lint failures block merging.
- Type-checking runs against application code.
- Coverage is reported.
- CI supports Windows and Linux where practical.
- The test suite is deterministic.

### Test coverage

- CI successfully runs the current test suite.
- A deliberately failing test causes CI failure.
- A deliberate lint violation causes CI failure.
- Coverage artifact is uploaded or reported.

---

## BM-003: Define domain and API boundaries

**Priority:** P0  
**Dependencies:** BM-001  
**Purpose:** Separate domain logic from HTTP and persistence concerns.

### Scope

Create or formalise:

```text
boardmatch/domain/
boardmatch/api/
boardmatch/infrastructure/
boardmatch/services/
```

Move or wrap:

```text
Opportunity
Candidate
FitResult
Application
ApplicationStage
IntroPath
```

Preserve compatibility with the current modules during migration.

### Acceptance criteria

- Fit scoring does not import FastAPI or database code.
- API routes do not contain persistence logic.
- Services coordinate repositories and domain models.
- Existing CLI behaviour continues working.
- Existing tests remain green or are updated without changing behaviour.

### Test coverage

- Domain models can be tested without application startup.
- Fit scoring runs without database access.
- API tests use service dependencies.
- CLI tests continue to pass.

### Interesting implementation fact

The current repository already has a useful boundary in `fit.py`. Its scoring function accepts a `Candidate` and an `Opportunity`, which means it can remain almost entirely independent from the future database layer.

---

# Phase 1: Database and persistence

## BM-004: Add PostgreSQL schema and migration tooling

**Priority:** P0  
**Dependencies:** BM-001, BM-003  
**Purpose:** Replace in-memory application state with durable storage.

### Scope

Add:

```text
alembic.ini
migrations/
boardmatch/infrastructure/db/
```

Initial tables:

```text
users
candidate_profiles
candidate_skills
candidate_credentials
candidate_achievements
candidate_board_experience
```

### Acceptance criteria

- A new database can be created from migrations.
- Migrations can be applied from an empty database.
- Migrations can be downgraded in development.
- Database timestamps use UTC.
- UUIDs are used for internal identifiers.
- Foreign keys and uniqueness constraints are present.
- Database credentials are not committed.

### Test coverage

- Migration upgrade succeeds on an empty database.
- Migration downgrade succeeds in a disposable test database.
- Required constraints reject invalid records.
- Duplicate user identities are rejected.
- Profile ownership relationships work correctly.

---

## BM-005: Add opportunity and source schema

**Priority:** P0  
**Dependencies:** BM-004  
**Purpose:** Store canonical vacancies and their source provenance.

### Scope

Create:

```text
ingestion_sources
ingestion_runs
opportunities
opportunity_source_records
opportunity_skills
opportunity_verifications
```

Support:

```text
active
expired
withdrawn
unverified
archived
```

### Acceptance criteria

- An opportunity can have multiple source records.
- Source URLs and external IDs are stored.
- Duplicate source records are rejected.
- Opportunities retain historical records after expiry.
- Remuneration supports paid, voluntary, and unknown.
- Fee amount supports decimal values and currency.
- Source verification timestamps are stored.

### Test coverage

- Opportunity creation and retrieval.
- Multiple sources linked to one opportunity.
- Duplicate external source record rejection.
- Expiry status update.
- Remuneration validation.
- Missing fee handling.
- Source provenance response.

---

## BM-006: Implement repository interfaces

**Priority:** P0  
**Dependencies:** BM-003, BM-004, BM-005  
**Purpose:** Prevent service code from depending directly on SQLAlchemy.

### Scope

Define protocols such as:

```python name=boardmatch/domain/repositories.py
from typing import Protocol

from .models import Candidate, Opportunity


class CandidateRepository(Protocol):
    """Provides user-scoped access to candidate profiles."""

    def get_for_user(self, user_id: str) -> Candidate | None:
        """Return the candidate profile owned by the specified user."""

    def save_for_user(self, user_id: str, candidate: Candidate) -> Candidate:
        """Save and return the updated candidate profile."""


class OpportunityRepository(Protocol):
    """Provides access to canonical board opportunities."""

    def get_by_id(self, opportunity_id: str) -> Opportunity | None:
        """Return one opportunity or None when not found."""

    def search(self, **filters: object) -> list[Opportunity]:
        """Return opportunities matching the requested filters."""
```

The comments are intentional. These interfaces become the stable contract between domain services and persistence implementations.

### Acceptance criteria

- Fixture repositories and database repositories implement the same interfaces.
- Services depend only on interfaces.
- Tests can inject an in-memory repository.
- Repository methods enforce user ownership where applicable.
- Database sessions are not leaked outside infrastructure code.

### Test coverage

- In-memory repository contract tests.
- Database repository contract tests.
- User isolation tests.
- Not-found behaviour.
- Transaction rollback behaviour.

---

## BM-007: Import existing demo data into development storage

**Priority:** P1  
**Dependencies:** BM-005, BM-006  
**Purpose:** Preserve the existing demo while moving it into the new data model.

### Scope

Create a development-only importer for:

```text
boardmatch/data/gov_vacancies.json
boardmatch/data/mock_sources.json
boardmatch/data/sample_candidate.json
```

Mark imported records as synthetic development data.

### Acceptance criteria

- Existing demo records can be imported repeatedly without duplicates.
- Imported opportunities retain source metadata.
- Synthetic records are visibly labelled in development.
- The CLI and API return equivalent results after import.
- Production startup never automatically imports synthetic fixtures.

### Test coverage

- Import into an empty database.
- Repeat import idempotency.
- Invalid fixture handling.
- Synthetic environment tagging.
- Result parity between fixture and database repositories.

---

# Phase 2: Identity and user profiles

## BM-008: Add authentication abstraction

**Priority:** P0  
**Dependencies:** BM-001, BM-003  
**Purpose:** Make every API operation user-aware without coupling the domain to one identity provider.

### Scope

Add:

```text
CurrentUser
AuthProvider protocol
authentication dependency
```

Initial provider:

```text
Microsoft Entra ID
```

Local development provider:

```text
development identity header or fixed test user
```

The development provider must be disabled in production.

### Acceptance criteria

- Protected routes reject unauthenticated requests.
- Authenticated requests resolve an internal BoardMatch user.
- External identity maps to one internal user.
- Test authentication can be injected without real Microsoft credentials.
- Development bypass cannot run in production.
- User identity is available through FastAPI dependencies.

### Test coverage

- Unauthenticated request returns 401.
- Invalid token returns 401.
- Valid token resolves the correct user.
- First authenticated request creates or updates the internal user.
- Development authentication is rejected when `APP_ENV=production`.

---

## BM-009: Implement candidate profile API

**Priority:** P0  
**Dependencies:** BM-004, BM-006, BM-008  
**Purpose:** Replace `sample_candidate.json` as the active user profile.

### API

```text
GET /api/v1/profile
PUT /api/v1/profile
PATCH /api/v1/profile/skills
PATCH /api/v1/profile/credentials
PATCH /api/v1/profile/experience
```

### Acceptance criteria

- Users can create and retrieve their own profile.
- Users cannot retrieve another user's profile.
- Profile updates increment `profile_version`.
- Profile status supports `draft`, `review_required`, and `confirmed`.
- Invalid years of experience are rejected.
- Empty or malformed profile fields return structured validation errors.
- Existing fit scoring can consume the persisted profile.

### Test coverage

- Create profile.
- Retrieve profile.
- Update profile.
- User isolation.
- Profile version increment.
- Validation errors.
- Confirmed profile status.
- Fit scoring using persisted profile.

### Interesting implementation fact

The current profile model stores skills, credentials, achievements, and board experience as lists. The new API should preserve that user-friendly shape even if the database stores each item in separate tables.

---

## BM-010: Add document metadata and CV upload workflow

**Priority:** P1  
**Dependencies:** BM-004, BM-008, BM-009  
**Purpose:** Allow users to upload CVs without placing files directly in the database.

### API

```text
POST /api/v1/profile/documents
GET /api/v1/profile/documents
GET /api/v1/profile/documents/{document_id}
DELETE /api/v1/profile/documents/{document_id}
```

### Acceptance criteria

- Only supported document types are accepted.
- File size limits are enforced.
- Files are stored in private object storage.
- Document metadata is persisted.
- Duplicate content is detected using a hash.
- Users can delete their documents.
- Deleted files are no longer downloadable.
- Upload processing is asynchronous or clearly represented as pending.

### Test coverage

- Valid PDF upload.
- Unsupported type rejection.
- Size limit rejection.
- Duplicate file rejection.
- User isolation.
- Delete workflow.
- Storage failure handling.
- Metadata persistence.

---

# Phase 3: Versioned API foundation

## BM-011: Introduce `/api/v1`

**Priority:** P0  
**Dependencies:** BM-006, BM-008  
**Purpose:** Create a stable public contract before adding production features.

### Scope

Move or wrap existing routes:

```text
/api/candidate
/api/opportunities
/api/opportunities/{id}
/api/opportunities/{id}/intro-paths
/api/coach/*
/api/tracker
/api/readiness
```

New routes:

```text
/api/v1/profile
/api/v1/opportunities
/api/v1/applications
/api/v1/readiness
/api/v1/coaching
```

### Acceptance criteria

- `/api/v1` routes are documented in OpenAPI.
- Existing demo routes either remain compatible or return deprecation headers.
- Response schemas are explicit Pydantic models.
- No route returns raw dataclasses or database objects.
- Errors use a consistent structure.
- Request IDs are available in logs.

### Test coverage

- Every v1 route has a success test.
- Every protected v1 route has an authentication test.
- Validation failures use the standard error format.
- Not-found errors use stable error codes.
- Legacy route compatibility is tested if retained.

---

## BM-012: Implement pagination and filtering

**Priority:** P1  
**Dependencies:** BM-005, BM-006, BM-011  
**Purpose:** Replace the current in-memory `limit` approach with scalable querying.

### API

```text
GET /api/v1/opportunities?page=1&page_size=20
```

Filters:

```text
status
paid_only
sector
location
min_fee_aud
closes_after
closes_before
source
```

### Acceptance criteria

- Pagination is performed at the repository level.
- Maximum page size is enforced.
- Results include total count or a documented cursor strategy.
- Filters compose correctly.
- Sorting is deterministic.
- Expired opportunities are excluded by default.

### Test coverage

- First page.
- Last page.
- Empty page.
- Maximum page size rejection.
- Combined filters.
- Sorting.
- Expired opportunity filtering.
- Stable ordering for equal values.

---

## BM-013: Standardise API errors and observability

**Priority:** P1  
**Dependencies:** BM-011  
**Purpose:** Make failures supportable in production.

### Acceptance criteria

- Errors contain `code`, `message`, `request_id`, and `details`.
- Internal exception details are not leaked.
- Validation errors identify affected fields.
- Requests log method, route, status, duration, and request ID.
- Sensitive data is excluded from logs.
- Health endpoints exist.

### API

```text
GET /health/live
GET /health/ready
```

### Test coverage

- Structured 404.
- Structured 422.
- Structured 500 in a controlled test.
- Request ID propagation.
- Health endpoint success.
- Readiness failure when database is unavailable.

---

# Phase 4: Applications and readiness

## BM-014: Persist applications

**Priority:** P0  
**Dependencies:** BM-005, BM-006, BM-008, BM-011  
**Purpose:** Replace the global `ReadinessTracker`.

### API

```text
GET    /api/v1/applications
POST   /api/v1/applications
GET    /api/v1/applications/{application_id}
PATCH  /api/v1/applications/{application_id}
DELETE /api/v1/applications/{application_id}
```

### Acceptance criteria

- Applications belong to one authenticated user.
- The same user cannot create duplicate applications for one opportunity.
- Users cannot access another user's applications.
- Application stages use the existing stage values.
- Notes are persisted.
- Deleted applications are handled according to the chosen retention policy.
- Unknown opportunities are rejected.

### Test coverage

- Create application.
- Duplicate application rejection.
- Retrieve application.
- Update stage.
- Update notes.
- Delete application.
- Unknown opportunity.
- User isolation.

---

## BM-015: Add application event history

**Priority:** P1  
**Dependencies:** BM-014  
**Purpose:** Preserve stage transition history and make readiness calculations explainable.

### API

```text
POST /api/v1/applications/{application_id}/events
GET  /api/v1/applications/{application_id}/events
```

### Acceptance criteria

- Every stage change creates an event.
- Previous and new stages are recorded.
- Events are ordered chronologically.
- Invalid transitions are rejected or explicitly marked as reopen operations.
- Event history cannot be silently edited.
- Notes can be attached to transitions.

### Test coverage

- Initial application event.
- Valid stage transition.
- Invalid stage transition.
- Chronological ordering.
- Reopen workflow.
- User isolation.
- Event immutability.

---

## BM-016: Persist fit evaluations

**Priority:** P1  
**Dependencies:** BM-009, BM-011, BM-014  
**Purpose:** Make fit scores reproducible.

### Acceptance criteria

- Evaluations record profile version.
- Evaluations record scoring version.
- Fit results include matched skills, missing skills, rationale, and gap actions.
- Repeated evaluation with the same versions is idempotent.
- A profile change creates a new evaluation version.
- Old evaluations remain available for audit.

### Test coverage

- Evaluation creation.
- Evaluation retrieval.
- Profile version mismatch.
- Scoring version change.
- Idempotent repeated evaluation.
- Missing required skills.
- Score range validation.

---

## BM-017: Implement persistent readiness service

**Priority:** P0  
**Dependencies:** BM-009, BM-014, BM-015, BM-016  
**Purpose:** Replace the in-memory readiness calculation.

### API

```text
GET /api/v1/readiness
GET /api/v1/readiness/history
```

### Acceptance criteria

- Readiness is calculated for the authenticated user only.
- Credential, skills, and pipeline components remain visible.
- Application stage history contributes correctly.
- The score is between 0 and 100.
- The scoring version is returned.
- Next actions are derived from the user's actual profile and applications.
- Results are deterministic for the same data and scoring version.

### Test coverage

- Empty profile.
- Profile with credentials.
- Profile with skills.
- Pipeline stage scoring.
- Score maximum and minimum.
- Next action deduplication.
- User isolation.
- Historical readiness snapshot.

---

# Phase 5: Real vacancy ingestion

## BM-018: Create source adapter interface

**Priority:** P0  
**Dependencies:** BM-005, BM-006  
**Purpose:** Make real ingestion replaceable and testable.

### Scope

Define:

```python name=boardmatch/ingestion/base.py
from typing import Protocol

from boardmatch.domain.models import Opportunity


class OpportunitySource(Protocol):
    """Adapter contract for one approved vacancy source."""

    source_key: str

    def fetch(self) -> list[Opportunity]:
        """Fetch and normalise opportunities from the external source."""
```

The comments explain the key contract. Each source is responsible for fetching and normalising data. Downstream scoring should never know whether the record came from JSON, RSS, or an API.

### Acceptance criteria

- Every source adapter returns the same domain shape.
- Network failures produce typed errors.
- Source metadata is attached to every record.
- Raw source payloads can be retained for audit.
- Adapters can be tested without live network calls.

### Test coverage

- Successful source fetch.
- Empty source result.
- Malformed source record.
- Timeout.
- Rate-limit response.
- Authentication failure.
- Normalisation parity.

---

## BM-019: Implement first approved live source

**Priority:** P0  
**Dependencies:** BM-018  
**Purpose:** Replace synthetic government vacancy data with one real source.

### Acceptance criteria

- The source URL and usage permissions are documented.
- Ingestion can run manually.
- Ingestion produces an `ingestion_run`.
- New records are inserted.
- Existing records are updated.
- Missing records are marked inactive only after a safe policy threshold.
- Source failures do not delete existing data.
- Every listing includes retrieval and verification timestamps.

### Test coverage

- Fixture response creates records.
- Existing record update.
- Withdrawn record handling.
- Duplicate record handling.
- Source outage preserves previous records.
- Malformed response creates a partial or failed run.
- Idempotent ingestion.

---

## BM-020: Add deduplication and canonicalisation

**Priority:** P1  
**Dependencies:** BM-019  
**Purpose:** Prevent the same vacancy appearing multiple times.

### Acceptance criteria

- Organisation names are normalised.
- Titles are normalised.
- Source records retain their original identity.
- Canonical opportunities merge duplicate source records.
- Potential duplicates can be flagged for review.
- Merging does not lose provenance.
- Canonicalisation is deterministic.

### Test coverage

- Exact duplicate.
- Differences in whitespace and case.
- Same vacancy from two sources.
- Similar but distinct vacancies.
- Missing closing date.
- Organisation alias handling.
- Provenance after merge.

---

## BM-021: Add scheduled ingestion operations

**Priority:** P1  
**Dependencies:** BM-019  
**Purpose:** Keep opportunities current without manual commands.

### API

Administrative routes:

```text
POST /api/v1/admin/sources/{source_id}/sync
GET  /api/v1/admin/ingestion-runs
GET  /api/v1/admin/ingestion-runs/{run_id}
```

### Acceptance criteria

- Only authorised administrators can trigger syncs.
- Concurrent runs for one source are prevented or safely coordinated.
- Run status is visible.
- Failures are recorded.
- Metrics are produced for created, updated, and deactivated records.
- Stale data is visible to users.

### Test coverage

- Admin access.
- Non-admin rejection.
- Successful run.
- Concurrent run handling.
- Partial run.
- Failed run.
- Run metrics.

---

# Phase 6: Document intelligence and profile extraction

## BM-022: Implement document processing service

**Priority:** P1  
**Dependencies:** BM-010  
**Purpose:** Parse uploaded CVs into reviewable profile suggestions.

### Acceptance criteria

- Processing status changes through a defined state machine.
- Extracted content is mapped to profile fields.
- Provider failures are retried safely.
- Uncertain fields are marked for review.
- No extracted data overwrites confirmed profile data automatically.
- The raw document and extracted result are linked.

### Test coverage

- Successful extraction fixture.
- Empty document.
- Unsupported document.
- Provider timeout.
- Provider malformed response.
- Retry handling.
- Review-required state.
- Confirmed profile remains unchanged until explicit confirmation.

---

## BM-023: Add profile review and confirmation

**Priority:** P1  
**Dependencies:** BM-022, BM-009  
**Purpose:** Make AI or document-derived data trustworthy.

### API

```text
GET  /api/v1/profile/suggestions
POST /api/v1/profile/suggestions/{suggestion_id}/accept
POST /api/v1/profile/suggestions/{suggestion_id}/reject
```

### Acceptance criteria

- Users can inspect every suggested field.
- Users can accept or reject individual suggestions.
- Confirmed fields retain their source.
- Profile version increments after accepted changes.
- Fit evaluations become stale after profile changes.
- The full confirmation action is auditable.

### Test coverage

- Accept one field.
- Reject one field.
- Accept all.
- Profile version increment.
- Stale fit evaluation.
- Audit event creation.
- User isolation.

---

# Phase 7: Microsoft identity and network integration

## BM-024: Implement Microsoft Entra authentication

**Priority:** P0  
**Dependencies:** BM-008  
**Purpose:** Replace development authentication with production identity.

### Acceptance criteria

- Tokens are validated against the configured issuer.
- Audience and tenant rules are enforced.
- Expired tokens are rejected.
- User identity maps consistently.
- Role claims can distinguish administrators.
- Authentication failures do not reveal token details.

### Test coverage

- Valid token.
- Expired token.
- Wrong audience.
- Wrong issuer.
- Wrong tenant.
- Missing subject claim.
- Admin role.
- User role.

---

## BM-025: Implement consent and integration records

**Priority:** P1  
**Dependencies:** BM-024, BM-004  
**Purpose:** Track third-party permissions explicitly.

### API

```text
GET    /api/v1/integrations
POST   /api/v1/integrations/microsoft/authorize
GET    /api/v1/integrations/microsoft/callback
DELETE /api/v1/integrations/microsoft
```

### Acceptance criteria

- Consent scopes are stored.
- Consent can be revoked.
- Revocation disables future syncs.
- Tokens are stored only through a secure secret mechanism.
- Integration status is visible to the user.
- Audit events are created for grant and revoke actions.

### Test coverage

- Connect integration.
- Callback failure.
- Consent persistence.
- Scope validation.
- Revocation.
- Revoked integration rejects sync.
- Token storage failure.
- Audit events.

---

## BM-026: Import and manage network connections

**Priority:** P1  
**Dependencies:** BM-025  
**Purpose:** Replace seeded connections with user-approved Microsoft Graph data.

### API

```text
GET    /api/v1/network/connections
POST   /api/v1/network/sync
PATCH  /api/v1/network/connections/{connection_id}
DELETE /api/v1/network/connections/{connection_id}
GET    /api/v1/opportunities/{opportunity_id}/intro-paths
```

### Acceptance criteria

- Connections are scoped to the authenticated user.
- Sync requires active consent.
- Imported connections can be approved individually.
- Deleted connections are not used for introduction paths.
- Relationship strength can be user-adjusted.
- Generated outreach only uses approved connections.
- Graph data is not exposed unnecessarily.

### Test coverage

- Successful sync fixture.
- Pagination from Graph.
- Duplicate connection update.
- Consent required.
- User isolation.
- Connection approval.
- Connection deletion.
- Intro path excludes unapproved connections.
- Graph failure handling.

### Important privacy fact

The network feature should be opt-in and user-controlled. A connection being present in Microsoft Graph does not automatically mean the person should be used in a BoardMatch introduction recommendation.

---

# Phase 8: AI coaching production hardening

## BM-027: Persist generated drafts

**Priority:** P1  
**Dependencies:** BM-009, BM-011  
**Purpose:** Make board CVs, bios, and outreach messages traceable.

### API

```text
POST   /api/v1/coaching/board-cv
POST   /api/v1/coaching/director-bio
POST   /api/v1/coaching/outreach
GET    /api/v1/coaching/drafts
GET    /api/v1/coaching/drafts/{draft_id}
DELETE /api/v1/coaching/drafts/{draft_id}
```

### Acceptance criteria

- Drafts belong to one user.
- Drafts record engine, model, prompt version, and profile version.
- Generated content is persisted only after successful generation.
- Template fallback is explicit.
- Users can retrieve and delete drafts.
- Draft generation does not expose another user's profile.

### Test coverage

- Template generation.
- Azure OpenAI mocked generation.
- Provider failure fallback.
- Persisted metadata.
- User isolation.
- Draft deletion.
- Opportunity-specific outreach.
- Sensitive prompt logging prevention.

---

## BM-028: Add AI output validation and safety controls

**Priority:** P1  
**Dependencies:** BM-027  
**Purpose:** Prevent unreliable or unsafe generated content.

### Acceptance criteria

- Generated output is non-empty.
- Required sections are present for each draft type.
- Candidate facts are not invented by the template engine.
- AI output is labelled as generated.
- Prompt and response limits are enforced.
- Rate limits exist per user.
- PII handling and retention rules are documented.
- Failed validation does not create a usable draft.

### Test coverage

- Empty response.
- Malformed response.
- Overlong response.
- Missing required section.
- Provider timeout.
- Rate limit.
- Prompt injection fixture.
- Candidate fact preservation.

---

# Phase 9: Security, privacy, and account lifecycle

## BM-029: Add authorisation and ownership enforcement

**Priority:** P0  
**Dependencies:** BM-008, BM-014, BM-024  
**Purpose:** Ensure no user can access another user's data.

### Acceptance criteria

- Every user-owned repository query includes user scope.
- Resource IDs cannot bypass ownership checks.
- Admin routes are role-protected.
- Deleted users cannot access the API.
- Access checks happen before sensitive resource retrieval where practical.

### Test coverage

- Cross-user profile access.
- Cross-user application access.
- Cross-user draft access.
- Cross-user connection access.
- Admin-only route access.
- Deleted user access.
- Object ID enumeration tests.

---

## BM-030: Add audit logging and data export

**Priority:** P1  
**Dependencies:** BM-008, BM-009, BM-014, BM-025  
**Purpose:** Make sensitive operations traceable and support account deletion.

### API

```text
POST /api/v1/account/export
DELETE /api/v1/account
GET  /api/v1/account/audit-events
```

### Acceptance criteria

- Sensitive actions create audit events.
- Users can request a data export.
- Export includes profile, applications, drafts, and consents.
- Account deletion revokes integrations.
- Account deletion removes or anonymises personal data according to policy.
- Audit records follow the documented retention policy.

### Test coverage

- Export request.
- Export contents.
- Integration revocation during deletion.
- Account deletion.
- Audit event creation.
- Repeated deletion.
- Data isolation after deletion.

---

## BM-031: Add privacy and retention controls

**Priority:** P1  
**Dependencies:** BM-010, BM-025, BM-030  
**Purpose:** Reduce risk around CVs, contacts, tokens, and generated content.

### Acceptance criteria

- Document retention is configurable.
- Extracted CV text has a defined retention period.
- Network data deletion is supported.
- Integration tokens are revocable.
- Privacy-sensitive fields are excluded from ordinary logs.
- The README documents data handling.
- Production storage encryption is required.

### Test coverage

- Retention cleanup selection.
- Expired document deletion.
- Network data deletion.
- Token revocation.
- Log redaction.
- Export after deletion request.

---

# Phase 10: Deployment and operations

## BM-032: Add production containerisation

**Priority:** P1  
**Dependencies:** BM-001, BM-004, BM-011  
**Purpose:** Make deployment repeatable.

### Acceptance criteria

- Application builds into a small production image.
- Development and production configurations are separate.
- The container runs as a non-root user.
- Health checks are configured.
- Database migrations run as an explicit deployment step.
- Secrets are injected at runtime.
- No mock data is loaded in production.

### Test coverage

- Container build.
- Container startup with test configuration.
- Health check.
- Migration execution.
- Missing production secret failure.
- Non-root runtime verification.

---

## BM-033: Add deployment pipeline

**Priority:** P1  
**Dependencies:** BM-002, BM-032  
**Purpose:** Automate safe deployment.

### Acceptance criteria

- Pull requests run CI only.
- Main branch can deploy to a staging environment.
- Production deployment requires approval.
- Database migrations run before application rollout.
- Rollback procedure is documented.
- Deployment logs include release identifiers.

### Test coverage

- CI workflow validation.
- Staging deployment.
- Migration failure handling.
- Rollback test.
- Health check failure blocks rollout.

---

## BM-034: Add monitoring and operational alerts

**Priority:** P1  
**Dependencies:** BM-013, BM-021, BM-032  
**Purpose:** Detect failures before users report them.

### Metrics

```text
http_request_duration
http_error_count
database_latency
ingestion_success_count
ingestion_failure_count
stale_opportunity_count
document_processing_failures
ai_generation_failures
graph_sync_failures
```

### Acceptance criteria

- Logs are structured.
- Metrics have useful dimensions without exposing PII.
- Alerts exist for database failure.
- Alerts exist for repeated ingestion failure.
- Alerts exist for stale opportunity data.
- Alerts exist for authentication failure spikes.
- Operational dashboards are documented.

### Test coverage

- Metric emission.
- Error log redaction.
- Alert rule validation.
- Ingestion failure alert.
- Database readiness failure.

---

# Phase 11: UI and user workflow migration

## BM-035: Migrate the demo UI to authenticated APIs

**Priority:** P1  
**Dependencies:** BM-009, BM-011, BM-014, BM-017  
**Purpose:** Replace the current global demo experience.

### Acceptance criteria

- The UI displays the signed-in user's profile.
- Opportunity results are user-specific.
- Applications can be created and updated.
- Readiness uses persisted data.
- Loading and error states are visible.
- Expired or unverified opportunities are labelled.
- The UI no longer assumes the name “Priya Raman”.
- No UI feature depends on mock-only IDs.

### Test coverage

- Browser or component test for sign-in state.
- Profile load.
- Opportunity search.
- Application creation.
- Readiness display.
- API error display.
- Empty result state.
- Expired opportunity display.

---

## BM-036: Add provenance and trust indicators

**Priority:** P1  
**Dependencies:** BM-019, BM-020, BM-035  
**Purpose:** Make real data understandable and trustworthy.

### Acceptance criteria

Each opportunity displays:

```text
source name
source link
first seen date
last verified date
closing date
status
remuneration confidence
```

### Test coverage

- Provenance rendering.
- Missing source URL.
- Stale record warning.
- Unknown remuneration.
- Withdrawn opportunity.
- Duplicate source indicator.

### Interesting product fact

The difference between a “real” product and a demo is not only live HTTP requests. Trust metadata, freshness, provenance, and failure visibility are equally important because real-world data is incomplete and changes over time.

---

# Suggested ticket dependency graph

```text
BM-001 ─┬─ BM-002
        ├─ BM-003 ─┬─ BM-004 ─┬─ BM-005 ─┬─ BM-006 ─ BM-007
        │           │          │          └─ BM-018 ─ BM-019 ─ BM-020 ─ BM-021
        │           │          └─ BM-010
        │           └─ BM-008 ─┬─ BM-009 ─┬─ BM-011 ─ BM-012
        │                      │          ├─ BM-014 ─ BM-015 ─ BM-017
        │                      │          ├─ BM-016
        │                      │          └─ BM-027 ─ BM-028
        │                      └─ BM-024 ─ BM-025 ─ BM-026
        │
        └─ BM-032 ─ BM-033 ─ BM-034

BM-009 ─ BM-022 ─ BM-023
BM-008 ─ BM-029 ─ BM-030 ─ BM-031
BM-017 ─ BM-035
BM-020 ─ BM-035 ─ BM-036
```

# Recommended release milestones

## Release 0.1: Production foundation

Tickets:

```text
BM-001
BM-002
BM-003
BM-004
BM-005
BM-006
BM-007
```

Outcome:

- PostgreSQL schema exists.
- Existing demo data can be imported.
- Domain logic is separated from storage.
- CI protects the codebase.

## Release 0.2: Authenticated core product

Tickets:

```text
BM-008
BM-009
BM-011
BM-012
BM-013
BM-014
BM-015
BM-017
BM-029
```

Outcome:

- Multiple users can sign in.
- Profiles and applications persist.
- Readiness is user-specific.
- APIs are versioned and protected.

## Release 0.3: Real opportunity data

Tickets:

```text
BM-018
BM-019
BM-020
BM-021
BM-036
```

Outcome:

- At least one approved live source is integrated.
- Listings have provenance and freshness.
- Duplicate and stale records are handled.

## Release 0.4: Real profile and network workflows

Tickets:

```text
BM-010
BM-022
BM-023
BM-024
BM-025
BM-026
```

Outcome:

- Users can upload and review CV information.
- Microsoft identity works.
- Network connections are consent-based.

## Release 0.5: AI and operational hardening

Tickets:

```text
BM-027
BM-028
BM-030
BM-031
BM-032
BM-033
BM-034
BM-035
```

Outcome:

- Generated content is persisted and validated.
- Privacy and account lifecycle are addressed.
- The application can be deployed and monitored.

# Definition of done for every ticket

Every ticket should include:

- Implementation
- Unit tests
- Integration tests where applicable
- Updated OpenAPI documentation
- Migration changes where applicable
- Error handling
- Logging that excludes sensitive data
- Documentation
- Local development instructions
- No regression in existing domain behaviour

# Recommended initial ticket order

Start with this exact sequence:

```text
1. BM-001 Establish production configuration
2. BM-002 Establish code quality and CI gates
3. BM-003 Define domain and API boundaries
4. BM-004 Add PostgreSQL schema and migration tooling
5. BM-005 Add opportunity and source schema
6. BM-006 Implement repository interfaces
7. BM-007 Import existing demo data
8. BM-008 Add authentication abstraction
9. BM-009 Implement candidate profile API
10. BM-011 Introduce /api/v1
11. BM-014 Persist applications
12. BM-017 Implement persistent readiness service
13. BM-018 Create source adapter interface
14. BM-019 Implement the first approved live source
```

This order produces a usable product early while keeping the ingestion work safely separated from the identity and persistence foundations.
