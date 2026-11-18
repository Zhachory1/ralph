"""Exercise the provider-only Hermes workers with synthetic responses and no network access."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "nousresearch/hermes-agent@sha256:36099926da316742423544e055d4734777105ffe6341873af5459b957d0c2919"
MODELS = {
    "architecture_scale": "anthropic/claude-sonnet-5.5",
    "security_blast_radius": "google/gemini-3.8-flash",
    "scope_pragmatism": "openai/gpt-6.2-sol",
}

# The container-local endpoint is injected only into this test's exec environment.
PROBE = r'''
import json, os, pathlib, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

fixture = json.load(sys.stdin)
expected_models = {
    "architecture_scale": "anthropic/claude-sonnet-5.5",
    "security_blast_radius": "google/gemini-3.8-flash",
    "scope_pragmatism": "openai/gpt-6.2-sol",
}
is_judge = "role" in fixture["job"]
if is_judge:
    assert fixture["job"]["model"] == expected_models[fixture["job"]["role"]]
    output = json.dumps({"judge_role": fixture["job"]["role"], "verdict": "PASS", "confidence": 0.9,
                         "blocking_issues": [], "non_blocking_notes": []})
else:
    for kind in ("HTTP", "HTTPS", "FTP", "ALL", "NO"):
        for name in (kind + "_PROXY", (kind + "_PROXY").lower()):
            assert os.environ.get(name, "") == "", name
    assert fixture["job"]["model"] == "test/model"
    output = "# Revised RFC" if "feedback" in fixture["job"] else "# Draft RFC"
calls = []
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if not self.path.endswith("/chat/completions"):
            self.send_response(404); self.end_headers(); return
        calls.append(request)
        chunk = {"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": request["model"],
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": output}, "finish_reason": None}]}
        end = {"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": request["model"],
               "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        body = ("data: " + json.dumps(chunk) + "\n\ndata: " + json.dumps(end) + "\n\ndata: [DONE]\n\n").encode()
        self.send_response(200); self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
patch = """import os
_exec = os.execvpe
def fake_endpoint(file, args, env):
    assert set(env) == {'HOME', 'HERMES_HOME', 'PATH', 'OPENROUTER_API_KEY'}
    env['OPENROUTER_BASE_URL'] = %r
    _exec(file, args, env)
os.execvpe = fake_endpoint
""" % ("http://127.0.0.1:%d/v1" % server.server_port)
result = subprocess.run([sys.executable, "-c", patch + fixture["worker"]], input=json.dumps(fixture["job"]),
                        capture_output=True, text=True, timeout=75)
server.shutdown()
if result.returncode != 0:
    raise RuntimeError("Hermes synthetic worker failed: " + result.stderr[-300:])
assert result.stdout.strip() == output
assert calls and all(call.get("model") == fixture["job"]["model"] and not call.get("tools") for call in calls)
assert any(json.dumps(fixture["job"], ensure_ascii=True) in str(message.get("content", ""))
           for call in calls for message in call.get("messages", []))
home = "council-judge" if is_judge else "ralph-author"
config = (pathlib.Path("/tmp") / home / "config.yaml").read_text()
denied = config.split("disabled_toolsets: [", 1)[1].split("]", 1)[0].split(", ")
assert all(name in denied for name in ("terminal", "file", "kanban", "delegation", "code_execution", "web", "browser"))
print(result.stdout.strip())
'''

for role, model in MODELS.items():
    fixture = {"worker": (ROOT / "council-judge-worker.py").read_text(), "job": {
        "role": role, "model": model, "artifact": "synthetic artifact", "rubric": "synthetic rubric"}}
    if role == "architecture_scale":
        fixture["job"]["artifact"] = "\x01" * (16 * 1024)
        fixture["job"]["rubric"] = (ROOT.parents[1] / "profiles/council/rubric-v1.md").read_text()
    command = ["docker", "run", "--rm", "--pull=never", "--init", "-i", "--network", "none", "--read-only",
               "--tmpfs", "/tmp:rw,nosuid,size=256m", "--user", "10000:10000", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--pids-limit", "128", "--memory", "1g",
               "-e", "OPENROUTER_API_KEY=synthetic-provider", "-e", "GH_TOKEN=synthetic-github",
               "-e", "VIKUNJA_API_TOKEN=synthetic-board", "--entrypoint", "python3", IMAGE, "-c", PROBE]
    result = subprocess.run(command, input=json.dumps(fixture), text=True, capture_output=True, timeout=100)
    if result.returncode:
        raise RuntimeError("Docker synthetic test failed: " + result.stderr[-500:])
    print("Hermes synthetic judge passed:", role)

# Exercise the real Node launcher and Docker flags, replacing only the worker's
# provider endpoint with the in-container fixture and disabling external network.
with tempfile.TemporaryDirectory() as directory:
    worker = (ROOT / "council-judge-worker.py").read_text()
    probe = PROBE.replace('fixture = json.load(sys.stdin)',
                          'fixture = {"job": json.load(sys.stdin), "worker": ' + repr(worker) + '}')
    docker = Path(directory) / "docker"
    docker.write_text("#!/usr/bin/env python3\nimport os, sys\nargs = sys.argv[1:]\n"
                      "if args[0] == 'run':\n    args.insert(1, '--network=none')\n"
                      "    args[-1] = " + repr(probe) + "\n"
                      "os.execv(" + repr(shutil.which("docker")) + ", [" + repr(shutil.which("docker")) + "] + args)\n")
    docker.chmod(0o700)
    env = {**os.environ, "PATH": directory + os.pathsep + os.environ["PATH"],
           "OPENROUTER_API_KEY": "synthetic-provider", "GH_TOKEN": "synthetic-github",
           "RALPH_COUNCIL_RUN_ID": "a" * 64}
    script = ('import { invokeCouncilJudge } from ' + json.dumps((ROOT / "council-judge.mjs").as_uri()) + '; '
              'const result = await invokeCouncilJudge({judge_role:"architecture_scale", '
              'artifact:"synthetic artifact", rubric:"synthetic rubric"}); '
              'if (result.judge_role !== "architecture_scale") throw new Error("wrong role");')
    result = subprocess.run(["node", "--input-type=module", "-e", script], env=env, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError("Real judge launcher synthetic test failed: " + result.stderr[-500:])
    print("Real Docker judge launcher passed")
    script = ('import { runV1Council } from ' + json.dumps((ROOT / "council-v1.mjs").as_uri()) + '; '
              'const result = await runV1Council({artifact:"synthetic panel artifact", revisions:0}); '
              'if (result.rubricVersion !== "v1" || result.decision !== "advance" '
              '|| result.verdicts.length !== 3) throw new Error("invalid panel result");')
    result = subprocess.run(["node", "--input-type=module", "-e", script], env=env, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError("Real Council panel synthetic test failed: " + result.stderr[-500:])
    print("Real Docker three-judge panel passed")
    author_worker = (ROOT / "author-worker.py").read_text()
    author_probe = PROBE.replace('fixture = json.load(sys.stdin)',
                                  'fixture = {"job": json.load(sys.stdin), "worker": ' + repr(author_worker) + '}')
    docker.write_text(docker.read_text().replace(repr(probe), repr(author_probe)))
    config = Path(directory) / "docker-config"
    config.mkdir()
    (config / "config.json").write_text(json.dumps({"proxies": {"default": {
        kind: "http://fixture:fixture@example.invalid:8080"
        for kind in ("httpProxy", "httpsProxy", "ftpProxy", "allProxy", "noProxy")
    }}}))
    docker_host = os.environ.get("DOCKER_HOST") if not os.environ.get("DOCKER_CONTEXT") else None
    if not docker_host:
        docker_host = subprocess.check_output(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"], text=True).strip()
    env.pop("DOCKER_CONTEXT", None)
    env.update(RALPH_DOCS_GITHUB_TOKEN="synthetic-docs", VIKUNJA_API_TOKEN="synthetic-board",
               RALPH_COUNCIL_MCP_TOKEN="synthetic-council", RALPH_COUNCIL_JOB_TOKEN="synthetic-job",
               DOCKER_HOST=docker_host, DOCKER_CONFIG=str(config))
    script = ('import { draftRfc } from ' + json.dumps((ROOT / "author.mjs").as_uri()) + '; '
              'const request = {brief:"Synthetic brief",model:"test/model"}; '
              'const draft = await draftRfc(request); if (draft.trim() !== "# Draft RFC") throw Error("draft"); '
              'const revision = await draftRfc({...request,currentDraft:draft,feedback:"Synthetic revision"}); '
              'if (revision.trim() !== "# Revised RFC") throw Error("revision");')
    result = subprocess.run(["node", "--input-type=module", "-e", script], env=env,
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError("Real Docker author synthetic test failed: " + result.stderr[-500:])
    print("Real Docker author draft and revision passed")

# Exercise the same in-container watchdog without waiting for a real judge.
watchdog = subprocess.run(["docker", "run", "--rm", "--pull=never", "--init", "--network", "none", "--read-only",
                           "--user", "10000:10000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                           "--entrypoint", "/usr/bin/timeout", IMAGE, "--kill-after=1s", "1s",
                           "sh", "-c", "exec sleep 30"], capture_output=True, timeout=20)
assert watchdog.returncode == 124
print("Docker watchdog fixture passed")

# The launcher uses only the ID Docker wrote for its own disposable container.
with tempfile.TemporaryDirectory() as directory:
    cidfile = str(Path(directory) / "cid")
    run = subprocess.run(["docker", "run", "--rm", "--pull=never", "-d", "--network", "none", "--cidfile", cidfile,
                          "--entrypoint", "sh", IMAGE, "-c", "exec sleep 60"], text=True, capture_output=True, timeout=20)
    if run.returncode:
        raise RuntimeError("Could not start disposable cleanup fixture")
    container_id = Path(cidfile).read_text().strip()
    try:
        assert container_id == run.stdout.strip() and len(container_id) == 64
        subprocess.run(["docker", "rm", "-f", container_id], check=True, capture_output=True, timeout=20)
        assert subprocess.run(["docker", "inspect", container_id], capture_output=True, timeout=20).returncode != 0
        print("Docker cleanup fixture passed")
    finally:
        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, timeout=20)
