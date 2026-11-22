// Bridge a normalized Gmail Pub/Sub notification or Google Chat event into a native
// Paperclip Routine trigger (POST routines/{id}/run, source: "api"). Review-only: no
// Gmail/Chat send call exists in this file. First offline slice of #266.
//
// Trust contract: a local CLI, invoked synchronously by a bridge that has already
// authenticated the event with Google (a verified Pub/Sub push subscription, or a
// verified Chat webhook bearer token). This worker is not a network listener.
// It adds no ingress auth of its own: a shared secret
// inside the event payload would be neither Google's auth nor a real boundary for a
// call that is, by construction, already local and trusted -- only a secret worth
// leaking. It enforces shape validation, a per-source allowlist, a Paperclip
// company/routine match, and one idempotent trigger per source event, keyed only
// on the event's stable identifier (Gmail messageId / Chat message name).
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const ORIGIN = /^http:\/\/127\.0\.0\.1:[0-9]+$/;
const UUID = /^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const DOMAIN = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/i;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const GMAIL_MESSAGE_ID = /^[0-9a-f]{8,40}$/i;
const GMAIL_HISTORY_ID = /^[0-9]{1,20}$/;
const SPACE = /^spaces\/[A-Za-z0-9_-]+$/;
const MESSAGE_NAME = /^spaces\/[A-Za-z0-9_-]+\/messages\/[A-Za-z0-9_.-]+$/;
const THREAD_NAME = /^spaces\/[A-Za-z0-9_-]+\/threads\/[A-Za-z0-9_.-]+$/;
const USER_NAME = /^users\/[A-Za-z0-9_-]{1,128}$/;
const MAX_ALLOWLIST = 16;
const MAX_TEXT = 4000; // chat-route's own cap on untrusted body text
const MAX_SUBJECT = 500;

function sha256Hex(value) {
  return createHash("sha256").update(value).digest("hex");
}

export function gmailIdempotencyKey(messageId) {
  return "gmail-" + sha256Hex(messageId);
}

export function chatIdempotencyKey(messageName) {
  return "chat-" + sha256Hex(messageName);
}

function isoOrNull(value) {
  if (typeof value !== "string" || !value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : value;
}

// The input is a normalized Gmail notification (already resolved past the raw
// Pub/Sub envelope by an upstream, authenticated step out of this slice's scope).
export function normalizeGmailEvent(raw) {
  if (!raw || typeof raw !== "object" || raw.kind !== "gmail") {
    throw new Error("Expected a normalized Gmail event");
  }
  const allowed = new Set(["kind", "messageId", "historyId", "from", "subject", "receivedAt"]);
  if (Object.keys(raw).some((key) => !allowed.has(key))) {
    throw new Error("Unexpected field on a Gmail event");
  }
  const { messageId, historyId, from, subject, receivedAt } = raw;
  if (typeof messageId !== "string" || !GMAIL_MESSAGE_ID.test(messageId)) {
    throw new Error("Invalid Gmail messageId");
  }
  if (typeof historyId !== "string" || !GMAIL_HISTORY_ID.test(historyId)) {
    throw new Error("Invalid Gmail historyId");
  }
  if (typeof from !== "string" || !EMAIL.test(from)) {
    throw new Error("Invalid Gmail sender address");
  }
  if (typeof subject !== "string" || subject.length > MAX_SUBJECT) {
    throw new Error("Invalid Gmail subject");
  }
  if (receivedAt !== undefined && isoOrNull(receivedAt) === null) {
    throw new Error("Invalid Gmail receivedAt");
  }
  return {
    source: "gmail",
    idempotencyKey: gmailIdempotencyKey(messageId),
    payload: {
      channel: "gmail",
      message_id: messageId,
      history_id: historyId,
      from,
      subject,
      received_at: receivedAt ?? null,
    },
  };
}

// The input is a Google Chat MESSAGE event, already past this slice's assumed
// upstream webhook-token verification. Only type "MESSAGE" is actionable here;
// every other event type (ADDED_TO_SPACE, REMOVED_FROM_SPACE, ...) is refused, not
// silently ignored, so a caller notices an unsupported type rather than losing it.
export function normalizeChatEvent(raw) {
  if (!raw || typeof raw !== "object" || raw.kind !== "chat") {
    throw new Error("Expected a Chat event");
  }
  if (raw.type !== "MESSAGE") {
    throw new Error(`Unsupported Chat event type: ${raw.type}`);
  }
  const allowed = new Set(["kind", "type", "space", "message"]);
  if (Object.keys(raw).some((key) => !allowed.has(key))) {
    throw new Error("Unexpected field on a Chat event");
  }
  const { space, message } = raw;
  if (typeof space !== "string" || !SPACE.test(space)) {
    throw new Error("Invalid Chat space");
  }
  if (!message || typeof message !== "object") {
    throw new Error("Missing Chat message");
  }
  const { name, thread, text, sender, createTime, clientAssignedMessageId } = message;
  if (typeof name !== "string" || !MESSAGE_NAME.test(name) || !name.startsWith(`${space}/messages/`)) {
    throw new Error("Invalid Chat message name");
  }
  if (thread !== undefined && (typeof thread !== "string" || !THREAD_NAME.test(thread)
      || !thread.startsWith(`${space}/threads/`))) {
    throw new Error("Invalid Chat thread");
  }
  if (typeof text !== "string" || !text.trim() || text.length > MAX_TEXT) {
    throw new Error("Invalid Chat message text");
  }
  if (!sender || typeof sender !== "object" || sender.type !== "HUMAN") {
    throw new Error("Chat message is not from a human sender");
  }
  if (typeof sender.name !== "string" || sender.name === "users/app" || !USER_NAME.test(sender.name)) {
    throw new Error("Invalid Chat sender name");
  }
  if (typeof clientAssignedMessageId === "string" && clientAssignedMessageId.startsWith("client-ralph-")) {
    throw new Error("Chat message originated from Ralph's own reply");
  }
  if (createTime !== undefined && isoOrNull(createTime) === null) {
    throw new Error("Invalid Chat createTime");
  }
  return {
    source: "chat",
    idempotencyKey: chatIdempotencyKey(name),
    payload: {
      channel: "chat",
      space,
      thread: thread ?? null,
      message_name: name,
      text,
      sender_type: sender.type,
      sender_name: sender.name,
    },
  };
}

export function isAllowedGmailSender(address, allowedDomains) {
  const at = address.lastIndexOf("@");
  if (at < 0) return false;
  return allowedDomains.has(address.slice(at + 1).toLowerCase());
}

export function isAllowedChatSpace(space, allowedSpaces) {
  return allowedSpaces.has(space);
}

export function parseAllowlist(source, raw) {
  const items = (raw ?? "").split(",").map((item) => item.trim()).filter(Boolean);
  const unique = new Set(items);
  if (!items.length || unique.size !== items.length || items.length > MAX_ALLOWLIST) {
    throw new Error("Invalid comms intake allowlist");
  }
  const pattern = source === "gmail" ? DOMAIN : SPACE;
  for (const item of items) {
    if (!pattern.test(item)) throw new Error("Invalid comms intake allowlist entry");
  }
  return source === "gmail" ? new Set(items.map((item) => item.toLowerCase())) : unique;
}

// Creates (or, on a redelivered idempotencyKey, reuses) one Paperclip Routine run.
// Paperclip's own API restricts `source` to "manual"|"api"; "api" is this ingress's
// value, with the comms channel (gmail/chat) carried in payload.channel instead.
export async function triggerCommsRoutine({ board, companyId, routineId, normalized }) {
  const { source, idempotencyKey, payload } = normalized;
  const routine = await board(`routines/${routineId}`);
  if (routine.id !== routineId || routine.companyId !== companyId) {
    throw new Error("Unexpected Routine company");
  }
  const run = await board(`routines/${routineId}/run`, "POST", { source: "api", idempotencyKey, payload });
  if (run.companyId !== companyId || run.routineId !== routineId
      || run.source !== "api" || run.idempotencyKey !== idempotencyKey) {
    throw new Error("Paperclip returned a mismatched run for this trigger");
  }
  return { runId: run.id, linkedIssueId: run.linkedIssueId ?? null, status: run.status, source, idempotencyKey };
}

async function main() {
  const [origin, companyId, routineId, source, allowlistArg, ...extra] = process.argv.slice(2);
  if (!ORIGIN.test(origin) || ![companyId, routineId].every((id) => UUID.test(id))
      || !["gmail", "chat"].includes(source) || !allowlistArg || extra.length) {
    throw new Error(
      "Usage: comms-intake-worker.mjs BOARD_ORIGIN COMPANY_ID ROUTINE_ID gmail|chat ALLOWLIST < normalized-event.json");
  }
  const allowlist = parseAllowlist(source, allowlistArg);
  const input = readFileSync(0, "utf8");
  let normalized;
  let allowed;
  try {
    const event = JSON.parse(input);
    normalized = source === "gmail" ? normalizeGmailEvent(event) : normalizeChatEvent(event);
    allowed = source === "gmail"
      ? isAllowedGmailSender(normalized.payload.from, allowlist)
      : isAllowedChatSpace(normalized.payload.space, allowlist);
  } catch (error) {
    if (source !== "chat") throw error;
    console.error("Invalid comms intake input");
    process.exitCode = 2;
    return;
  }
  if (!allowed) {
    console.log(`refused: ${source} sender/space is not on the allowlist; no Routine triggered`);
    return;
  }

  const token = process.env.PAPERCLIP_API_KEY;
  delete process.env.PAPERCLIP_API_KEY;
  if (!token) throw new Error("Set a temporary scoped PAPERCLIP_API_KEY");
  const board = async (path, method = "GET", body) => {
    const response = await fetch(`${origin}/api/${path}`, {
      method,
      headers: { authorization: `Bearer ${token}`, origin, "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: "error",
      signal: AbortSignal.timeout(15_000),
    });
    if (!response.ok) throw new Error(`Paperclip ${method} failed (${response.status})`);
    return response.json();
  };
  const result = await triggerCommsRoutine({ board, companyId, routineId, normalized });
  console.log(JSON.stringify(result));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
