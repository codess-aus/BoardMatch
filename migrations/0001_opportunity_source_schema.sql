-- migrate:up
CREATE TABLE ingestion_sources (
    id TEXT PRIMARY KEY,
    source_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    usage_terms_url TEXT,
    is_enabled BOOLEAN NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1)),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE ingestion_runs (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES ingestion_sources(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT,
    records_seen INTEGER NOT NULL DEFAULT 0 CHECK (records_seen >= 0),
    records_imported INTEGER NOT NULL DEFAULT 0 CHECK (records_imported >= 0)
);

CREATE TABLE opportunities (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    organisation TEXT NOT NULL,
    sector TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unverified'
        CHECK (status IN ('active', 'expired', 'withdrawn', 'unverified', 'archived')),
    remuneration TEXT NOT NULL DEFAULT 'unknown'
        CHECK (remuneration IN ('paid', 'voluntary', 'unknown')),
    fee_amount NUMERIC(12, 2),
    fee_currency TEXT,
    closes_on DATE,
    canonical_url TEXT,
    summary TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (fee_amount IS NULL OR fee_amount >= 0),
    CHECK (fee_amount IS NULL OR remuneration = 'paid'),
    CHECK (fee_currency IS NULL OR (length(fee_currency) = 3 AND upper(fee_currency) = fee_currency)),
    CHECK ((fee_amount IS NULL AND fee_currency IS NULL) OR (fee_amount IS NOT NULL AND fee_currency IS NOT NULL))
);

CREATE TABLE opportunity_source_records (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES ingestion_sources(id) ON DELETE RESTRICT,
    ingestion_run_id TEXT REFERENCES ingestion_runs(id) ON DELETE SET NULL,
    external_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    title TEXT NOT NULL,
    organisation TEXT NOT NULL,
    observed_status TEXT NOT NULL DEFAULT 'unverified'
        CHECK (observed_status IN ('active', 'expired', 'withdrawn', 'unverified', 'archived')),
    raw_payload TEXT,
    first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_id, external_id)
);

CREATE TABLE opportunity_skills (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    skill_name TEXT NOT NULL,
    skill_type TEXT NOT NULL CHECK (skill_type IN ('required', 'desirable')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (opportunity_id, skill_name, skill_type)
);

CREATE TABLE opportunity_verifications (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    source_record_id TEXT REFERENCES opportunity_source_records(id) ON DELETE SET NULL,
    verification_status TEXT NOT NULL DEFAULT 'unverified'
        CHECK (verification_status IN ('verified', 'unverified', 'rejected')),
    verified_at TIMESTAMP,
    last_checked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    verified_by TEXT,
    notes TEXT
);

CREATE INDEX idx_ingestion_runs_source_id ON ingestion_runs(source_id);
CREATE INDEX idx_opportunities_status ON opportunities(status);
CREATE INDEX idx_opportunity_source_records_opportunity_id ON opportunity_source_records(opportunity_id);
CREATE INDEX idx_opportunity_skills_opportunity_id ON opportunity_skills(opportunity_id);
CREATE INDEX idx_opportunity_verifications_opportunity_id ON opportunity_verifications(opportunity_id);

-- migrate:down
DROP INDEX IF EXISTS idx_opportunity_verifications_opportunity_id;
DROP INDEX IF EXISTS idx_opportunity_skills_opportunity_id;
DROP INDEX IF EXISTS idx_opportunity_source_records_opportunity_id;
DROP INDEX IF EXISTS idx_opportunities_status;
DROP INDEX IF EXISTS idx_ingestion_runs_source_id;
DROP TABLE IF EXISTS opportunity_verifications;
DROP TABLE IF EXISTS opportunity_skills;
DROP TABLE IF EXISTS opportunity_source_records;
DROP TABLE IF EXISTS opportunities;
DROP TABLE IF EXISTS ingestion_runs;
DROP TABLE IF EXISTS ingestion_sources;
