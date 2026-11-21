// Bridge authenticated Gmail and Google Chat messages into one native Paperclip
// Routine task per source thread, with idempotent comments for each message.
//
// Trust contract: a local CLI, invoked synchronously by a bridge that has already
// authenticated the event with Google (a verified Pub/Sub push subscription, or a
// verified Chat webhook bearer token) -- neither exists in this repo yet, and this
// is not a network listener. It adds no ingress auth of its own: a shared secret
// inside the event payload would be neither Google's auth nor a real boundary for a
// call that is, by construction, already local and trusted -- only a secret worth
// leaking. It enforces shape validation, a per-source allowlist, a Paperclip
// company/routine match, one idempotent Routine per source thread, and one
// idempotent comment per message. Source sender resources are attribution only.
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const ORIGIN = /^http:\/\/(?:127\.0\.0\.1|host\.docker\.internal):[0-9]+$/;
const UUID = /^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const DOMAIN = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/i;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const GMAIL_MESSAGE_ID = /^[0-9a-f]{8,40}$/i;
const GMAIL_HISTORY_ID = /^[0-9]{1,20}$/;
const SPACE = /^spaces\/[A-Za-z0-9_-]+$/;
const MESSAGE_NAME = /^spaces\/[A-Za-z0-9_-]+\/messages\/[A-Za-z0-9_.-]+$/;
const THREAD_NAME = /^spaces\/[A-Za-z0-9_-]+\/threads\/[A-Za-z0-9_.-]+$/;
const MAX_ALLOWLIST = 16;
const MAX_TEXT = 4000; // chat-route's own cap on untrusted body text
const MAX_SUBJECT = 500;
const COMMENT_UUID_NAMESPACE = Buffer.from("6ba7b8129dad11d180b400c04fd430c8", "hex");

function sha256Hex(value) {
  return createHash("sha256").update(value).digest("hex");
}

export function gmailIdempotencyKey(messageId) {
  return "gmail-thread-" + sha256Hex(messageId);
}

export function chatIdempotencyKey(threadName) {
  return "chat-thread-" + sha256Hex(threadName);
}

function commentClientRequestId(source, messageId) {
  const name = Buffer.from(`urn:ralph:comms:comment:${source}:${messageId}`);
  const bytes = createHash("sha1").update(Buffer.concat([COMMENT_UUID_NAMESPACE, name])).digest().subarray(0, 16);
  bytes[6] = (bytes[6] & 0x0f) | 0x50;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function isoOrNull(value) {
  if (typeof value !== "string" || !value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : value;
}

// Gmail events are actual messages returned by the authenticated mailbox API,
// not Pub/Sub notifications or history records.
export function normalizeGmailEvent(raw) {
  if (!raw || typeof raw !== "object" || raw.kind !== "gmail") {
    throw new Error("Expected a normalized Gmail event");
  }
  const allowed = new Set([
    "kind", "mailbox", "messageId", "threadId", "historyId", "from", "subject", "text", "receivedAt",
  ]);
  if (Object.keys(raw).some((key) => !allowed.has(key))) {
    throw new Error("Unexpected field on a Gmail event");
  }
  const { mailbox, messageId, threadId, historyId, from, subject, text, receivedAt } = raw;
  if (typeof mailbox !== "string" || !EMAIL.test(mailbox)) {
    throw new Error("Invalid Gmail mailbox");
  }
  if (typeof messageId !== "string" || !GMAIL_MESSAGE_ID.test(messageId)) {
    throw new Error("Invalid Gmail messageId");
  }
  if (typeof threadId !== "string" || !GMAIL_MESSAGE_ID.test(threadId)) {
    throw new Error("Invalid Gmail threadId");
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
  if (typeof text !== "string" || text.length > MAX_TEXT) {
    throw new Error("Invalid Gmail message text");
  }
  if (receivedAt !== undefined && isoOrNull(receivedAt) === null) {
    throw new Error("Invalid Gmail receivedAt");
  }
  return {
    source: "gmail",
    idempotencyKey: gmailIdempotencyKey(`${mailbox.toLowerCase()}:${threadId}`),
    commentRequestId: commentClientRequestId("gmail", messageId),
    payload: {
      channel: "gmail",
      mailbox,
      message_id: messageId,
      thread_id: threadId,
      history_id: historyId,
      from,
      subject,
      text,
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
  if (sender.name !== undefined && (typeof sender.name !== "string"
      || !/^users\/[A-Za-z0-9._@+-]{1,240}$/.test(sender.name))) {
    throw new Error("Invalid Chat sender resource");
  }
  if (typeof clientAssignedMessageId === "string" && clientAssignedMessageId.startsWith("client-ralph-")) {
    throw new Error("Chat message originated from Ralph's own reply");
  }
  if (createTime !== undefined && isoOrNull(createTime) === null) {
    throw new Error("Invalid Chat createTime");
  }
  const threadName = thread ?? name;
  return {
    source: "chat",
    idempotencyKey: chatIdempotencyKey(threadName),
    commentRequestId: commentClientRequestId("chat", name),
    payload: {
      channel: "chat",
      space,
      thread: threadName,
      message_name: name,
      text,
      sender_type: sender.type,
      source_sender: sender.name ?? null,
    },
  };
}

export function commentBody(normalized) {
  const { source, payload } = normalized;
  if (source === "chat") {
    return [
      `Google Chat · ${payload.space} · ${payload.thread}`,
      `Sender resource (source attribution only): ${payload.source_sender ?? "not provided"}`,
      "",
      payload.text,
    ].join("\n");
  }
  return [
    `Gmail · thread ${payload.thread_id}`,
    `From: ${payload.from}`,
    `Subject: ${payload.subject.replace(/[\r\n]+/g, " ")}`,
    "",
    payload.text,
  ].join("\n");
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

// The native Routine idempotency key maps every message in a source thread to one
// task. Paperclip's comment clientRequestId deduplicates each message on replay.
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
  if (typeof run.linkedIssueId !== "string") {
    throw new Error("Paperclip Routine run has no linked task");
  }
  const issue = await board(`issues/${run.linkedIssueId}`);
  if (issue.id !== run.linkedIssueId || issue.companyId !== companyId) {
    throw new Error("Unexpected linked task company");
  }
  const comment = await board(`issues/${run.linkedIssueId}/comments`, "POST", {
    body: commentBody(normalized),
    clientRequestId: normalized.commentRequestId,
  });
  if (comment.clientRequestId !== normalized.commentRequestId || typeof comment.id !== "string") {
    throw new Error("Paperclip returned a mismatched comment for this message");
  }
  return {
    runId: run.id,
    linkedIssueId: run.linkedIssueId,
    commentId: comment.id,
    status: run.status,
    source,
    idempotencyKey,
    commentRequestId: normalized.commentRequestId,
  };
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
