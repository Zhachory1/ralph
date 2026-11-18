-- Persist a pinned merge receipt before attempting the board update.
BEGIN;

CREATE TABLE IF NOT EXISTS ready_merge_receipts (
    ticket_id        BIGINT PRIMARY KEY REFERENCES ready_dispatches(ticket_id),
    merge_commit_sha TEXT NOT NULL CHECK (merge_commit_sha ~ '^[a-f0-9]{40}$'),
    merged_by        TEXT NOT NULL CHECK (merged_by <> ''),
    merged_at        TIMESTAMPTZ NOT NULL,
    board_done_at    TIMESTAMPTZ
);

CREATE OR REPLACE FUNCTION record_ready_merge(
    p_ticket_id BIGINT, p_repo TEXT, p_pr_number INTEGER, p_head_sha TEXT,
    p_merge_commit_sha TEXT, p_merged_by TEXT, p_merged_at TIMESTAMPTZ
)
RETURNS BOOLEAN LANGUAGE SQL AS $$
    WITH accepted AS (
        INSERT INTO ready_merge_receipts AS receipt (ticket_id, merge_commit_sha, merged_by, merged_at)
        SELECT ticket_id, p_merge_commit_sha, p_merged_by, p_merged_at
        FROM ready_dispatches
        WHERE ticket_id = p_ticket_id AND status = 'waiting_merge'
          AND repo = p_repo AND pr_number = p_pr_number AND head_sha = p_head_sha
        ON CONFLICT (ticket_id) DO UPDATE SET merge_commit_sha = EXCLUDED.merge_commit_sha
            WHERE receipt.merge_commit_sha = EXCLUDED.merge_commit_sha
              AND receipt.merged_by = EXCLUDED.merged_by AND receipt.merged_at = EXCLUDED.merged_at
        RETURNING ticket_id
    )
    SELECT EXISTS (SELECT FROM accepted);
$$;

CREATE OR REPLACE FUNCTION mark_ready_board_done(p_ticket_id BIGINT, p_merge_commit_sha TEXT)
RETURNS BOOLEAN LANGUAGE SQL AS $$
    WITH marked AS (
        UPDATE ready_merge_receipts
        SET board_done_at = COALESCE(board_done_at, clock_timestamp())
        WHERE ticket_id = p_ticket_id AND merge_commit_sha = p_merge_commit_sha
        RETURNING ticket_id
    )
    SELECT EXISTS (SELECT FROM marked);
$$;

COMMIT;
