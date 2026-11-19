# First offline slice: Gmail/Chat intake into Paperclip Routines (#266)

`compose/v4/comms-intake-worker.mjs` turns one normalized Gmail Pub/Sub
notification or Google Chat `MESSAGE` event into a native Paperclip Routine
trigger: `POST routines/{id}/run`, `source: "api"` (Paperclip's enum is
`"manual"|"api"`; the comms channel lives in `payload.channel`). Review-only
— no Gmail/Chat send call exists — and it does not decide what happens after
a run is created; that is the Routine's assigned agent, out of scope here.
Builds on #209; activation (a real webhook, real credentials, a routine
wired to act) follows #261.

**Trust contract.** A local CLI, invoked synchronously by a bridge that has
already authenticated the event with Google (a verified Pub/Sub push
subscription, or a verified Chat webhook bearer token) — neither exists in
this repo yet, and this is not a network listener; it claims no live
ingress. It adds no ingress auth of its own: a shared secret inside the
event payload would be neither Google's auth nor a real boundary for a call
that is, by construction, already local and trusted, and would only persist
a secret worth leaking. It enforces shape validation, a required per-source
allowlist (domains for Gmail, `spaces/...` for Chat), a Paperclip
company/routine match, and one idempotent trigger per source event.

```
PAPERCLIP_API_KEY=<scoped token> node compose/v4/comms-intake-worker.mjs \
  http://127.0.0.1:PORT COMPANY_ID ROUTINE_ID gmail|chat ALLOWLIST < normalized-event.json
```

`idempotencyKey` is a SHA-256 of the event's stable identifier alone (Gmail
`messageId` / Chat message `name`), so a redelivery reuses the same run and
linked issue — verified against a disposable, authenticated local Paperclip
instance, which is also what showed `source` is restricted to
`"manual"|"api"`.

Not proven here: the Gmail-side normalization step, a registered Chat
webhook, or any Routine actually assigned to act on a comms issue.

Verify: `node --test compose/v4/test/comms-intake-worker.test.mjs` — offline,
synthetic, no Docker, no live credentials, including one CLI boundary test
spawning the real worker over stdin against a loopback-only stand-in.
