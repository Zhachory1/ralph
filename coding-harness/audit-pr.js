import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { realpathSync } from "node:fs";

function run(file, args, cwd, input) {
  return new Promise((resolve, reject) => {
    const child = execFile(file, args, { cwd, encoding: "buffer", timeout: 120_000, maxBuffer: 32 * 1024 * 1024 },
      (error, stdout, stderr) => {
        if (error && typeof error.code !== "number") reject(error);
        else resolve({ code: error?.code ?? 0, stdout, stderr });
      });
    child.stdin.on("error", () => {});
    child.stdin.end(input);
  });
}

async function checked(file, args, cwd) {
  const result = await run(file, args, cwd);
  if (result.code !== 0) throw new Error(`${file} ${args[0]} failed (${result.code})`);
  return result.stdout;
}

async function sandbox(config, command, archive, cwd) {
  const name = `ralph-audit-${randomUUID()}`;
  try {
    const result = await run("docker", ["run", "--rm", "--name", name, "-i", "--pull=never",
      "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
      "--user=65534:65534", "--pids-limit=64", "--memory=512m", "--cpus=1",
      "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m", "--env", "HOME=/tmp",
      "--entrypoint", "/bin/sh", config.image, "-c",
      'mkdir /tmp/source && cd /tmp/source && tar -xf - || exit 125; exec /bin/sh -c "$1" </dev/null',
      "audit", command], cwd, archive);
    if (result.code >= 125) throw new Error(`Audit sandbox failed (${result.code})`);
    return { passed: result.code === 0, output: Buffer.concat([result.stdout, result.stderr]).toString().slice(-20_000) };
  } finally {
    await run("docker", ["rm", "-f", name], cwd);
  }
}

export async function auditPr({ repo_path, pr_number, head_sha }) {
  if (!Number.isSafeInteger(pr_number) || pr_number < 1 || !/^[a-f0-9]{40}$/.test(head_sha)) {
    throw new Error("audit_pr requires a positive PR number and full lowercase head SHA");
  }
  const cwd = realpathSync(repo_path);
  const repos = JSON.parse(process.env.RALPH_AUDIT_REPOS || "{}");
  const config = Object.hasOwn(repos, cwd) ? repos[cwd] : null;
  if (!config || typeof config.image !== "string" || !/^[\w./:-]+@sha256:[a-f0-9]{64}$/.test(config.image)
      || ![config.test, config.lint].every((cmd) => typeof cmd === "string" && cmd.trim())) {
    throw new Error("audit_pr requires an operator-configured image digest and test/lint commands");
  }
  const git = (args) => checked("git", args, cwd);
  const ref = `refs/ralph-audit/${randomUUID()}`;
  try {
    await git(["fetch", "--no-tags", "--no-write-fetch-head", "origin",
      `refs/pull/${pr_number}/head:${ref}/head`, `refs/pull/${pr_number}/merge:${ref}/merge`]);
    const head = (await git(["rev-parse", `${ref}/head`])).toString().trim();
    const mergeHead = (await git(["rev-parse", `${ref}/merge^2`])).toString().trim();
    if (head !== head_sha || mergeHead !== head_sha) throw new Error("PR head SHA mismatch");
    const changed = await git(["diff", "--name-only", "-z", `${ref}/merge^1...${head}`]);
    const archive = await git(["archive", "--format=tar", head]);
    const test = await sandbox(config, config.test, archive, cwd);
    const lint = await sandbox(config, config.lint, archive, cwd);
    return { tests_passed: test.passed, test_summary: test.output,
      lint_issues: lint.passed ? [] : [lint.output || "Lint command failed"],
      changed_files: changed.toString().split("\0").filter(Boolean) };
  } finally {
    for (const suffix of ["head", "merge"]) {
      await git(["update-ref", "-d", `${ref}/${suffix}`]).catch((error) => console.error(error.message));
    }
  }
}
