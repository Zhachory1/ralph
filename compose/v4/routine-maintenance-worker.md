# Outbound PR maintenance worker — ops notes

Code: [`routine-maintenance-worker.mjs`](routine-maintenance-worker.mjs). Tests:
[`test/routine-maintenance-worker.test.mjs`](test/routine-maintenance-worker.test.mjs) (offline,
pure decision logic), [`test/routine-maintenance-worker-cli.test.mjs`](test/routine-maintenance-worker-cli.test.mjs)
(one CLI boundary test against a loopback HTTP fixture), and
[`test/paperclip-maintenance-canary.py`](test/paperclip-maintenance-canary.py) (real board calls
against a disposable, network-isolated Paperclip container; no production writes, no live
scheduling). Implements #263's first bounded slice: detect, dedupe, wake, or escalate — never
rebase or merge.

## Link an issue to its outbound PR

Before any event for a PR can be processed, the Paperclip issue that tracks it must carry an
`outbound-pr` document recording exactly which repo and PR number it owns. This worker checks
this on every event (defense in depth); nothing upstream of this worker is assumed to be correct.

```bash
BODY=$(jq -nc --arg repo "$REPO" --argjson pr "$PR_NUMBER" '{repo:$repo,pr_number:$pr} | tostring')
curl -sS -b cookies.txt -X PUT "$ORIGIN/api/issues/$ISSUE_ID/documents/outbound-pr" \
  --json "{\"format\":\"markdown\",\"body\":$BODY}"
```

i.e. `PUT issues/{issueId}/documents/outbound-pr` with
`{"format": "markdown", "body": "{\"repo\":\"owner/repo\",\"pr_number\":123}"}`. There is no
reverse lookup (PR → issue): whatever creates the outbound PR in the first place is responsible
for writing this document on the same issue, once.

## Run it

One manually triggered Routine run in, one decision out. No polling, no scheduling.

```bash
PAPERCLIP_API_KEY=<scoped token, never logged> \
  node compose/v4/routine-maintenance-worker.mjs \
  http://127.0.0.1:<port> <companyId> <routineId> <runId> <bot-github-login> <directorUserId>
```

- `<companyId>`, `<routineId>`, `<runId>` are Paperclip UUIDs. `<runId>`'s Routine run must be
  `source: "manual"` with a `triggerPayload` of `{issueId, repo, pr_number, head_sha, event_type,
  event_id, author_login}` — the trusted, already-normalized GitHub event. Producing that
  normalized payload from a raw GitHub webhook or poll is not part of this slice.
- `<bot-github-login>` is this worker's own GitHub login, used only to ignore its own replies.
- `<directorUserId>` is the Director's Paperclip user id (from `auth/get-session`, **not** a
  UUID — verified against a live instance to be an opaque ~32-char auth-library token; this
  worker validates it as a safe-to-interpolate string, not as a UUID). Operator-configured, never
  auto-discovered by this worker.
- Exit code is nonzero on `effect_ambiguous` or `stale_state` (the two outcomes that need a human
  to look, not an automatic retry — see the file header comment for why there is no generalized
  recovery here) and on any thrown error (including a non-404 board read failure, which never
  resets the worker's retry state — it fails the run instead).

## Bounds

At most 2 wake attempts per issue (PDF Sec 6) before this worker escalates to the Director
instead of waking a third time. `base_drift` and `merge_ready` always escalate; they never wake.
`review_feedback` means feedback an upstream normalizer already classified as an ordinary,
allowed fix — ambiguous or substantive feedback must be classified upstream (as `base_drift`,
`merge_ready`, or a future distinct event type) before it ever reaches this worker.

## Concurrency: two separate requirements, not one

This worker needs **both** of the following; neither one alone is enough:

1. **Create the Routine with `concurrencyPolicy: "coalesce_if_active"`.** This only controls
   whether Paperclip starts a *new run* while one is already active on that Routine. It does not
   serialize this host worker process.
2. **Run `routine-maintenance-worker.mjs` serially** — one process at a time, never two
   invocations in flight for the same issue. Paperclip's coalescing cannot prevent an operator
   (or whatever eventually schedules this) from handing two already-created runs for the same
   issue to two concurrent worker processes; that is an operational rule this code does not
   enforce by itself.

The `baseRevisionId` compare-and-swap on the `maintenance` document is a narrower, in-process
guard against a write landing between one run's read and write if serial execution is ever
violated — it is a backstop, not a substitute for either requirement above.
