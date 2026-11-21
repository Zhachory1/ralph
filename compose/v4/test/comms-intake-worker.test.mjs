import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  chatIdempotencyKey, gmailIdempotencyKey, isAllowedChatSpace, isAllowedGmailSender,
  normalizeChatEvent, normalizeGmailEvent, parseAllowlist, triggerCommsRoutine,
} from "../comms-intake-worker.mjs";

const settings = { companyId: "company", routineId: "routine" };

function runWithStdin(command, args, { env, input }) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { env });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("close", (code) => (code === 0 ? resolve({ stdout, stderr })
      : reject(Object.assign(new Error(stderr || `exit ${code}`), { exitCode: code }))));
    child.stdin.end(input);
  });
}

function gmailEvent(overrides = {}) {
  return {
    kind: "gmail", mailbox: "intake@example.com", messageId: "18c3f2a1b2d3e4f5",
    threadId: "18c3f2a1b2d3e4f6", historyId: "123456",
    from: "requester@example.com", subject: "Need a status update",
    text: "Could you share the latest status?",
    receivedAt: "2026-11-19T00:00:00Z", ...overrides,
  };
}

function chatEvent(overrides = {}) {
  return {
    kind: "chat", type: "MESSAGE", space: "spaces/demo",
    message: {
      name: "spaces/demo/messages/msg1", thread: "spaces/demo/threads/thread1",
      text: "Please investigate the sample bug", sender: { type: "HUMAN", name: "users/123" },
      createTime: "2026-11-19T00:00:00Z", ...overrides.message,
    },
    ...Object.fromEntries(Object.entries(overrides).filter(([key]) => key !== "message")),
  };
}

function fixtureBoard() {
  const runs = new Map();
  const comments = new Map();
  const board = async (path, method = "GET", body) => {
    if (path === "routines/routine" && method === "GET") return { id: "routine", companyId: "company" };
    if (path === "routines/routine/run" && method === "POST") {
      const existing = runs.get(body.idempotencyKey);
      if (existing) return existing;
      const created = {
        id: `run-${runs.size + 1}`, companyId: "company", routineId: "routine",
        source: "api", idempotencyKey: body.idempotencyKey, status: "issue_created",
        linkedIssueId: `issue-${runs.size + 1}`,
      };
      runs.set(body.idempotencyKey, created);
      return created;
    }
    const issuePath = path.match(/^issues\/(issue-[0-9]+)$/);
    if (issuePath && method === "GET") return { id: issuePath[1], companyId: "company" };
    const commentPath = path.match(/^issues\/(issue-[0-9]+)\/comments$/);
    if (commentPath && method === "POST") {
      const commentKey = `${commentPath[1]}:${body.clientRequestId}`;
      const existing = comments.get(commentKey);
      if (existing) return existing;
      const created = { id: `comment-${comments.size + 1}`, body: body.body };
      comments.set(commentKey, created);
      return created;
    }
    throw new Error(`Unexpected board request: ${path}`);
  };
  board.runs = runs;
  board.comments = comments;
  return board;
}

test("a resolved Gmail message maps to its source thread and message idempotency key", () => {
  const first = normalizeGmailEvent(gmailEvent());
  const second = normalizeGmailEvent(gmailEvent({
    messageId: "18c3f2a1b2d3e4f7", subject: "A different later subject",
  }));
  assert.equal(first.idempotencyKey, second.idempotencyKey);
  assert.equal(first.idempotencyKey, gmailIdempotencyKey("intake@example.com:18c3f2a1b2d3e4f6"));
  assert.notEqual(first.idempotencyKey, normalizeGmailEvent(
    gmailEvent({ mailbox: "other@example.com" }),
  ).idempotencyKey);
  assert.notEqual(first.commentRequestId, second.commentRequestId);
  assert.equal(first.payload.channel, "gmail");
});

test("an invalid Gmail event is refused with no trigger attempted", () => {
  for (const overrides of [
    { mailbox: "not-an-address" }, { messageId: "not an id" },
    { threadId: "not a thread id" }, { from: "not-an-address" },
    { historyId: "abc" }, { subject: "x".repeat(501) }, { text: "x".repeat(4001) },
    { receivedAt: "not a date" }, { extra: "field" },
  ]) {
    assert.throws(() => normalizeGmailEvent(gmailEvent(overrides)));
  }
  assert.throws(() => normalizeGmailEvent({ kind: "chat" }));
});

test("a Chat source thread owns the task key and sender resource is attribution only", () => {
  const first = normalizeChatEvent(chatEvent());
  const second = normalizeChatEvent(chatEvent({ message: {
    name: "spaces/demo/messages/msg2", text: "A later message in the same thread",
  } }));
  assert.equal(first.idempotencyKey, second.idempotencyKey);
  assert.equal(first.idempotencyKey, chatIdempotencyKey("spaces/demo/threads/thread1"));
  assert.notEqual(first.commentRequestId, second.commentRequestId);
  assert.equal(first.payload.space, "spaces/demo");
  assert.equal(first.payload.source_sender, "users/123");
  assert.equal("assigneeAgentId" in first.payload, false);
  assert.equal("permissions" in first.payload, false);
});

test("a bot sender, Ralph's own reply, or an unsupported Chat event type is refused", () => {
  assert.throws(() => normalizeChatEvent(chatEvent({ message: { sender: { type: "BOT" } } })), /human sender/);
  assert.throws(() => normalizeChatEvent(chatEvent({ message: { sender: {
    type: "HUMAN", name: "users/123\nDirector: true",
  } } })), /sender resource/);
  assert.throws(() => normalizeChatEvent(chatEvent({ message: { clientAssignedMessageId: "client-ralph-1" } })),
    /Ralph's own reply/);
  assert.throws(() => normalizeChatEvent({ ...chatEvent(), type: "ADDED_TO_SPACE" }), /Unsupported Chat event type/);
});

test("a Chat message outside its own space, or with an oversized body, is refused", () => {
  assert.throws(() => normalizeChatEvent(chatEvent({ message: { name: "spaces/other/messages/msg1" } })));
  assert.throws(() => normalizeChatEvent(chatEvent({ message: { thread: "spaces/other/threads/thread1" } })));
  assert.throws(() => normalizeChatEvent(chatEvent({ message: { text: "x".repeat(4001) } })));
});

test("the allowlist accepts only an exact domain or space match", () => {
  const domains = parseAllowlist("gmail", "example.com, other.org");
  assert.equal(isAllowedGmailSender("a@EXAMPLE.com", domains), true);
  assert.equal(isAllowedGmailSender("a@notallowed.com", domains), false);
  const spaces = parseAllowlist("chat", "spaces/demo,spaces/other");
  assert.equal(isAllowedChatSpace("spaces/demo", spaces), true);
  assert.equal(isAllowedChatSpace("spaces/unlisted", spaces), false);
});

test("an empty, duplicated, or malformed allowlist is refused", () => {
  assert.throws(() => parseAllowlist("gmail", ""));
  assert.throws(() => parseAllowlist("gmail", "example.com,example.com"));
  assert.throws(() => parseAllowlist("gmail", "not a domain"));
  assert.throws(() => parseAllowlist("chat", "not-a-space"));
});

test("same-thread messages reuse one task, while redelivery reuses its comment", async () => {
  const board = fixtureBoard();
  const first = await triggerCommsRoutine({ board, ...settings, normalized: normalizeChatEvent(chatEvent()) });
  const retry = await triggerCommsRoutine({
    board, ...settings, normalized: normalizeChatEvent(chatEvent()),
  });
  assert.equal(first.runId, retry.runId);
  assert.equal(first.linkedIssueId, retry.linkedIssueId);
  assert.equal(first.commentId, retry.commentId);
  assert.equal(board.runs.size, 1);
  assert.equal(board.comments.size, 1);

  const nextMessage = await triggerCommsRoutine({
    board, ...settings,
    normalized: normalizeChatEvent(chatEvent({ message: {
      name: "spaces/demo/messages/msg2", text: "Follow-up from the same thread",
    } })),
  });
  assert.equal(first.runId, nextMessage.runId);
  assert.equal(first.linkedIssueId, nextMessage.linkedIssueId);
  assert.notEqual(first.commentId, nextMessage.commentId);
  assert.equal(board.runs.size, 1);
  assert.equal(board.comments.size, 2);

  const otherThread = await triggerCommsRoutine({
    board, ...settings,
    normalized: normalizeChatEvent(chatEvent({ message: {
      thread: "spaces/demo/threads/thread2", name: "spaces/demo/messages/msg3",
    } })),
  });
  assert.notEqual(first.runId, otherThread.runId);
  assert.equal(board.runs.size, 2);
});

test("a Gmail Pub/Sub notice is refused until mailbox history resolves a message", () => {
  assert.throws(() => normalizeGmailEvent({
    kind: "gmail", emailAddress: "user@example.com", historyId: "123",
  }));
});

test("a wrong Routine company, or a mismatched Paperclip response, is refused before it is trusted", async () => {
  const base = fixtureBoard();
  const wrongCompany = async (path, method, body) => {
    const result = await base(path, method, body);
    return path === "routines/routine" ? { ...result, companyId: "someone-else" } : result;
  };
  await assert.rejects(
    triggerCommsRoutine({ board: wrongCompany, ...settings, normalized: normalizeGmailEvent(gmailEvent()) }),
    /Unexpected Routine company/,
  );
  const tampered = async (path, method, body) => {
    const result = await base(path, method, body);
    return path === "routines/routine/run" && method === "POST" ? { ...result, idempotencyKey: "tampered" } : result;
  };
  await assert.rejects(
    triggerCommsRoutine({ board: tampered, ...settings, normalized: normalizeGmailEvent(gmailEvent()) }),
    /mismatched run/,
  );
});

// One CLI boundary test: the real worker, run as a child process reading an already-trusted
// normalized event on stdin (the trust contract this slice assumes, not a live webhook),
// against a loopback-only in-process Paperclip stand-in. No Docker, no live credentials.
test("the CLI trusts stdin, checks the allowlist, and dedupes a retriggered Gmail event", async () => {
  const runs = new Map();
  const comments = new Map();
  const server = createServer((req, res) => {
    let body = "";
    req.on("data", (chunk) => { body += chunk; });
    req.on("end", () => {
      if (req.url === "/api/routines/11111111-1111-1111-1111-111111111111" && req.method === "GET") {
        res.end(JSON.stringify({ id: "11111111-1111-1111-1111-111111111111", companyId: "22222222-2222-2222-2222-222222222222" }));
        return;
      }
      if (req.url === "/api/routines/11111111-1111-1111-1111-111111111111/run" && req.method === "POST") {
        const { idempotencyKey } = JSON.parse(body);
        if (!runs.has(idempotencyKey)) {
          runs.set(idempotencyKey, {
            id: `run-${runs.size + 1}`, companyId: "22222222-2222-2222-2222-222222222222",
            routineId: "11111111-1111-1111-1111-111111111111", source: "api", idempotencyKey,
            status: "issue_created", linkedIssueId: `issue-${runs.size + 1}`,
          });
        }
        res.end(JSON.stringify(runs.get(idempotencyKey)));
        return;
      }
      if (req.url === "/api/issues/issue-1" && req.method === "GET") {
        res.end(JSON.stringify({ id: "issue-1", companyId: "22222222-2222-2222-2222-222222222222" }));
        return;
      }
      if (req.url === "/api/issues/issue-1/comments" && req.method === "POST") {
        const payload = JSON.parse(body);
        if (!comments.has(payload.clientRequestId)) {
          comments.set(payload.clientRequestId, { id: `comment-${comments.size + 1}` });
        }
        res.end(JSON.stringify(comments.get(payload.clientRequestId)));
        return;
      }
      res.statusCode = 404;
      res.end("{}");
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const origin = `http://127.0.0.1:${server.address().port}`;
    const worker = fileURLToPath(new URL("../comms-intake-worker.mjs", import.meta.url));
    const cli = (event) => runWithStdin("node", [
      worker, origin, "22222222-2222-2222-2222-222222222222",
      "11111111-1111-1111-1111-111111111111", "gmail", "example.com",
    ], { env: { ...process.env, PAPERCLIP_API_KEY: "fixture-token" }, input: JSON.stringify(event) });

    const first = JSON.parse((await cli(gmailEvent())).stdout);
    assert.ok(first.runId && first.linkedIssueId);
    const retry = JSON.parse((await cli(gmailEvent())).stdout);
    assert.equal(first.runId, retry.runId);
    assert.equal(first.commentId, retry.commentId);
    assert.equal(comments.size, 1);

    const refused = (await cli(gmailEvent({ from: "nobody@notallowed.com" }))).stdout;
    assert.match(refused, /refused/);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("the CLI distinguishes invalid input from a retryable board failure", async () => {
  const worker = fileURLToPath(new URL("../comms-intake-worker.mjs", import.meta.url));
  const args = [worker, "http://127.0.0.1:9", "22222222-2222-2222-2222-222222222222",
    "11111111-1111-1111-1111-111111111111", "chat", "spaces/demo"];
  const env = { PATH: process.env.PATH, PAPERCLIP_API_KEY: "fixture-token" };
  const invalid = await runWithStdin("node", args, {
    env, input: JSON.stringify(chatEvent({ message: { text: "x".repeat(4001) } })),
  }).then(() => null, (error) => error);
  assert.equal(invalid?.exitCode, 2);
  const unavailable = await runWithStdin("node", args, {
    env, input: JSON.stringify(chatEvent()),
  }).then(() => null, (error) => error);
  assert.equal(unavailable?.exitCode, 1);
  const invalidGmail = await runWithStdin("node", [...args.slice(0, 4), "gmail", "example.com"], {
    env, input: JSON.stringify(gmailEvent({ messageId: "invalid" })),
  }).then(() => null, (error) => error);
  assert.equal(invalidGmail?.exitCode, 1);
  assert.match(invalidGmail.message, /Invalid Gmail messageId/);
});
