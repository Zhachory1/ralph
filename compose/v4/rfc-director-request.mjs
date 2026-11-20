// Draft an RFC, run it through Council, and request a native Director review on a real board.
// This is the first slice that connects author.mjs, the Council panel and decideRequestRoute
// to Paperclip's own issue_document and Director-only request_confirmation primitives.
//
// `board` is an already-authenticated Paperclip client for the target company:
// `board(path, method, body) => Promise<parsed JSON>`. This module does not pick its
// transport or credential; the host wires that the same way it already does elsewhere.
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { draftRfc } from "./author.mjs";
import { runV1Council } from "./council-v1.mjs";
import { decideRequestRoute } from "./request-routing.mjs";

const JOB_KEYS = ["board", "directorUserId", "issueId", "brief", "model", "domain",
  "revisions", "currentDraft", "feedback"];

export async function requestDirectorReview(job) {
  if (!job || Object.keys(job).some((key) => !JOB_KEYS.includes(key))) {
    throw new TypeError("Expected an RFC review request");
  }
  const { board, directorUserId, issueId, brief, model, domain,
    revisions = 0, currentDraft, feedback } = job;
  if (typeof board !== "function" || typeof directorUserId !== "string" || !directorUserId.trim()
      || typeof issueId !== "string" || !issueId.trim() || !["coding", "non-coding"].includes(domain)) {
    throw new TypeError("Expected a board client, Director user ID, issue ID and domain");
  }

  const artifact = await draftRfc({ brief, model, ...(currentDraft !== undefined && { currentDraft, feedback }) });
  const panel = await runV1Council({ artifact, revisions });
  const digest = createHash("sha256").update(artifact).digest("hex");
  // initiative is always true: this step exists only to ask the Director, never to self-advance.
  const route = decideRequestRoute({ kind: "novel", domain, artifact }, {
    allowed: true, initiative: true, consequential: false, revisions,
    council: { status: "completed", artifactSha256: digest, result: { verdicts: panel.verdicts } },
  });
  if (route !== "director") return { route, artifact, verdicts: panel.verdicts };

  // The revision requested below is whatever Paperclip just stored; verifying that it
  // stays the exact approved revision through to publication is a separate assurance
  // (docs/workflows.md), not another gate here.
  const document = await board(`issues/${issueId}/documents/rfc`, "PUT", { format: "markdown", body: artifact });
  if (!document || document.id === undefined || document.latestRevisionId === undefined) {
    throw new Error("Paperclip did not return the stored RFC document");
  }
  const confirmation = await board(`issues/${issueId}/interactions`, "POST", {
    kind: "request_confirmation", idempotencyKey: document.latestRevisionId,
    continuationPolicy: "wake_assignee_on_accept",
    resolverPolicy: "human_only", addresseeUserId: directorUserId,
    payload: {
      version: 1, prompt: "Approve this exact RFC revision?",
      target: {
        type: "issue_document", issueId, key: "rfc", documentId: document.id,
        revisionId: document.latestRevisionId, revisionNumber: document.latestRevisionNumber,
      },
    },
  });
  if (!confirmation || confirmation.id === undefined) throw new Error("Paperclip did not return the review request");

  return {
    route, artifact, verdicts: panel.verdicts, documentId: document.id,
    revisionId: document.latestRevisionId, interactionId: confirmation.id,
  };
}

const maxJobBytes = 64 * 1024;

function readJob(path) {
  const raw = path !== undefined ? readFileSync(path) : readFileSync(0);
  if (raw.length > maxJobBytes) throw new Error("RFC review job input is too large");
  const job = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw));
  if (!job || typeof job !== "object" || Array.isArray(job)) throw new Error("Expected a JSON object");
  return job;
}

// Host CLI: reuses routine-audit-worker.mjs's loopback-origin and board-API-bearer-token
// convention -- no new credential scheme. The job (brief, issue, Director, model) is config,
// never a secret; only PAPERCLIP_API_KEY and OPENROUTER_API_KEY come from the environment.
async function main() {
  const [origin, jobPath, ...extra] = process.argv.slice(2);
  if (!/^http:\/\/127\.0\.0\.1:[0-9]+$/.test(origin ?? "") || extra.length) {
    throw new Error("Usage: node compose/v4/rfc-director-request.mjs ORIGIN [JOB_FILE]\n"
      + "Reads the job JSON from JOB_FILE, or from stdin if omitted.");
  }
  const token = process.env.PAPERCLIP_API_KEY;
  delete process.env.PAPERCLIP_API_KEY;
  if (!token) throw new Error("Set a temporary scoped PAPERCLIP_API_KEY");
  const job = readJob(jobPath);
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
  // job comes last so a job file cannot smuggle in its own "board" and bypass this transport.
  console.log(JSON.stringify(await requestDirectorReview({ ...job, board })));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error instanceof SyntaxError ? "Invalid JSON in the RFC review job" : error.message);
    process.exitCode = 1;
  });
}
