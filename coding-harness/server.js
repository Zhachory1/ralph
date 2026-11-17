#!/usr/bin/env node
// Host-native coding harness (spec Sec 11): an MCP server whose tools are a
// thin wrapper over a configured CLI (adapters.json), not a reimplementation
// of coding/testing/worktree logic. This slice implements one tool,
// implement_spec, against one concrete adapter (mewrite), proving the
// harness-agnostic contract from Sec 11.0 end to end.
//
// Non-goals this slice: no GitHub PR creation (pr_url is always null until
// that lands), no pending_human_notes polling, no Docker sandboxing (the
// spec reserves that for audit_pr on untrusted external code, not needed
// for a repo this harness already trusts).

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { ListToolsRequestSchema, CallToolRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { spawn } from "node:child_process";
import { readFileSync, existsSync, mkdirSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ADAPTERS = JSON.parse(readFileSync(resolve(HERE, "adapters.json"), "utf8"));

function git(args, cwd) {
  return new Promise((resolvePromise, reject) => {
    const proc = spawn("git", args, { cwd });
    let stdout = "", stderr = "";
    proc.stdout.on("data", (d) => (stdout += d));
    proc.stderr.on("data", (d) => (stderr += d));
    proc.on("close", (code) => {
      if (code !== 0) reject(new Error(`git ${args.join(" ")} failed: ${stderr.trim()}`));
      else resolvePromise(stdout);
    });
  });
}

// Provisions an isolated worktree for this ticket, per spec Sec 11's
// implement_spec row ("Provisions isolated Git worktree for repo_path").
// Idempotent: re-running against a worktree that already exists for this
// ticket reuses it rather than failing, matching the spec's bounded-retry
// expectation (a worker crash mid-run should not orphan work).
async function ensureWorktree(repoPath, branch, ticketId) {
  const worktreeRoot = resolve(repoPath, "..", ".harness-worktrees");
  const worktreePath = resolve(worktreeRoot, ticketId);
  mkdirSync(worktreeRoot, { recursive: true });
  if (existsSync(worktreePath)) return worktreePath;

  const branches = await git(["branch", "--list", branch], repoPath);
  if (branches.trim()) {
    await git(["worktree", "add", worktreePath, branch], repoPath);
  } else {
    await git(["worktree", "add", "-b", branch, worktreePath], repoPath);
  }
  return worktreePath;
}

function buildPrompt(rfcContent, acceptanceCriteria) {
  const criteria = (acceptanceCriteria || [])
    .map((c, i) => `${i + 1}. ${c}`)
    .join("\n");
  return [
    "Implement the following specification. Make the smallest correct change",
    "that satisfies every acceptance criterion below, run the project's own",
    "tests if a test command is discoverable, and report what you did.",
    "",
    "--- Specification ---",
    rfcContent,
    "",
    "--- Acceptance criteria ---",
    criteria || "(none given)",
  ].join("\n");
}

// Runs the configured adapter's CLI non-interactively in the worktree and
// parses its JSONL event stream for the final assistant message and exit
// status -- the "machine-parseable completion signal" half of the Sec 11.0
// contract. Adapters that do not emit JSONL would need their own parser
// here; this slice only implements mewrite's.
function runAdapter(adapter, cwd, prompt, skillPaths) {
  return new Promise((resolvePromise, reject) => {
    const args = [adapter.promptFlag, ...adapter.modeFlag];
    for (const skill of skillPaths || []) {
      args.push(adapter.skillFlag, skill);
    }
    args.push(prompt);

    // stdin must be explicitly closed ('ignore'), not left as an open pipe:
    // mewrite blocks waiting for stdin EOF otherwise, which a bare spawn()
    // default never sends -- found by hitting this hang directly, not
    // documented behavior. -p/--print mode never reads stdin itself.
    const proc = spawn(adapter.command, args, { cwd, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "", stderr = "";
    proc.stdout.on("data", (d) => (stdout += d));
    proc.stderr.on("data", (d) => (stderr += d));
    proc.on("close", (code) => {
      resolvePromise({ code, stdout, stderr });
    });
    proc.on("error", reject);
  });
}

function parseJsonlCompletion(stdout) {
  const lines = stdout.split("\n").filter((l) => l.trim());
  let finalText = null;
  let turnEndSeen = false;
  for (const line of lines) {
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      continue; // a non-JSON line is not this adapter's contract violation to raise
    }
    if (event.type === "turn_end" && event.message?.content) {
      turnEndSeen = true;
      const textParts = event.message.content.filter((c) => c.type === "text");
      finalText = textParts.map((c) => c.text).join("\n");
    }
  }
  return { finalText, turnEndSeen };
}

async function implementSpec(args) {
  const { ticket_id, repo_path, branch, rfc_path, acceptance_criteria } = args;

  if (!existsSync(rfc_path)) {
    return {
      status: "error",
      pr_url: null,
      branch,
      test_summary: null,
      diff_stats: null,
      open_questions: [`rfc_path does not exist: ${rfc_path}`],
    };
  }

  const worktreePath = await ensureWorktree(repo_path, branch, ticket_id);
  const rfcContent = readFileSync(rfc_path, "utf8");
  const prompt = buildPrompt(rfcContent, acceptance_criteria);

  const adapter = ADAPTERS.mewrite;
  const result = await runAdapter(adapter, worktreePath, prompt, []);
  const { finalText, turnEndSeen } = parseJsonlCompletion(result.stdout);

  // --stat HEAD alone misses new untracked files -- a CLI run that only
  // creates files (no edits to tracked ones) would report an empty diff
  // despite real work happening. `git add -A` first (worktree-local, never
  // touching the index outside it) makes new files visible to the stat.
  await git(["add", "-A"], worktreePath).catch(() => {});
  const diffStat = await git(["diff", "--stat", "HEAD"], worktreePath).catch(() => "");

  return {
    status: result.code === 0 && turnEndSeen ? "completed" : "failed",
    // Pull-request creation is out of scope for this slice (see header
    // comment); real automation would open one here and fill this in.
    pr_url: null,
    branch,
    test_summary: finalText,
    diff_stats: diffStat.trim() || null,
    open_questions: result.code !== 0 ? [`adapter exited ${result.code}: ${result.stderr.trim().slice(0, 500)}`] : [],
  };
}

const server = new Server(
  { name: "ralph-coding-harness", version: "0.0.1" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "implement_spec",
      description:
        "Provisions an isolated Git worktree, runs the configured coding CLI against an RFC and acceptance criteria, and returns a structured result (spec Sec 11).",
      inputSchema: {
        type: "object",
        properties: {
          ticket_id: { type: "string" },
          product: { type: "string" },
          repo_path: { type: "string" },
          branch: { type: "string" },
          rfc_path: { type: "string" },
          acceptance_criteria: { type: "array", items: { type: "string" } },
        },
        required: ["ticket_id", "repo_path", "branch", "rfc_path"],
      },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: toolArgs } = request.params;
  if (name !== "implement_spec") {
    throw new Error(`unknown tool: ${name}`);
  }
  const payload = await implementSpec(toolArgs);
  return { content: [{ type: "text", text: JSON.stringify(payload) }] };
});

const transport = new StdioServerTransport();
await server.connect(transport);
