// Process one trusted, normalized GitHub event for an outbound PR through a manually
// triggered Paperclip Routine (#263). Reuses the Sec 6.1 loop-guard dedup policy (bot replies
// and already-handled events are ignored) and a bounded-retry/escalate policy (PDF Sec 6: at
// most MAX_WAKE_ATTEMPTS attempts), now expressed against the Paperclip board instead of the
// superseded n8n/Postgres control plane (#240 supersedes #206). Resumes the existing OpenCode
// worktree via a comment for an allowed fix; base drift, merge readiness and exhausted retries
// notify the Director instead of acting.
//
// `event_type: "review_feedback"` means feedback an upstream normalizer has already classified
// as an ordinary, allowed fix. This worker does not read feedback content or judge ambiguity:
// any feedback that is substantive, unclear, or not obviously an allowed fix must be classified
// upstream (before it reaches this worker) as `base_drift` or `merge_ready`-style escalation, or
// as a future distinct event type — never let ambiguous feedback enter as `review_feedback`.
//
// Concurrency: this worker requires serial execution per issue, and that is an *operational*
// requirement this code does not enforce on its own -- run one `routine-maintenance-worker.mjs`
// process at a time (per issue, or globally, until a real scheduler exists); never fan two
// invocations out concurrently for the same issue. The Routine itself should also be created
// with `concurrencyPolicy: "coalesce_if_active"`, but that setting only governs whether
// Paperclip starts a *new run* while one is already active on the Routine -- it does not
// serialize this host worker process, and two already-created runs for the same issue can
// still be handed to two worker invocations at once if an operator or caller runs them that
// way. Both are required together: `coalesce_if_active` on the Routine, and serial execution
// of the host worker. The `baseRevisionId` compare-and-swap on the `maintenance` document is a
// narrower, in-process guard against a write landing between this run's read and write if that
// operational requirement is ever violated -- it is not a substitute for it. The event's own
// effect (wake or escalate) is only performed after that compare-and-swap claim succeeds, so a
// run that never reaches the claim can be replayed safely and a replay that finds the claim
// already committed is a no-op (the dedup check above). If the claim lands but the effect call
// then fails (network error, timeout, 5xx), the event is already marked processed: it will
// never be retried
// automatically, and we report `effect_ambiguous` rather than build a generalized recovery
// engine (rollback, auto-retry, or reconciliation) that attempts to guess what the far end did
// with a request it may or may not have received. An operator must look at the PR and the
// Paperclip issue directly.
import { pathToFileURL } from "node:url";

export const MAX_WAKE_ATTEMPTS = 2;
const WAKE_EVENTS = new Set(["ci_failure", "review_feedback"]);
const ESCALATE_EVENTS = new Set(["base_drift", "merge_ready"]);
const sha = /^[a-f0-9]{40}$/;
const uuid = /^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
// Verified against a disposable Paperclip instance (compose/v4/test/paperclip-maintenance-canary.py):
// company and issue ids are UUIDs, but a user id (the Director, from auth/get-session) is an
// opaque ~32-char auth-library token, not a UUID. Validated here for safe interpolation only.
const opaqueId = /^[A-Za-z0-9_-]{1,128}$/;
const repoSlug = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

function validateEvent(event) {
  const { issueId, repo, pr_number: pr, head_sha: head, event_type: type, event_id: id, author_login: author } = event ?? {};
  if (typeof issueId !== "string" || !uuid.test(issueId) || !repoSlug.test(repo) || !Number.isSafeInteger(pr) || pr < 1
      || typeof head !== "string" || !sha.test(head) || !(WAKE_EVENTS.has(type) || ESCALATE_EVENTS.has(type))
      || typeof id !== "string" || !id || typeof author !== "string" || !author) {
    throw new Error("Expected a trusted outbound PR event with a linked issue UUID, repo, PR number, head SHA, type, id and author");
  }
  return { issueId, repo, pr, head, type, id, author };
}

function validateState(state) {
  if (!state || !Array.isArray(state.processedEventIds) || !state.processedEventIds.every((row) => typeof row === "string")
      || !Number.isInteger(state.wakeCount) || state.wakeCount < 0 || typeof state.escalated !== "boolean") {
    throw new Error("Unexpected outbound maintenance state shape");
  }
  return state;
}

// deps.loadTrackedPr(issueId) -> {repo, pr_number} | null — ownership check, defense in depth
//   against a trusted issueId that no longer (or never did) track the event's repo/PR.
// deps.loadState(issueId) -> { state: {processedEventIds, wakeCount, escalated}, revisionId }
// deps.saveState(issueId, revisionId, nextState) -> truthy on a successful compare-and-swap
//   claim, falsy on a stale revision. Never called on a path this function treats as a no-op.
// deps.wake(issueId, detail) / deps.escalate(issueId, detail) -> truthy on success, falsy (not
//   thrown) for a failure this worker must report rather than retry.
export async function runOutboundMaintenance(event, { botLogin, loadTrackedPr, loadState, saveState, wake, escalate }) {
  if (typeof botLogin !== "string" || !botLogin) throw new Error("Configure this worker's own GitHub login");
  const { issueId, repo, pr, head, type, id, author } = validateEvent(event);
  if (author.toLowerCase() === botLogin.toLowerCase()) return { status: "own_reply" };

  const tracked = await loadTrackedPr(issueId);
  if (!tracked || tracked.repo !== repo || tracked.pr_number !== pr) return { status: "out_of_scope" };

  const { state, revisionId } = await loadState(issueId);
  validateState(state);
  if (state.processedEventIds.includes(id)) return { status: "duplicate" };
  if (state.escalated) return { status: "already_escalated" };

  const detail = { repo, pr, head, type, id };
  const exhausted = WAKE_EVENTS.has(type) && state.wakeCount >= MAX_WAKE_ATTEMPTS;
  const escalating = ESCALATE_EVENTS.has(type) || exhausted;
  const reason = escalating ? (exhausted ? "wake_attempts_exhausted" : type) : undefined;
  const wakeCount = escalating ? state.wakeCount : state.wakeCount + 1;
  const nextState = { processedEventIds: [...state.processedEventIds, id], wakeCount, escalated: escalating };

  // Claim first: commit the event as handled before performing its one-time effect.
  const claimed = await saveState(issueId, revisionId, nextState);
  if (!claimed) return { status: "stale_state" };

  const performed = escalating ? await escalate(issueId, { ...detail, reason }) : await wake(issueId, detail);
  if (!performed) return { status: "effect_ambiguous", action: escalating ? "escalate" : "wake" };
  return escalating ? { status: "escalated", reason } : { status: "woke", wakeCount };
}

class BoardError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// Builds the Paperclip wiring for runOutboundMaintenance from an injected `board` transport.
// `board(path, method, body)` must resolve with the parsed JSON body on a 2xx response and
// reject with a BoardError (or any error exposing `.status`) carrying the real HTTP status on
// a non-2xx response, so only a 404 is ever treated as "this document does not exist yet" --
// no other board error is swallowed into default/reset state.
export function createPaperclipDeps(board, { botLogin, directorUserId }) {
  if (typeof directorUserId !== "string" || !opaqueId.test(directorUserId)) {
    throw new Error("Expected the Director's user id as a safe-to-interpolate opaque id");
  }
  const readDocument = async (issueId, key) => {
    try {
      return await board(`issues/${issueId}/documents/${key}`);
    } catch (error) {
      if (error?.status === 404) return null;
      throw error;
    }
  };
  const loadTrackedPr = async (issueId) => {
    const doc = await readDocument(issueId, "outbound-pr");
    if (!doc) return null;
    try {
      const parsed = JSON.parse(doc.body);
      return typeof parsed?.repo === "string" && Number.isSafeInteger(parsed?.pr_number) ? parsed : null;
    } catch {
      return null;
    }
  };
  const loadState = async (issueId) => {
    const doc = await readDocument(issueId, "maintenance");
    if (!doc) return { state: { processedEventIds: [], wakeCount: 0, escalated: false }, revisionId: undefined };
    return { state: JSON.parse(doc.body), revisionId: doc.latestRevisionId };
  };
  const saveState = async (issueId, revisionId, state) => {
    try {
      await board(`issues/${issueId}/documents/maintenance`, "PUT",
        { format: "markdown", body: JSON.stringify(state), ...(revisionId ? { baseRevisionId: revisionId } : {}) });
      return true;
    } catch {
      return false;
    }
  };
  const wake = async (issueId, detail) => {
    try {
      await board(`issues/${issueId}/comments`, "POST", {
        body: `Outbound maintenance: ${detail.type} on ${detail.repo}#${detail.pr} at \`${detail.head}\` `
          + `(event ${detail.id}). Resuming to apply an allowed fix.`,
      });
      return true;
    } catch {
      return false;
    }
  };
  const escalate = async (issueId, detail) => {
    try {
      await board(`issues/${issueId}/interactions`, "POST", {
        kind: "request_confirmation", resolverPolicy: "human_only", addresseeUserId: directorUserId,
        idempotencyKey: `outbound-maintenance:${issueId}:${detail.id}`,
        payload: { version: 1, prompt: `Outbound PR maintenance needs the Director: ${detail.reason} on ${detail.repo}#${detail.pr}.`, event: detail },
      });
      return true;
    } catch {
      return false;
    }
  };
  return { botLogin, loadTrackedPr, loadState, saveState, wake, escalate };
}

// --- Routine entrypoint: wires the pure decision above to a real loopback Paperclip board. ---
// The Director's user id is operator-configured, never auto-discovered (same convention as
// run-ready-merge.mjs's config.directors): this worker never decides who the Director is.
// No polling or scheduling here: one manually triggered Routine run in, one decision out.
async function main() {
  const [origin, companyId, routineId, runId, botLogin, directorUserId, ...extra] = process.argv.slice(2);
  if (!/^http:\/\/127\.0\.0\.1:[0-9]+$/.test(origin) || ![companyId, routineId, runId].every((id) => uuid.test(id))
      || !botLogin || !opaqueId.test(directorUserId ?? "") || extra.length) {
    throw new Error("Usage: routine-maintenance-worker.mjs BOARD_ORIGIN COMPANY_ID ROUTINE_ID RUN_ID BOT_LOGIN DIRECTOR_USER_ID");
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
    if (!response.ok) throw new BoardError(response.status, `Paperclip ${method} ${path} failed (${response.status})`);
    return response.json();
  };

  const routine = await board(`routines/${routineId}`);
  if (routine.id !== routineId || routine.companyId !== companyId) throw new Error("Unexpected Routine company");
  const runs = await board(`routines/${routineId}/runs`);
  const run = runs.find((row) => row.id === runId);
  const payload = run?.triggerPayload;
  if (run?.source !== "manual" || !payload) throw new Error("Expected a manually triggered Routine run with a trigger payload");

  const deps = createPaperclipDeps(board, { botLogin, directorUserId });
  const result = await runOutboundMaintenance({
    issueId: payload.issueId, repo: payload.repo, pr_number: payload.pr_number, head_sha: payload.head_sha,
    event_type: payload.event_type, event_id: payload.event_id, author_login: payload.author_login,
  }, deps);
  console.log(JSON.stringify(result));
  if (result.status === "effect_ambiguous" || result.status === "stale_state") process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
