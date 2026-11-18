// Host-only publisher for draft RFCs. Never expose its GitHub credential to a model.
import { execFile } from "node:child_process";
import { mkdtempSync, openSync, readSync, closeSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";

const exec = promisify(execFile);
const maxInput = 128 * 1024;
const maxArtifact = 16 * 1024;

function readJob(path) {
  const handle = openSync(path, "r");
  try {
    const bytes = Buffer.alloc(maxInput + 1);
    let length = 0;
    while (length < bytes.length) {
      const count = readSync(handle, bytes, length, bytes.length - length, null);
      if (!count) break;
      length += count;
    }
    if (length > maxInput) throw new Error("RFC job input is too large");
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(0, length)));
  } finally {
    closeSync(handle);
  }
}

async function publish({ slug, artifact, expectedHead }) {
  const repo = process.env.RALPH_DOCS_REPO ?? "";
  const docsToken = process.env.RALPH_DOCS_GITHUB_TOKEN;
  if (!/^[a-z0-9][a-z0-9-]*\/[a-z0-9][a-z0-9_.-]*$/i.test(repo) || repo.endsWith(".git") || !docsToken) {
    throw new Error("Configure the docs repository and a docs-only token");
  }

  const cwd = mkdtempSync(join(tmpdir(), "ralph-docs-pr-"));
  try {
    const env = { PATH: process.env.PATH, HOME: process.env.HOME, GH_TOKEN: docsToken,
      GH_CONFIG_DIR: join(cwd, "gh"), GIT_TERMINAL_PROMPT: "0", GIT_CONFIG_NOSYSTEM: "1",
      GIT_CONFIG_GLOBAL: "/dev/null" };
    async function run(command, args) {
      try { return (await exec(command, args, { cwd, env, timeout: 60_000 })).stdout; }
      catch { throw new Error(`${command} ${args[0]} failed`); }
    }
    const gh = (...args) => run("gh", args);
    const git = (...args) => run("git", args);
    const author = (await gh("api", "user", "--jq", ".login")).trim();
    const base = (await gh("repo", "view", repo, "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name")).trim();
    const branch = `docs/rfc-${slug}`;
    if (!author || !base || base === branch) throw new Error("Could not identify the publisher or a safe RFC branch");
    await git("check-ref-format", `refs/heads/${base}`);
    const ref = `refs/heads/${branch}`;
    const path = `docs/1-projects/${slug}/rfc.md`;
    const remote = `https://github.com/${repo}.git`;
    const title = `RFC: ${slug}`;
    const body = `Draft RFC: \`${path}\`. Publication does not imply review or approval.`;
    const artifactFile = join(cwd, "artifact");
    writeFileSync(artifactFile, artifact);
    await git("init", "--bare", "--template=");
    await git("config", "credential.helper", "");
    await git("config", "--add", "credential.helper", "!gh auth git-credential");
    const list = async () => JSON.parse(await gh("pr", "list", "--repo", repo, "--head", branch, "--state", "all",
      "--json", "url,isDraft,state,baseRefName,isCrossRepository,author,headRefOid"));
    const existing = await list();
    const validatePr = (prs, head) => {
      if (prs.length !== 1 || prs[0].state !== "OPEN" || prs[0].isDraft !== true || prs[0].isCrossRepository !== false
          || prs[0].author?.login !== author || prs[0].baseRefName !== base) {
        throw new Error("Existing RFC PR does not match this draft; refusing to overwrite it");
      }
      if (head && prs[0].headRefOid !== head) throw new Error("Draft PR head does not match the reviewed RFC commit");
      if (typeof prs[0].url !== "string" || !prs[0].url.toLowerCase().startsWith(`https://github.com/${repo.toLowerCase()}/pull/`)
          || !/^[1-9]\d*$/.test(prs[0].url.split("/").at(-1))) {
        throw new Error("Draft PR URL is not in the configured docs repository");
      }
      return prs[0].url;
    };
    if (existing.length) validatePr(existing);
    const published = (await git("ls-remote", "--heads", remote, ref)).trim();
    const blob = (await git("hash-object", "-w", "--", artifactFile)).trim();
    await git("fetch", "--no-tags", remote, `refs/heads/${base}:refs/heads/base`,
      ...(published ? [`${ref}:refs/heads/rfc`] : []));
    if ((await git("ls-tree", "refs/heads/base", "--", path)).trim()) {
      throw new Error("RFC path already exists on the default branch");
    }
    let head;
    let oldHead = "";
    if (published) {
      oldHead = (await git("rev-parse", "refs/heads/rfc")).trim();
      const fork = (await git("merge-base", "refs/heads/base", oldHead)).trim();
      const files = await git("diff", "--name-status", "-z", "--no-renames", fork, oldHead);
      const entry = (await git("ls-tree", "refs/heads/rfc", "--", path)).trim();
      if (files !== `A\0${path}\0` || !/^100644 blob [a-f0-9]+\t/.test(entry)) {
        throw new Error("Existing RFC branch does not match this artifact; refusing to overwrite it");
      }
      if (existing.length) validatePr(existing, oldHead);
      if (entry === `100644 blob ${blob}\t${path}`) {
        head = oldHead;
        if (existing.length) return { pr_url: validatePr(existing, head), head_sha: head };
      } else if (!existing.length || expectedHead !== oldHead) {
        throw new Error("RFC revision requires the current draft head SHA; refusing to overwrite it");
      }
    } else {
      if (existing.length || expectedHead) throw new Error("Existing RFC PR has no matching branch");
    }
    if (!head) {
      const parent = oldHead || "refs/heads/base";
      await git("read-tree", parent);
      await git("update-index", "--add", "--cacheinfo", `100644,${blob},${path}`);
      const tree = (await git("write-tree")).trim();
      head = (await git("-c", "user.name=ralph-docs-author", "-c", "user.email=ralph-docs-author@localhost",
        "commit-tree", tree, "-p", parent, "-m", title)).trim();
      // Compare-and-swap. A revision's new commit is always a child of oldHead.
      await git("push", `--force-with-lease=${ref}:${oldHead}`, remote, `${head}:${ref}`);
    }
    const confirmed = (prs) => ({ pr_url: validatePr(prs, head), head_sha: head });
    if (existing.length) {
      try { return confirmed(await list()); }
      catch { throw new Error(`RFC commit ${head} was pushed, but draft state could not be confirmed; inspect the PR`); }
    }
    try {
      await gh("pr", "create", "--repo", repo, "--draft", "--base", base, "--head", branch,
        "--title", title, "--body", body);
    } catch (error) {
      const retry = await list();
      if (retry.length) return confirmed(retry);
      throw error;
    }
    return confirmed(await list());
  } finally {
    rmSync(cwd, { recursive: true, force: true });
  }
}

try {
  const input = process.argv[2];
  if (!input || process.argv.length !== 3) throw new Error("Usage: node compose/v4/docs-pr.mjs <job.json>");
  const job = readJob(input);
  if (!job || Object.keys(job).some((key) => !["slug", "artifact", "expectedHead"].includes(key))
      || typeof job.slug !== "string" || !/^[a-z0-9][a-z0-9-]{0,62}$/.test(job.slug)
      || typeof job.artifact !== "string" || !job.artifact.trim() || Buffer.byteLength(job.artifact) > maxArtifact
      || (job.expectedHead !== undefined && (typeof job.expectedHead !== "string"
        || !/^[a-f0-9]{40}$/.test(job.expectedHead)))) {
    throw new Error("Expected a slug, RFC text within 16 KiB and optional current draft head SHA");
  }
  console.log(JSON.stringify(await publish(job)));
} catch (error) {
  console.error(error instanceof SyntaxError ? "Invalid JSON from RFC job or service" : error.message);
  process.exitCode = 1;
}
