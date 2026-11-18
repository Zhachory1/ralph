-- Ready-to-Draft dispatch lease and the pinned PR handoff.
BEGIN;

CREATE TABLE IF NOT EXISTS ready_dispatches (
    ticket_id   BIGINT PRIMARY KEY CHECK (ticket_id > 0),
    status      TEXT NOT NULL CHECK (status IN ('running', 'waiting_merge')),
    lease_token UUID,
    expires_at  TIMESTAMPTZ,
    repo        TEXT,
    pr_number   INTEGER CHECK (pr_number > 0),
    head_sha    TEXT CHECK (head_sha ~ '^[a-f0-9]{40}$'),
    CHECK (
        (status = 'running' AND lease_token IS NOT NULL AND expires_at IS NOT NULL
         AND repo IS NULL AND pr_number IS NULL AND head_sha IS NULL)
        OR
        (status = 'waiting_merge' AND lease_token IS NULL AND expires_at IS NULL
         AND repo IS NOT NULL AND pr_number IS NOT NULL AND head_sha IS NOT NULL)
    )
);

CREATE OR REPLACE FUNCTION claim_ready_dispatch(p_ticket_id BIGINT)
RETURNS UUID LANGUAGE SQL AS $$
    INSERT INTO ready_dispatches AS dispatch (ticket_id, status, lease_token, expires_at)
    VALUES (p_ticket_id, 'running', gen_random_uuid(), clock_timestamp() + INTERVAL '25 minutes')
    ON CONFLICT (ticket_id) DO UPDATE
        SET lease_token = EXCLUDED.lease_token, expires_at = clock_timestamp() + INTERVAL '25 minutes'
        WHERE dispatch.status = 'running' AND dispatch.expires_at <= clock_timestamp()
    RETURNING dispatch.lease_token;
$$;

CREATE OR REPLACE FUNCTION record_ready_draft(
    p_ticket_id BIGINT, p_lease_token UUID, p_repo TEXT, p_pr_number INTEGER, p_head_sha TEXT
)
RETURNS BOOLEAN LANGUAGE SQL AS $$
    WITH recorded AS (
        UPDATE ready_dispatches
        SET status = 'waiting_merge', lease_token = NULL, expires_at = NULL,
            repo = p_repo, pr_number = p_pr_number, head_sha = p_head_sha
        WHERE ticket_id = p_ticket_id AND status = 'running'
          AND lease_token = p_lease_token AND expires_at > clock_timestamp()
        RETURNING ticket_id
    )
    SELECT EXISTS (SELECT FROM recorded);
$$;

COMMIT;
