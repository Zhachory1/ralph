# Gmail and Chat intake operations

This is the as-built operator reference for the local receive path. It does not
activate an external integration.

`comms-intake-worker.mjs` creates one native Paperclip Routine run per source
thread using `idempotencyKey`, then adds each resolved message as a comment
using `clientRequestId`. Replaying a message reuses its comment; follow-up
messages in that thread get new comments on the same task. Distinct threads get
distinct tasks. A threadless Chat message uses its message resource as its
one-message thread.

## Google Chat

`chat-ingress.py` verifies Google's OIDC signature, audience and Chat
service-account identity before dispatch. The sender resource is retained only
as message source attribution; it is never converted to a Paperclip identity,
permission, or Director authority. Bot events and Ralph's own messages are
ignored.

The `chat-intake` Compose profile has its own scoped Paperclip key and does not
mount `chat-creds`, which remains the sender's separate volume. Host-run mode
binds the bridge to loopback. The Compose service binds inside its own network
and publishes only on host loopback. A public route, real Chat space, or live
webhook requires separate authorization.

## Gmail

`gmail-ingress.py` is the bounded Pub/Sub push receiver. It verifies Google's
OIDC signature, issuer, configured audience and configured push service-account
identity; checks the exact subscription resource, bounded Pub/Sub envelope,
decoded notification and configured mailbox; then synchronously invokes the
existing `gmail-history-worker.py`. The worker independently verifies that
Gmail's authenticated `users.getProfile(userId="me")` mailbox matches
`GMAIL_MAILBOX`, then lists mailbox history using a separate `gmail.readonly`
OAuth token. It resolves each `messagesAdded` ID with `messages.get` and only
passes messages carrying the `INBOX` label and neither `SENT` nor `DRAFT` to the
shared worker. Notification text is never treated as message content. The token
is rejected unless its only scope is `gmail.readonly`; Gmail credentials are
not shared with Chat or any sender.

The worker holds an exclusive lock in `gmail-intake-state` across cursor read,
history pagination, per-message delivery and commit. Cursor replacement is
atomic, and both the new file and its parent directory are fsynced before the
receiver acknowledges the push. A mailbox mismatch, missing message labels,
partial/ambiguous Paperclip response, history failure or cursor commit failure
leaves the cursor unadvanced and yields a non-2xx response. Replays are safe
through Paperclip's native per-thread task key and stable per-message comment
`clientRequestId`. A 404/expired cursor is reported without resetting it;
initial cursor setup and expired-history recovery remain operator actions.

The `gmail-receive` Compose profile is opt-in and serves `/gmail` on the
loopback-only host port `RALPH_GMAIL_INGRESS_PORT`. Set `GMAIL_PUBSUB_AUDIENCE`,
`GMAIL_PUBSUB_SERVICE_ACCOUNT`, `GMAIL_PUBSUB_SUBSCRIPTION`, and `GMAIL_MAILBOX`
to the exact values selected during separate deployment authorization. No
public listener, HTTPS forwarding route, live subscription or Google account
is configured by this repository. Any external HTTPS-to-loopback forwarding
is operator-managed and must preserve the configured audience and OIDC token.

Successful worker completion returns HTTP 204; invalid authentication or
envelopes are rejected, and unresolved delivery or persistence failures return
HTTP 503 so Pub/Sub can retry. Configure a bounded Pub/Sub delivery-attempt
policy and dead-letter topic during deployment, including for expired history
and permanent configuration failures. Inspect the dead-letter path and recover
the Gmail cursor through the documented operator procedure; do not reset the
cursor automatically or treat a failed request as acknowledged.

## Browser admin page

The `comms-admin` Compose profile serves a read-only status page on
`http://127.0.0.1:8789`. It displays configured mailbox, sender domains,
Chat spaces and Routine ID, but not credentials. It cannot connect an account,
change routes, activate webhooks, or send messages. It is independent of the
receive profiles.

## Compose settings

The receive and admin profiles are opt-in and absent from the default
`COMPOSE_PROFILES`. See `compose/.env.example` for the separate receive key,
Paperclip origin, company and Routine IDs, Chat space allowlist, Gmail mailbox,
sender-domain allowlist and Pub/Sub identity values. Gmail's read-only token
lives in the dedicated `gmail-intake-creds` volume; the cursor and lock live
separately in `gmail-intake-state`. Do not populate these or start a receiver
without the specific authorization for its account, subscription and route.

## Offline verification

```sh
node --test compose/v4/test/comms-intake-worker.test.mjs
python3 compose/v4/test/test-chat-ingress.py
python3 compose/v4/test/test-gmail-ingress.py
python3 compose/v4/test/test-gmail-history-worker.py
python3 compose/v4/test/test-comms-admin.py
```

The tests use synthetic messages and in-process API shapes. They verify sender
attribution, thread mapping, redelivery, OIDC receiver checks, subscription and
mailbox validation, message resolution, OAuth mailbox identity and read-only
scope, inbound label filtering, serialized cursor safety, and receive/send
separation. They do not connect an account, access a mailbox, register a
webhook, send a message, or start containers.
