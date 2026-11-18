\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    claim UUID;
    merged_at TIMESTAMPTZ := '2026-11-18T01:00:00Z';
BEGIN
    claim := claim_ready_dispatch(102);
    PERFORM record_ready_draft(102, claim, 'fixture/project', 7, repeat('a', 40));
    IF record_ready_merge(102, 'fixture/project', 7, repeat('b', 40), repeat('c', 40), 'fixture-director', merged_at) THEN
        RAISE EXCEPTION 'A different PR head must not produce a merge receipt';
    END IF;
    IF record_ready_merge(102, 'fixture/other', 7, repeat('a', 40), repeat('c', 40), 'fixture-director', merged_at)
       OR record_ready_merge(102, 'fixture/project', 8, repeat('a', 40), repeat('c', 40), 'fixture-director', merged_at) THEN
        RAISE EXCEPTION 'A receipt must match both repository and PR number';
    END IF;
    IF mark_ready_board_done(102, repeat('c', 40)) THEN
        RAISE EXCEPTION 'Cannot mark Done before a matching merge receipt';
    END IF;
    IF NOT record_ready_merge(102, 'fixture/project', 7, repeat('a', 40), repeat('c', 40), 'fixture-director', merged_at) THEN
        RAISE EXCEPTION 'Exact pinned evidence must be accepted';
    END IF;
    IF NOT record_ready_merge(102, 'fixture/project', 7, repeat('a', 40), repeat('c', 40), 'fixture-director', merged_at) THEN
        RAISE EXCEPTION 'An identical receipt must be accepted on retry';
    END IF;
    IF record_ready_merge(102, 'fixture/project', 7, repeat('a', 40), repeat('d', 40), 'fixture-director', merged_at) THEN
        RAISE EXCEPTION 'A conflicting receipt must not replace the existing merge';
    END IF;
    IF NOT mark_ready_board_done(102, repeat('c', 40)) OR NOT mark_ready_board_done(102, repeat('c', 40)) THEN
        RAISE EXCEPTION 'The recorded merge can be marked Done idempotently';
    END IF;
END;
$$;

ROLLBACK;
