#!/usr/bin/env node
// Host-native coding harness (spec Sec 11): an MCP server whose tools are a
// thin wrapper over a configured CLI (adapters.json), not a reimplementation
// of coding/testing/worktree logic. This slice implements one tool,
// implement_spec, against one concrete adapter (mewrite), proving the
// harness-agnostic contract from Sec 11.0 end to end.
//
// implement_spec opens a real draft PR via `gh` when the adapter produces
// a diff. Non-goals: no pending_human_notes polling, no Docker sandboxing
// (the spec reserves that for audit_pr on untrusted external code, not
// needed for a repo this harness already trusts).

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
    // Without this, a missing git binary or a cwd that does not exist emits
    // an 'error' event with no listener, which Node treats as an uncaught
    // exception and crashes the whole server process rather than rejecting
    // this one call -- confirmed by reproducing it directly.
    proc.on("error", reject);
  });
}

function gh(args, cwd) {
  return new Promise((resolvePromise, reject) => {
    const proc = spawn("gh", args, { cwd, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "", stderr = "";
    proc.stdout.on("data", (d) => (stdout += d));
    proc.stderr.on("data", (d) => (stderr += d));
    proc.on("close", (code) => {
      if (code !== 0) reject(new Error(`gh ${args.join(" ")} failed: ${stderr.trim()}`));
      else resolvePromise(stdout);
    });
    proc.on("error", reject);
  });
}

// Pushes the branch and opens a draft PR, or finds the existing one for
// this branch -- idempotent, same reasoning as ensureWorktree: a retried
// implement_spec call for a branch already pushed must not open a second
// PR. Returns { pr_url, note } rather than throwing: a push/PR failure
// does not undo the real code change the adapter already made, so this is
// reported as a note alongside a successful diff, never as the whole
// call's failure.
async function ensurePullRequest(worktreePath, branch, ticketId) {
  try {
    // Always push first: an existing PR's branch still needs this run's
    // new commits to reach GitHub. The early-return-on-existing-PR this
    // replaced skipped the push entirely on a second call, so the PR
    // silently went stale -- the local commit existed, diff_stats
    // described it, but it never left the worktree. Caught by pushing a
    // second real change through this path and finding the remote branch
    // one commit behind local, not by inspection.
    await git(["push", "-u", "origin", branch], worktreePath);

    const existing = await gh(
      ["pr", "list", "--head", branch, "--json", "url", "--jq", ".[0].url"],
      worktreePath
    );
    const existingUrl = existing.trim();
    if (existingUrl) return { pr_url: existingUrl, note: null };

    try {
      const createdUrl = await gh(
        ["pr", "create", "--draft", "--title", `[ralph] ${ticketId}`,
         "--body", `Opened by the coding harness for ticket ${ticketId}. Draft: review before merging.`],
        worktreePath
      );
      return { pr_url: createdUrl.trim(), note: null };
    } catch (createErr) {
      // The list-then-create sequence above has a window: two concurrent
      // implement_spec calls for the same branch can both pass the list
      // check before either creates, and gh itself refuses the second
      // `pr create` for a branch that already has one. Re-checking here
      // (rather than treating that refusal as a real failure) is what
      // makes a loser of that race return the winner's URL instead of an
      // error -- the same race ensureWorktree already guards for worktree
      // creation, applied to PR creation.
      // The recheck's own failure is swallowed to "" on purpose, not a
      // bug: if it fails too, recheckUrl is empty either way, and
      // createErr -- the real create error, not a generic "recheck
      // failed" message -- is what gets thrown and reported below.
      const recheck = await gh(
        ["pr", "list", "--head", branch, "--json", "url", "--jq", ".[0].url"],
        worktreePath
      ).catch(() => "");
      const recheckUrl = recheck.trim();
      if (recheckUrl) return { pr_url: recheckUrl, note: null };
      throw createErr;
    }
  } catch (err) {
    // Deliberately not fatal: implement_spec's status still reflects
    // whether the adapter produced a correct diff, independent of whether
    // GitHub access happened to work this run.
    return { pr_url: null, note: `could not open a pull request: ${err.message}` };
  }
}

async function isValidWorktree(path) {
  try {
    const out = await git(["rev-parse", "--is-inside-work-tree"], path);
    return out.trim() === "true";
  } catch {
    return false;
  }
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

  // A directory existing is not the same as a valid worktree: a prior run
  // that crashed between mkdir and `git worktree add`, or any other stray
  // directory at this path, must not be silently reused and fail opaquely
  // on every later git call inside it.
  if (existsSync(worktreePath)) {
    if (await isValidWorktree(worktreePath)) return worktreePath;
    throw new Error(
      `${worktreePath} exists but is not a valid git worktree (a prior ` +
      `run may have crashed mid-setup); remove it manually before retrying ticket ${ticketId}`
    );
  }

  // Clears stale worktree metadata (e.g. a worktree directory removed by
  // something other than `git worktree remove`), so a branch that exists
  // with no live worktree does not get treated as still claimed.
  await git(["worktree", "prune"], repoPath).catch(() => {});

  const branches = await git(["branch", "--list", branch], repoPath);
  try {
    if (branches.trim()) {
      await git(["worktree", "add", worktreePath, branch], repoPath);
    } else {
      await git(["worktree", "add", "-b", branch, worktreePath], repoPath);
    }
  } catch (err) {
    // A second concurrent call for the same ticket_id racing this one is
    // the most likely cause of a worktree-add failure right after the
    // existsSync/prune checks above found nothing -- surfaced as its own
    // message rather than git's raw (and less actionable) error text.
    throw new Error(
      `failed to create worktree for ticket ${ticketId} (a concurrent ` +
      `implement_spec call for the same ticket is the most likely cause): ${err.message}`
    );
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

function errorPayload(branch, message) {
  // "error" is reserved for setup/infra failures where the adapter never
  // ran at all (bad rfc_path, a broken worktree, git itself failing) --
  // distinct from "failed", which means the adapter ran and did not
  // complete cleanly. A caller retries these differently: an "error" is a
  // request/environment problem to fix before retrying; a "failed" is a
  // legitimate adapter outcome that may just need a different approach.
  return {
    status: "error",
    pr_url: null,
    branch,
    test_summary: null,
    diff_stats: null,
    open_questions: [message],
  };
}

function worktreeStatusErrorPayload(message) {
  // Mirrors getWorktreeStatus's own success keys, nulled out -- not
  // implementSpec's errorPayload shape (pr_url/test_summary/diff_stats),
  // which this tool has no equivalent fields for. The ticket this tool was
  // built from asked for "the same error-payload pattern implement_spec
  // uses"; returning only {status, open_questions} the first time this was
  // implemented did not meet that -- fixed here, and the fix is returning
  // every field this tool's own success payload has, nulled, not borrowing
  // implement_spec's unrelated field names.
  return {
    status: "error",
    branch: null,
    clean: null,
    ci_state: null,
    pending_notes_count: null,
    open_questions: [message],
  };
}

async function getWorktreeStatus(args) {
  // ticket_id is not used to derive a path: unlike implement_spec (which
  // provisions a *new* worktree from a base repo_path), this tool's
  // repo_path argument is already the worktree to inspect, matching the
  // spec's description for the harness's other "checks out existing
  // worktree" tools. ticket_id is carried for logging/future use, same as
  // implement_spec's unused `product` argument -- intentionally, not an
  // oversight.
  const { repo_path } = args;
  if (!(await isValidWorktree(repo_path))) {
    return worktreeStatusErrorPayload(`repo_path is not a valid git working tree: ${repo_path}`);
  }
  let branch, porcelain;
  try {
    branch = (await git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)).trim();
    porcelain = await git(["status", "--porcelain"], repo_path);
  } catch (err) {
    return worktreeStatusErrorPayload(`could not read worktree status: ${err.message}`);
  }
  // A detached HEAD makes git itself print the literal string "HEAD", not
  // a real branch name or an error -- returning that silently as `branch`
  // would read as a working branch called "HEAD". Surfaced explicitly
  // instead: null branch, with the real reason carried in open_questions,
  // same as any other field this tool cannot honestly answer.
  if (branch === "HEAD") {
    return {
      status: "completed",
      branch: null,
      clean: porcelain.trim() === "",
      ci_state: null,
      pending_notes_count: 0,
      open_questions: ["worktree is in detached HEAD state; no branch name to report"],
    };
  }
  return {
    status: "completed",
    branch,
    clean: porcelain.trim() === "",
    // Stubs, not computed: no CI integration and no pending_human_notes
    // table exist yet (spec Sec 11's own ci_state/pending_notes_count
    // fields). An honest null/0 here is correct for this slice, not a
    // shortcut -- see coding-harness/README.md.
    ci_state: null,
    pending_notes_count: 0,
    open_questions: [],
  };
}

async function implementSpec(args) {
  const { ticket_id, repo_path, branch, rfc_path, acceptance_criteria } = args;

  if (!existsSync(rfc_path)) {
    return errorPayload(branch, `rfc_path does not exist: ${rfc_path}`);
  }

  let worktreePath;
  try {
    worktreePath = await ensureWorktree(repo_path, branch, ticket_id);
  } catch (err) {
    // Every setup failure (missing repo_path, a stray non-worktree
    // directory, a concurrent call racing this one) returns the
    // structured payload the MCP contract promises, rather than letting
    // the rejection propagate as an unhandled protocol-level error.
    return errorPayload(branch, `could not provision worktree: ${err.message}`);
  }

  let rfcContent;
  try {
    rfcContent = readFileSync(rfc_path, "utf8");
  } catch (err) {
    return errorPayload(branch, `could not read rfc_path: ${err.message}`);
  }
  const prompt = buildPrompt(rfcContent, acceptance_criteria);

  const adapter = ADAPTERS.mewrite;
  let result;
  try {
    result = await runAdapter(adapter, worktreePath, prompt, []);
  } catch (err) {
    // spawn() itself failing (adapter binary missing, etc.) is also a
    // setup failure, not the adapter "running and failing".
    return errorPayload(branch, `could not run adapter: ${err.message}`);
  }
  const { finalText, turnEndSeen } = parseJsonlCompletion(result.stdout);

  // --stat HEAD alone misses new untracked files -- a CLI run that only
  // creates files (no edits to tracked ones) would report an empty diff
  // despite real work happening. `git add -A` first (worktree-local, never
  // touching the index outside it) makes new files visible to the stat.
  await git(["add", "-A"], worktreePath).catch(() => {});
  const diffStat = await git(["diff", "--stat", "HEAD"], worktreePath).catch(() => "");

  const status = result.code === 0 && turnEndSeen ? "completed" : "failed";
  const openQuestions = result.code !== 0 ? [`adapter exited ${result.code}: ${result.stderr.trim().slice(0, 500)}`] : [];

  let prUrl = null;
  // Only on a real, completed diff: a failed run, or one that touched
  // nothing, has nothing to open a PR for. The adapter's own edits are
  // staged (git add -A above) but not committed -- committing here, right
  // before the push, keeps the harness (not the adapter) as the one place
  // that decides what constitutes a reviewable unit of work.
  if (status === "completed" && diffStat.trim()) {
    try {
      // -c identity flags, not a global/repo git config write: this
      // process's commits are attributed to the harness, without
      // depending on (or silently overwriting) whatever git identity the
      // host or operator already has configured -- found missing by
      // hitting "Author identity unknown" on a real host with no global
      // git user.name/email set at all.
      await git([
        "-c", "user.name=ralph-coding-harness",
        "-c", "user.email=ralph-coding-harness@localhost",
        "commit", "-m", `[ralph] ${ticket_id}`,
      ], worktreePath);
    } catch (err) {
      openQuestions.push(`could not commit changes: ${err.message}`);
    }
    if (!openQuestions.length) {
      const { pr_url, note } = await ensurePullRequest(worktreePath, branch, ticket_id);
      prUrl = pr_url;
      if (note) openQuestions.push(note);
    }
  }

  return {
    status,
    pr_url: prUrl,
    branch,
    test_summary: finalText,
    diff_stats: diffStat.trim() || null,
    open_questions: openQuestions,
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
    {
      name: "get_worktree_status",
      description:
        "Reports a worktree's current branch and clean/dirty state (spec Sec 11). ci_state and pending_notes_count are always stubbed (null/0) until CI integration and pending_human_notes exist.",
      inputSchema: {
        type: "object",
        properties: {
          ticket_id: { type: "string" },
          repo_path: { type: "string" },
        },
        required: ["ticket_id", "repo_path"],
      },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: toolArgs } = request.params;
  let payload;
  if (name === "implement_spec") {
    payload = await implementSpec(toolArgs);
  } else if (name === "get_worktree_status") {
    payload = await getWorktreeStatus(toolArgs);
  } else {
    throw new Error(`unknown tool: ${name}`);
  }
  return { content: [{ type: "text", text: JSON.stringify(payload) }] };
});

const transport = new StdioServerTransport();
await server.connect(transport);
