// Gate one exact Google Chat reply through a revision-bound native confirmation.
import { createHash, randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";

const MAX_BODY_BYTES = 100_000;
const MAX_PARTS = 5;
const MAX_PART_CHARACTERS = 4_000;
const uuid = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

function intentKey(prefix, intentId) {
  const digest = createHash("sha256").update(intentId).digest("hex");
  return `${prefix}${digest.slice(0, 64 - prefix.length)}`;
}

function documentKey(prefix, intentId) {
  return intentKey(prefix, intentId);
}

function canonical(value) {
  return JSON.stringify(value);
}

function parseDocument(document) {
  try {
    return JSON.parse(document.body);
  } catch {
    throw new Error("Paperclip send document is not valid JSON");
  }
}

function validateEnvelope(envelope) {
  if (!envelope || envelope.version !== 1 || typeof envelope.intentId !== "string"
      || !envelope.intentId || Buffer.byteLength(canonical(envelope)) > MAX_BODY_BYTES
      || envelope.provider !== "google_chat" || typeof envelope.accountId !== "string"
      || !envelope.accountId || !/^users\/[A-Za-z0-9._@+-]+$/.test(envelope.recipient ?? "")
      || !/^spaces\/[A-Za-z0-9_-]+$/.test(envelope.space ?? "")
      || typeof envelope.thread !== "string" || !envelope.thread.startsWith(`${envelope.space}/threads/`)
      || typeof envelope.message !== "string" || !envelope.message.trim()
      || !Array.isArray(envelope.parts) || envelope.parts.length < 1 || envelope.parts.length > MAX_PARTS
       || envelope.parts.some((part, index) => part?.index !== index + 1 || typeof part.text !== "string"
         || !part.text.trim() || Array.from(part.text).length > MAX_PART_CHARACTERS
         || typeof part.requestId !== "string" || typeof part.messageId !== "string")) {
    throw new Error("Invalid exact Chat send envelope");
  }
  return envelope;
}

function preview(envelope) {
  const serialized = JSON.stringify(envelope, null, 2);
  const longestFence = Math.max(2, ...[...serialized.matchAll(/`+/g)].map(([run]) => run.length));
  const fence = "`".repeat(longestFence + 1);
  return [
    "Approve this exact Google Chat send envelope. The ordered parts and provider IDs "
      + "below are the complete send request.",
    "",
    `${fence}json`,
    serialized,
    fence,
  ].join("\n");
}

function interactionTargetMatches(interaction, issueId, document, revisionId) {
  const target = interaction?.payload?.target;
  return target?.type === "issue_document" && target.issueId === issueId
    && target.documentId === document.id && target.key === document.key
    && target.revisionId === revisionId && target.revisionNumber === document.latestRevisionNumber;
}

function acceptedInteractionMatches(interaction, config, issueId, document) {
  const target = interaction?.payload?.target;
  const policy = interaction?.effectiveResolverPolicy ?? interaction?.requestedResolverPolicy
    ?? interaction?.resolverPolicy;
  return interaction?.status === "accepted" && interaction.resolvedByUserId === config.approverUserId
    && interaction.addresseeUserId === config.approverUserId && policy === "human_only"
    && target?.type === "issue_document" && target.issueId === issueId
    && target.documentId === document.id && target.key === document.key
    && target.revisionId === document.latestRevisionId
    && target.revisionNumber === document.latestRevisionNumber;
}

async function acceptedApprovalIsCurrent(deps, config, envelope, interaction, document, revisionId) {
  const currentInteraction = (await deps.listInteractions(config.issueId))
    .find((candidate) => candidate.id === interaction.id);
  if (!acceptedInteractionMatches(currentInteraction, config, config.issueId, document)
      || !interactionTargetMatches(currentInteraction, config.issueId, document, revisionId)) return false;

  const [revision, latest] = await Promise.all([
    deps.getRevision(config.issueId, document.key, revisionId),
    deps.getDocument(config.issueId, document.key),
  ]);
  const expectedBody = canonical(envelope);
  return Boolean(revision?.id === revisionId && revision.isLatest && revision.body === expectedBody
    && latest?.id === document.id && latest.key === document.key && latest.lockedAt
    && latest.latestRevisionId === revisionId
    && latest.latestRevisionNumber === document.latestRevisionNumber
    && latest.body === expectedBody);
}

function validateState(state, envelope, interaction, revisionId) {
  if (!state || state.version !== 1 || state.intentId !== envelope.intentId
      || state.interactionId !== interaction.id || state.approvedRevisionId !== revisionId
      || !Array.isArray(state.parts) || state.parts.length !== envelope.parts.length
      || state.parts.some((part, index) => part?.index !== envelope.parts[index].index
        || part.requestId !== envelope.parts[index].requestId
        || part.messageId !== envelope.parts[index].messageId)) {
    throw new Error("Paperclip send claim does not match its approved intent");
  }
  return state;
}

async function readState(deps, issueId, key) {
  const document = await deps.getDocument(issueId, key);
  return document ? { document, state: parseDocument(document) } : null;
}

async function writeState(deps, issueId, key, previous, state) {
  const body = canonical(state);
  try {
    return await deps.putDocument(issueId, key, body, previous?.latestRevisionId ?? null);
  } catch (error) {
    const current = await deps.getDocument(issueId, key).catch(() => null);
    if (current?.body === body) return current;
    throw error;
  }
}

async function claimIntent(deps, config, envelope, interaction, revisionId) {
  const key = documentKey("chat-send-state-", envelope.intentId);
  const prior = await readState(deps, config.issueId, key);
  if (prior) {
    const state = validateState(prior.state, envelope, interaction, revisionId);
    if (state.status === "delivered") return { status: "already_delivered", key, ...prior };
    if (state.status === "declined") return { status: "declined", key, ...prior };
    if (state.status === "failed") return { status: "failed", key, ...prior };
    if (state.status === "approval_mismatch") return { status: "approval_mismatch", key, ...prior };
    if (state.status === "ambiguous" || state.status === "dispatching") {
      return { status: "held_for_reconciliation", key, ...prior };
    }
    if (state.status === "receipt_recorded") return { status: "resume_receipt", key, ...prior };
    if (state.status === "ready_next_part") {
      const attemptId = randomUUID();
      state.status = "dispatching";
      state.dispatchAttemptId = attemptId;
      const document = await writeState(deps, config.issueId, key, prior.document, state);
      return { status: "claimed", key, document, state, attemptId };
    }
    throw new Error("Unexpected Paperclip send claim state");
  }

  const attemptId = randomUUID();
  const state = {
    version: 1,
    intentId: envelope.intentId,
    interactionId: interaction.id,
    approvedRevisionId: revisionId,
    status: "dispatching",
    dispatchAttemptId: attemptId,
    parts: envelope.parts.map((part) => ({
      index: part.index,
      requestId: part.requestId,
      messageId: part.messageId,
      status: "pending",
    })),
  };
  try {
    const document = await deps.putDocument(config.issueId, key, canonical(state), null);
    return { status: "claimed", key, document, state, attemptId };
  } catch (error) {
    const current = await readState(deps, config.issueId, key).catch(() => null);
    if (current?.document.body === canonical(state)) {
      return { status: "claimed", key, ...current, attemptId };
    }
    if (current) {
      validateState(current.state, envelope, interaction, revisionId);
      return { status: "held_for_reconciliation", key, ...current };
    }
    throw error;
  }
}

async function resumeRecordedReceipt(deps, issueId, claim) {
  const reread = await deps.getDocument(issueId, claim.key);
  if (!reread || reread.body !== claim.document.body
      || reread.latestRevisionId !== claim.document.latestRevisionId) {
    return { status: "receipt_readback_failed" };
  }
  const state = parseDocument(reread);
  const part = state.parts.find((entry) => entry.status === "receipt_recorded");
  if (!part?.receipt?.name || !part.receipt.createTime) return { status: "receipt_readback_failed" };
  part.status = "delivered";
  state.dispatchAttemptId = null;
  state.status = state.parts.every((entry) => entry.status === "delivered") ? "delivered" : "ready_next_part";
  const saved = await writeState(deps, issueId, claim.key, reread, state);
  return { status: state.status, state: parseDocument(saved), document: saved };
}

async function dispatchClaim(deps, config, envelope, claim, interaction, revisionId, envelopeDocument, sendPart) {
  if (claim.status === "already_delivered") return { status: "delivered" };
  if (["declined", "failed", "approval_mismatch", "held_for_reconciliation"].includes(claim.status)) {
    return { status: claim.status };
  }
  if (claim.status === "resume_receipt") {
    const resumed = await resumeRecordedReceipt(deps, config.issueId, claim);
    if (resumed.status === "delivered" || resumed.status === "receipt_readback_failed") {
      return { status: resumed.status };
    }
    const nextClaim = await claimIntent(deps, config, envelope, interaction, revisionId);
    if (nextClaim.status !== "claimed") return { status: nextClaim.status };
    return dispatchClaim(deps, config, envelope, nextClaim, interaction, revisionId, envelopeDocument, sendPart);
  }

  let currentDocument = claim.document;
  let state = claim.state;
  for (let index = 0; index < envelope.parts.length; index += 1) {
    const part = envelope.parts[index];
    const stored = state.parts[index];
    if (stored.status === "delivered") continue;
    if (stored.status !== "pending") return { status: "held_for_reconciliation" };

    stored.status = "sending";
    currentDocument = await writeState(deps, config.issueId, claim.key, currentDocument, state);
    if (!await acceptedApprovalIsCurrent(deps, config, envelope, interaction, envelopeDocument, revisionId)) {
      stored.status = "approval_mismatch";
      state.status = "approval_mismatch";
      state.dispatchAttemptId = null;
      await writeState(deps, config.issueId, claim.key, currentDocument, state);
      return { status: "approval_mismatch" };
    }
    const result = await sendPart(envelope, part);
    if (result?.status === "definite_failure") {
      stored.status = "failed";
      state.status = "failed";
      state.failureCode = result.code ?? "provider_rejected";
      await writeState(deps, config.issueId, claim.key, currentDocument, state);
      return { status: "failed" };
    }
    if (result?.status !== "sent" || typeof result.name !== "string" || !result.name
        || typeof result.createTime !== "string" || !result.createTime) {
      stored.status = "ambiguous";
      if (result?.receipt) stored.partialReceipt = result.receipt;
      state.status = "ambiguous";
      await writeState(deps, config.issueId, claim.key, currentDocument, state).catch(() => null);
      return { status: "held_for_reconciliation" };
    }

    stored.status = "receipt_recorded";
    stored.receipt = {
      name: result.name,
      createTime: result.createTime,
      requestId: part.requestId,
      messageId: part.messageId,
    };
    state.status = "receipt_recorded";
    const receiptDocument = await writeState(deps, config.issueId, claim.key, currentDocument, state);
    const readback = await deps.getDocument(config.issueId, claim.key).catch(() => null);
    if (!readback || readback.body !== receiptDocument.body
        || readback.latestRevisionId !== receiptDocument.latestRevisionId) {
      return { status: "receipt_readback_failed" };
    }

    stored.status = "delivered";
    state.dispatchAttemptId = null;
    state.status = index === envelope.parts.length - 1 ? "delivered" : "ready_next_part";
    currentDocument = await writeState(deps, config.issueId, claim.key, readback, state);
    if (state.status === "ready_next_part") {
      const nextClaim = await claimIntent(deps, config, envelope, interaction, state.approvedRevisionId);
      if (nextClaim.status !== "claimed") return { status: nextClaim.status };
      return dispatchClaim(deps, config, envelope, nextClaim, interaction, state.approvedRevisionId,
        envelopeDocument, sendPart);
    }
  }
  return { status: state.status === "delivered" ? "delivered" : "held_for_reconciliation" };
}

export async function runOutboundSend(envelopeInput, config, deps, sendPart) {
  const envelope = validateEnvelope(envelopeInput);
  if (!config || !uuid.test(config.issueId ?? "") || typeof config.approverUserId !== "string"
      || !config.approverUserId || typeof config.accountId !== "string" || !config.accountId
      || envelope.accountId !== config.accountId
      || typeof sendPart !== "function") {
    throw new Error("Configure the private Paperclip send issue, human approver and Chat account identity");
  }
  const key = documentKey("chat-send-", envelope.intentId);
  const expectedBody = canonical(envelope);
  let document = await deps.getDocument(config.issueId, key);
  if (!document) {
    try {
      document = await deps.putDocument(config.issueId, key, expectedBody, null);
    } catch (error) {
      document = await deps.getDocument(config.issueId, key).catch(() => null);
      if (!document) throw error;
    }
  }
  if (document.body !== expectedBody) return { status: "changed_envelope" };
  if (!document.lockedAt) {
    await deps.lockDocument(config.issueId, key);
    document = await deps.getDocument(config.issueId, key);
  }
  if (!document?.lockedAt || document.body !== expectedBody) return { status: "envelope_not_immutable" };

  const interactionKey = intentKey("chat-send-approval-", envelope.intentId);
  const interactions = await deps.listInteractions(config.issueId);
  let interaction = interactions.find((row) => row.idempotencyKey === interactionKey);
  if (!interaction) {
    interaction = await deps.createInteraction(config.issueId, {
      kind: "request_confirmation",
      resolverPolicy: "human_only",
      addresseeUserId: config.approverUserId,
      idempotencyKey: interactionKey,
      title: "Confirm outbound Google Chat reply",
      summary: "One exact Chat reply is held until the addressed human accepts this immutable envelope.",
      payload: {
        version: 1,
        prompt: "Accept this exact Google Chat send?",
        detailsMarkdown: preview(envelope),
        target: {
          type: "issue_document",
          issueId: config.issueId,
          documentId: document.id,
          key,
          revisionId: document.latestRevisionId,
          revisionNumber: document.latestRevisionNumber,
        },
      },
    });
  }
  if (interaction.status === "rejected" || interaction.status === "declined") {
    const state = await claimIntent(deps, config, envelope, interaction, document.latestRevisionId);
    if (state.status === "claimed") {
      state.state.status = "declined";
      state.state.parts = state.state.parts.map((part) => ({ ...part, status: "declined" }));
      await writeState(deps, config.issueId, state.key, state.document, state.state);
    }
    return { status: "declined" };
  }
  if (interaction.status !== "accepted") return { status: "awaiting_confirmation", interactionId: interaction.id };

  if (!acceptedInteractionMatches(interaction, config, config.issueId, document)
      || !interactionTargetMatches(interaction, config.issueId, document, document.latestRevisionId)) {
    return { status: "approval_mismatch" };
  }
  const revision = await deps.getRevision(config.issueId, key, document.latestRevisionId);
  const latest = await deps.getDocument(config.issueId, key);
  if (!revision || !revision.isLatest || revision.body !== expectedBody
      || !latest?.lockedAt || latest.latestRevisionId !== document.latestRevisionId
      || latest.body !== expectedBody) {
    return { status: "stale_approval" };
  }

  const claim = await claimIntent(deps, config, envelope, interaction, revision.id);
  return dispatchClaim(deps, config, envelope, claim, interaction, revision.id, document, sendPart);
}

class BoardError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function createBoardClient(config) {
  const apiOrigin = new URL(config.apiUrl);
  if (apiOrigin.protocol !== "https:" && !(apiOrigin.protocol === "http:"
      && ["127.0.0.1", "localhost", "[::1]"].includes(apiOrigin.hostname))) {
    throw new Error("Paperclip API must use HTTPS or loopback HTTP");
  }
  const request = async (path, method = "GET", body) => {
    const response = await fetch(new URL(`/api/${path}`, apiOrigin), {
      method,
      headers: { authorization: `Bearer ${config.apiKey}`, "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: "error",
      signal: AbortSignal.timeout(15_000),
    });
    if (!response.ok) {
      throw new BoardError(response.status, `Paperclip request failed (${response.status})`);
    }
    return response.json();
  };
  const path = (issueId, suffix) => `issues/${issueId}/${suffix}`;
  return {
    getDocument: async (issueId, key) => {
      try { return await request(path(issueId, `documents/${key}`)); }
      catch (error) { if (error.status === 404) return null; throw error; }
    },
    putDocument: (issueId, key, body, baseRevisionId) => request(
      path(issueId, `documents/${key}`), "PUT",
      { format: "markdown", body, ...(baseRevisionId ? { baseRevisionId } : {}) },
    ),
    lockDocument: (issueId, key) => request(path(issueId, `documents/${key}/lock`), "POST", {}),
    getRevision: (issueId, key, revisionId) => request(
      path(issueId, `documents/${key}/revisions/${revisionId}`),
    ),
    listInteractions: (issueId) => request(path(issueId, "interactions")),
    createInteraction: (issueId, body) => request(path(issueId, "interactions"), "POST", body),
  };
}

function sendPartWithGchat(envelope, part) {
  const sender = new URL("../../skills/ops/google-chat/gchat.py", import.meta.url);
  const providerEnv = { ...process.env };
  for (const key of ["CHAT_PAPERCLIP_API_URL", "CHAT_PAPERCLIP_API_KEY", "CHAT_SEND_ISSUE_ID",
    "CHAT_SEND_APPROVER_USER_ID", "CHAT_SEND_ACCOUNT_ID"]) delete providerEnv[key];
  try {
    const output = execFileSync("python3", [sender.pathname, "send", envelope.space, part.text,
      "--thread", envelope.thread, "--strict-thread", "--request-id", part.requestId,
      "--message-id", part.messageId, "--json"], {
      encoding: "utf8", timeout: 60_000, maxBuffer: 1024 * 1024, env: providerEnv,
    });
    const result = JSON.parse(output);
    if (result.outcome === "definite_failure") return { status: "definite_failure", code: result.code };
    if (result.sent === true && typeof result.name === "string" && typeof result.createTime === "string") {
      return { status: "sent", name: result.name, createTime: result.createTime };
    }
    return { status: "ambiguous", receipt: {
      ...(typeof result.name === "string" ? { name: result.name } : {}),
      ...(typeof result.createTime === "string" ? { createTime: result.createTime } : {}),
    } };
  } catch {
    return { status: "ambiguous" };
  }
}

async function main() {
  const config = {
    apiUrl: process.env.CHAT_PAPERCLIP_API_URL,
    apiKey: process.env.CHAT_PAPERCLIP_API_KEY,
    issueId: process.env.CHAT_SEND_ISSUE_ID,
    approverUserId: process.env.CHAT_SEND_APPROVER_USER_ID,
    accountId: process.env.CHAT_SEND_ACCOUNT_ID,
  };
  if (!config.apiUrl || !config.apiKey) throw new Error("Configure the Paperclip API URL and scoped Chat-send API key");
  const envelope = JSON.parse(await new Promise((resolve, reject) => {
    let input = "";
    process.stdin.setEncoding("utf8").on("data", (chunk) => { input += chunk; })
      .on("end", () => resolve(input)).on("error", reject);
  }));
  const result = await runOutboundSend(envelope, config, createBoardClient(config), sendPartWithGchat);
  process.stdout.write(`${JSON.stringify(result)}\n`);
  if (["awaiting_confirmation", "changed_envelope", "envelope_not_immutable", "approval_mismatch",
    "stale_approval", "held_for_reconciliation", "receipt_readback_failed"].includes(result.status)) {
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
