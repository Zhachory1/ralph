import assert from "node:assert/strict";
import test from "node:test";
import { runOutboundSend } from "../comms-outbound-worker.mjs";

const issueId = "11111111-1111-4111-8111-111111111111";
const approverUserId = "director-user";

function envelope(overrides = {}) {
  return {
    version: 1,
    intentId: "chat-operation:reply:run-1",
    provider: "google_chat",
    accountId: "chat-account",
    recipient: "users/12345",
    space: "spaces/example",
    thread: "spaces/example/threads/one",
    message: "First part\n\nSecond part",
    parts: [
      { index: 1, text: "First part", requestId: "request-1", messageId: "client-1" },
      { index: 2, text: "Second part", requestId: "request-2", messageId: "client-2" },
    ],
    ...overrides,
  };
}

class FakeBoard {
  constructor({ resolution = "accepted", resolver = approverUserId, mismatchTarget = false } = {}) {
    this.documents = new Map();
    this.revisions = new Map();
    this.interactions = [];
    this.resolution = resolution;
    this.resolver = resolver;
    this.mismatchTarget = mismatchTarget;
    this.staleReceiptReadback = false;
    this.previousState = null;
    this.interactionReads = 0;
    this.mutateApprovalOnRead = null;
  }

  key(issue, key) { return `${issue}/${key}`; }

  async getDocument(issue, key) {
    const document = this.documents.get(this.key(issue, key));
    if (!document) return null;
    if (this.staleReceiptReadback && key.startsWith("chat-send-state-")
        && document.body.includes('"status":"receipt_recorded"')) {
      this.staleReceiptReadback = false;
      return this.previousState;
    }
    return structuredClone(document);
  }

  async putDocument(issue, key, body, baseRevisionId) {
    const docKey = this.key(issue, key);
    const existing = this.documents.get(docKey);
    if (existing && existing.latestRevisionId !== baseRevisionId) {
      throw Object.assign(new Error("stale"), { status: 409 });
    }
    if (!existing && baseRevisionId !== null) throw new Error("unexpected base revision");
    const revisionNumber = (existing?.latestRevisionNumber ?? 0) + 1;
    const latestRevisionId = `revision-${revisionNumber}-${key}`;
    if (existing) {
      const priorRevision = this.revisions.get(`${docKey}/${existing.latestRevisionId}`);
      if (priorRevision) priorRevision.isLatest = false;
    }
    const document = {
      id: existing?.id ?? `document-${key}`,
      issueId: issue,
      key,
      body,
      latestRevisionId,
      latestRevisionNumber: revisionNumber,
      lockedAt: existing?.lockedAt ?? null,
    };
    if (existing && body.includes('"status":"receipt_recorded"')) this.previousState = structuredClone(existing);
    this.documents.set(docKey, document);
    this.revisions.set(`${docKey}/${latestRevisionId}`, {
      ...structuredClone(document), id: latestRevisionId, isLatest: true,
    });
    return structuredClone(document);
  }

  async lockDocument(issue, key) {
    const doc = this.documents.get(this.key(issue, key));
    doc.lockedAt = "locked";
    return { document: structuredClone(doc) };
  }

  async getRevision(issue, key, revisionId) {
    return structuredClone(this.revisions.get(`${this.key(issue, key)}/${revisionId}`) ?? null);
  }

  async listInteractions() {
    this.interactionReads += 1;
    if (this.interactionReads === this.mutateApprovalOnRead && this.interactions[0]) {
      this.interactions[0].payload.target.revisionId = "changed-revision";
    }
    return structuredClone(this.interactions);
  }

  async createInteraction(issue, body) {
    const existing = this.interactions.find((row) => row.idempotencyKey === body.idempotencyKey);
    if (existing) return structuredClone(existing);
    const payload = structuredClone(body.payload);
    if (this.mismatchTarget) payload.target.revisionId = "wrong-revision";
    const interaction = {
      ...structuredClone(body),
      id: `interaction-${this.interactions.length + 1}`,
      status: this.resolution,
      resolvedByUserId: this.resolver,
      requestedResolverPolicy: "human_only",
      effectiveResolverPolicy: "human_only",
      addresseeUserId: body.addresseeUserId,
      payload,
    };
    this.interactions.push(interaction);
    return structuredClone(interaction);
  }
}

function config() {
  return { issueId, approverUserId, accountId: "chat-account" };
}

function sendReceipt(envelope, part) {
  return { status: "sent", name: `messages/${part.index}`, createTime: "2026-11-22T00:00:00Z" };
}

test("declined confirmation never dispatches", async () => {
  const board = new FakeBoard({ resolution: "rejected" });
  let sends = 0;
  const result = await runOutboundSend(envelope(), config(), board, async () => { sends += 1; });
  assert.equal(result.status, "declined");
  assert.equal(sends, 0);
  const stateDoc = [...board.documents.values()].find((row) => row.key.startsWith("chat-send-state-"));
  assert.equal(JSON.parse(stateDoc.body).status, "declined");
});

test("a changed envelope cannot reuse its old confirmation and a new intent gets a fresh card", async () => {
  const board = new FakeBoard({ resolution: "pending" });
  const first = envelope();
  assert.equal((await runOutboundSend(first, config(), board, sendReceipt)).status, "awaiting_confirmation");
  const changedMessage = envelope({ message: "Changed message", parts: [
    { index: 1, text: "Changed message", requestId: "request-1", messageId: "client-1" },
  ] });
  const changedRecipient = envelope({ recipient: "users/67890" });
  const changedSpace = envelope({ space: "spaces/other", thread: "spaces/other/threads/one" });
  const changedThread = envelope({ thread: "spaces/example/threads/other" });
  const reordered = envelope({ parts: [
    { index: 1, text: "Second part", requestId: "request-2", messageId: "client-2" },
    { index: 2, text: "First part", requestId: "request-1", messageId: "client-1" },
  ] });
  for (const changed of [changedMessage, changedRecipient, changedSpace, changedThread, reordered]) {
    assert.equal((await runOutboundSend(changed, config(), board, sendReceipt)).status, "changed_envelope");
  }
  assert.equal(board.interactions.length, 1);
  const freshIntent = await runOutboundSend({ ...reordered, intentId: "chat-operation:reply:run-2" },
    config(), board, sendReceipt);
  assert.equal(freshIntent.status, "awaiting_confirmation");
  assert.equal(board.interactions.length, 2);
});

test("replay and concurrent accepted attempts claim once and dispatch the ordered parts once", async () => {
  const board = new FakeBoard();
  const approved = envelope();
  let sends = 0;
  const send = async (message, part) => { sends += 1; return sendReceipt(message, part); };
  const results = await Promise.all([
    runOutboundSend(approved, config(), board, send),
    runOutboundSend(approved, config(), board, send),
  ]);
  assert.equal(sends, 2);
  assert.ok(results.some((result) => result.status === "delivered"));
  assert.ok(results.some((result) => result.status === "held_for_reconciliation"));
  assert.equal(board.interactions.length, 1);
  const stateDocument = [...board.documents.values()].find((row) => row.key.startsWith("chat-send-state-"));
  const stored = JSON.parse((await board.getDocument(issueId, stateDocument.key)).body);
  assert.deepEqual(stored.parts.map((part) => part.status), ["delivered", "delivered"]);
  assert.deepEqual(stored.parts.map((part) => part.receipt.name), ["messages/1", "messages/2"]);
  assert.deepEqual(stored.parts.map((part) => part.receipt.createTime), [
    "2026-11-22T00:00:00Z", "2026-11-22T00:00:00Z",
  ]);
  assert.equal((await runOutboundSend(approved, config(), board, send)).status, "delivered");
  assert.equal(sends, 2);
});

test("the accepted interaction and locked revision are re-read after claim and before each part", async () => {
  const board = new FakeBoard();
  const result = await runOutboundSend(envelope(), config(), board, sendReceipt);
  assert.equal(result.status, "delivered");
  assert.equal(board.interactionReads, 3);

  const changedApproval = new FakeBoard();
  changedApproval.mutateApprovalOnRead = 2;
  let sends = 0;
  const rejected = await runOutboundSend(envelope(), config(), changedApproval, async () => { sends += 1; });
  assert.equal(rejected.status, "approval_mismatch");
  assert.equal(sends, 0);
  const claim = [...changedApproval.documents.values()].find((row) => row.key.startsWith("chat-send-state-"));
  assert.equal(JSON.parse(claim.body).status, "approval_mismatch");
});

test("an accepted interaction with a different target or resolver cannot dispatch", async (t) => {
  for (const board of [new FakeBoard({ mismatchTarget: true }), new FakeBoard({ resolver: "someone-else" })]) {
    await t.test(board.mismatchTarget ? "target mismatch" : "resolver mismatch", async () => {
      let sends = 0;
      const result = await runOutboundSend(envelope(), config(), board, async () => { sends += 1; });
      assert.equal(result.status, "approval_mismatch");
      assert.equal(sends, 0);
    });
  }
});

test("a definite provider failure is recorded terminally and is not retried", async () => {
  const board = new FakeBoard();
  let sends = 0;
  const fail = async () => { sends += 1; return { status: "definite_failure", code: "invalid_request" }; };
  assert.equal((await runOutboundSend(envelope(), config(), board, fail)).status, "failed");
  assert.equal((await runOutboundSend(envelope(), config(), board, sendReceipt)).status, "failed");
  assert.equal(sends, 1);
});

test("ambiguous provider timeout is held and never blindly retried", async () => {
  const board = new FakeBoard();
  let sends = 0;
  const timeout = async () => { sends += 1; return { status: "ambiguous" }; };
  assert.equal((await runOutboundSend(envelope(), config(), board, timeout)).status, "held_for_reconciliation");
  assert.equal((await runOutboundSend(envelope(), config(), board, sendReceipt)).status, "held_for_reconciliation");
  assert.equal(sends, 1);
});

test("receipt is read back before delivery; failed read-back resumes without a provider retry", async () => {
  const board = new FakeBoard();
  board.staleReceiptReadback = true;
  let sends = 0;
  const send = async (message, part) => { sends += 1; return sendReceipt(message, part); };
  assert.equal((await runOutboundSend(envelope(), config(), board, send)).status, "receipt_readback_failed");
  const stateKey = [...board.documents.values()].find((row) => row.key.startsWith("chat-send-state-")).key;
  const recorded = JSON.parse((await board.getDocument(issueId, stateKey)).body);
  assert.equal(recorded.status, "receipt_recorded");
  assert.deepEqual(recorded.parts[0].receipt, {
    name: "messages/1",
    createTime: "2026-11-22T00:00:00Z",
    requestId: "request-1",
    messageId: "client-1",
  });
  assert.equal((await runOutboundSend(envelope(), config(), board, send)).status, "delivered");
  assert.equal(sends, 2);
  assert.equal(JSON.parse((await board.getDocument(issueId, stateKey)).body).parts[0].status, "delivered");
  assert.equal(JSON.parse((await board.getDocument(issueId, stateKey)).body).parts[1].status, "delivered");
});
