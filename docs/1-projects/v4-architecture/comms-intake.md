# Gmail/Chat intake into Paperclip Routines (#266)

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
subscription, or a verified Chat webhook bearer token). This worker is not a
network listener; it claims no live
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

## Authenticated Chat bridge

`compose/v4/chat-ingress.py` is an opt-in, loopback-only HTTP bridge for a
non-add-on Google Chat app configured with an **HTTP endpoint URL** audience.
It uses the existing `google-auth` host dependency to verify Google's OIDC
signature and the configured audience, then requires the verified service
account `chat@system.gserviceaccount.com`. It forwards human `MESSAGE` events
to the existing intake worker, which enforces the space allowlist and Routine
idempotency. Bot events and the app's own replies are ignored. Invalid tokens
are rejected; verification or board outages return a retryable failure.

The Routine payload retains `message.sender.name` as `sender_name`. It accepts
`users/<id>` with 1–128 ASCII letters, digits, underscores or hyphens; it rejects
missing names, `users/app`, and email aliases. The OIDC token authenticates the Chat
service, not the human sender; the sender name is metadata, not a Paperclip identity
or Director approval. The HTTPS proxy and any direct worker caller remain trusted
to pass the event without alteration.

Set `PAPERCLIP_API_KEY` through the host's secret manager, not a command-line
argument. The key inherits its issuing account's authority: use an account
restricted to the intended company. With `google-auth` and `requests` installed:

```sh
python3 compose/v4/chat-ingress.py \
  --origin http://127.0.0.1:3100 --company-id COMPANY_UUID --routine-id ROUTINE_UUID \
  --spaces spaces/example --audience https://example.invalid/chat --port 18765
```

The bridge listens only on `127.0.0.1`; a separately authorized, rate-limited
HTTPS ingress must forward `/chat` without changing the configured audience or
logging tokens and message bodies. It must supply one `Content-Length` and no
chunked body; the bridge accepts at most 256 KiB. It never sends Chat messages. Offline tests
exercise Google's verifier with synthetic keys and the board path with a mocked
verifier; they do not prove a real Google connection or activate routing.

Not yet implemented: Gmail history resolution, a verified mapping of Chat senders
to authorized Paperclip users ([#304](https://github.com/Zhachory1/ralph/issues/304)),
Chat thread-to-issue continuity, a registered Chat webhook, or approved external
sends. Do not activate live Chat routing until the identity mapping and thread
continuity exist.

Verify: `node --test compose/v4/test/comms-intake-worker.test.mjs` — offline,
synthetic, no Docker, no live credentials, including one CLI boundary test
spawning the real worker over stdin against a loopback-only stand-in.
Run `python3 compose/v4/test/test-chat-ingress.py` for the Chat authentication
boundary and `python3 compose/v4/test/paperclip-chat-ingress-canary.py` with the
cached Paperclip image for the native Routine dispatch and replay check.
Run `python3 compose/v4/test/paperclip-chat-oidc-canary.py` with the same image
to check Google's library against synthetic signed tokens without network access.
