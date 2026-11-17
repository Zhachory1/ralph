-- v4 control-plane schema (spec: docs/v4-spec.docx, Sec 11.2).
--
-- Scope of this slice: only the tables the PR-review flow needs --
-- discover-reviews' dedup/lock concerns and the registry that lets
-- n8n route a review request without a hardcoded workflow. RFC/council
-- tables, pending_human_notes and the rest land with later slices.

CREATE DATABASE pm_tracker;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE workflow_registry (
    workflow_id       TEXT PRIMARY KEY,
    source_type       TEXT NOT NULL CHECK (source_type IN ('static_n8n', 'static_hermes', 'synthesized_skill')),
    target_profile    TEXT NOT NULL DEFAULT 'orchestrator',
    name              TEXT NOT NULL,
    description       TEXT NOT NULL,
    description_embed VECTOR(1536),
    input_schema      JSONB NOT NULL,
    execution_target  TEXT NOT NULL,
    requires_approval BOOLEAN NOT NULL DEFAULT TRUE,
    content_hash      TEXT NOT NULL,
    version           INTEGER NOT NULL DEFAULT 1,
    status            TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'deprecated', 'draft')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_workflow_embed ON workflow_registry USING hnsw (description_embed vector_cosine_ops);

-- Loop-guard dedup for every GitHub node ID this instance has already acted
-- on (comment_id, review_id, check_suite_id), per Sec 6.1.
CREATE TABLE processed_pr_events (
    event_id      TEXT PRIMARY KEY,
    repo          TEXT NOT NULL,
    pr_number     INTEGER NOT NULL,
    event_type    TEXT NOT NULL,
    author_login  TEXT NOT NULL,
    processed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Per-(repo, pr_number) lease lock with a 25-minute TTL (Sec 6.1), so a
-- crashed or sleeping worker's lock is reclaimed rather than deadlocking
-- the PR.
CREATE TABLE pr_maintenance_locks (
    repo          TEXT NOT NULL,
    pr_number     INTEGER NOT NULL,
    locked_by     TEXT NOT NULL,
    locked_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at    TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '25 minutes',
    PRIMARY KEY (repo, pr_number)
);

-- Inbound coworker PR review tracking (Sec 6.2): discovery and head-SHA
-- advancement state for review-requested:@me polling.
CREATE TABLE inbound_pr_reviews (
    repo              TEXT NOT NULL,
    pr_number         INTEGER NOT NULL,
    head_sha          TEXT NOT NULL,
    author_login      TEXT NOT NULL,
    title             TEXT NOT NULL,
    vikunja_ticket_id INTEGER,
    status            TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'audited', 'reviewed_by_human')),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (repo, pr_number)
);
