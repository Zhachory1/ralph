\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    first_claim UUID;
    next_claim UUID;
BEGIN
    first_claim := claim_ready_dispatch(101);
    IF first_claim IS NULL OR claim_ready_dispatch(101) IS NOT NULL THEN
        RAISE EXCEPTION 'A duplicate Ready event must not claim active work';
    END IF;

    UPDATE ready_dispatches SET expires_at = clock_timestamp() - INTERVAL '1 second'
    WHERE ticket_id = 101;
    IF record_ready_draft(101, first_claim, 'fixture/project', 7, repeat('a', 40)) THEN
        RAISE EXCEPTION 'An expired worker must not record a draft';
    END IF;

    next_claim := claim_ready_dispatch(101);
    IF next_claim IS NULL OR next_claim = first_claim THEN
        RAISE EXCEPTION 'An expired lease must be replaced with a new fence token';
    END IF;
    IF record_ready_draft(101, first_claim, 'fixture/project', 7, repeat('a', 40)) THEN
        RAISE EXCEPTION 'A reclaimed worker must not record a draft';
    END IF;
    IF NOT record_ready_draft(101, next_claim, 'fixture/project', 7, repeat('b', 40)) THEN
        RAISE EXCEPTION 'The current worker must be able to record a draft';
    END IF;
    IF claim_ready_dispatch(101) IS NOT NULL THEN
        RAISE EXCEPTION 'A duplicate event must not rerun a ticket with a draft';
    END IF;
    IF NOT EXISTS (
        SELECT FROM ready_dispatches
        WHERE ticket_id = 101 AND status = 'waiting_merge' AND repo = 'fixture/project'
          AND pr_number = 7 AND head_sha = repeat('b', 40)
    ) THEN
        RAISE EXCEPTION 'The draft must carry its exact head to the merge gate';
    END IF;
END;
$$;

ROLLBACK;
