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

`gmail-history-worker.py` accepts a notification from an authenticated caller,
lists mailbox history using a separate `gmail.readonly` OAuth token, resolves
each `messagesAdded` ID with `messages.get`, and only then passes the actual
message into the shared worker. Notification text is never treated as message
content. The token is rejected unless its only scope is `gmail.readonly`; Gmail
credentials are not shared with Chat or any sender.

The worker saves the Gmail history API's high-water mark only after all history
pages have been traversed, every message resolved, and every Paperclip intake
accepted. Partial failure leaves the stored cursor unchanged, and replays are
safe through Paperclip's native task/comment idempotency. A 404/expired cursor
is reported without resetting it. Initial cursor setup and expired-history
recovery are operator actions.

The `gmail-receive` Compose profile stays idle until an authenticated caller
invokes it; no inbound HTTP listener is provided. After separately authorized
setup, a notification can be piped to the local worker as follows:

```sh
docker compose --profile gmail-receive exec -T gmail-intake \
  /opt/comms/bin/python /ralph/compose/v4/gmail-history-worker.py < notification.json
```

## Browser admin page

The `comms-admin` Compose profile serves a read-only status page on
`http://127.0.0.1:8789`. It displays configured mailbox, sender domains,
Chat spaces and Routine ID, but not credentials. It cannot connect an account,
change routes, activate webhooks, or send messages. It is independent of the
receive profiles.

## Compose settings

The receive and admin profiles are opt-in and absent from the default
`COMPOSE_PROFILES`. See `compose/.env.example` for the separate receive key,
Paperclip origin, company and Routine IDs, Chat space allowlist, Gmail mailbox
and sender-domain allowlist. Gmail's read-only token lives in the dedicated
`gmail-intake-creds` volume; the cursor lives separately in
`gmail-intake-state`. Do not populate these or start a receiver without the
specific authorization for its account, space and route.

## Offline verification

```sh
node --test compose/v4/test/comms-intake-worker.test.mjs
python3 compose/v4/test/test-chat-ingress.py
python3 compose/v4/test/test-gmail-history-worker.py
python3 compose/v4/test/test-comms-admin.py
```

The tests use synthetic messages and in-process API shapes. They verify sender
attribution, thread mapping, redelivery, message resolution, cursor safety,
read-only OAuth scope and receive/send separation. They do not connect an
account, access a mailbox, register a webhook, send a message, or start
containers.
