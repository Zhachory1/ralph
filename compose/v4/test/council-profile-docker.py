"""Exercise the isolated Hermes Council profile with synthetic local endpoints."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "nousresearch/hermes-agent@sha256:36099926da316742423544e055d4734777105ffe6341873af5459b957d0c2919"

PROBE = r'''
import json, os, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

worker = sys.stdin.read()
token = os.environ["RALPH_COUNCIL_JOB_TOKEN"]
calls = []
requests = []
mcp_methods = []
steps = []
verdicts = [{"judge_role": role, "verdict": "PASS", "confidence": 0.9,
             "blocking_issues": [], "non_blocking_notes": []}
            for role in ("architecture_scale", "security_blast_radius", "scope_pragmatism")]
class MCP(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        assert self.headers.get("Authorization") == "Bearer " + token
        message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method = message.get("method")
        mcp_methods.append(method)
        if method == "notifications/initialized":
            self.send_response(202); self.end_headers(); return
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "fixture", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "review_artifact", "description": "Review a registered artifact",
                      "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]}
        elif method == "tools/call":
            calls.append(message["params"])
            result = {"content": [{"type": "text", "text": json.dumps(
                {"rubricVersion": "v1", "decision": "advance", "verdicts": verdicts})}]}
        else:
            result = {}
        body = json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "result": result}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        self.send_response(405); self.end_headers()

if os.environ.get("COUNCIL_FIXTURE_MCP_PORT"):
    class HostMCP:
        server_port = int(os.environ["COUNCIL_FIXTURE_MCP_PORT"])
        def shutdown(self): pass
    mcp = HostMCP()
else:
    mcp = HTTPServer(("127.0.0.1", 0), MCP)
    threading.Thread(target=mcp.serve_forever, daemon=True).start()

class Model(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        requests.append(request)
        if not request.get("stream"):
            body = json.dumps({"id": "fixture", "object": "chat.completion", "created": 1,
                               "model": request.get("model", "test/model"),
                               "choices": [{"index": 0, "message": {"role": "assistant", "content": "{}"},
                                            "finish_reason": "stop"}]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        tools = [entry["function"]["name"] for entry in request.get("tools", [])]
        if len(steps) < 2:
            name = "tool_describe" if not steps else "tool_call"
            assert name in tools, tools
            arguments = ({"names": ["mcp__council__review_artifact"]} if not steps else {
                "calls": [{"name": "mcp__council__review_artifact",
                           "arguments": {}}]})
            delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_fixture", "type": "function",
                     "function": {"name": name, "arguments": json.dumps(arguments)}}]}
            finish = "tool_calls"
            steps.append(name)
        else:
            delta = {"role": "assistant", "content": "Synthetic Council completed"}
            finish = "stop"
        chunk = {"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": request["model"],
                 "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
        end = {"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": request["model"],
               "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
        body = ("data: " + json.dumps(chunk) + "\n\ndata: " + json.dumps(end) + "\n\ndata: [DONE]\n\n").encode()
        self.send_response(200); self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

model = HTTPServer(("127.0.0.1", 0), Model)
threading.Thread(target=model.serve_forever, daemon=True).start()
patch = """import os
_exec = os.execvpe
def fake_endpoint(file, args, env):
    assert not any(k in env for k in ('GH_TOKEN', 'GITHUB_TOKEN', 'VIKUNJA_API_TOKEN', 'RALPH_COUNCIL_MCP_TOKEN'))
    env['OPENROUTER_BASE_URL'] = %r
    _exec(file, args, env)
os.execvpe = fake_endpoint
""" % ("http://127.0.0.1:%d/v1" % model.server_port)
job = {"model": "test/model", "port": mcp.server_port}
result = subprocess.run([sys.executable, "-c", patch + worker], input=json.dumps(job),
                        capture_output=True, text=True, timeout=110)
model.shutdown(); mcp.shutdown()
if result.returncode:
    raise RuntimeError("Hermes Council fixture failed: " + result.stderr[-2000:])
if not os.environ.get("COUNCIL_FIXTURE_MCP_PORT"):
    assert len(calls) == 1 and calls[0]["name"] == "review_artifact"
    assert calls[0]["arguments"] == {}
assert len(steps) == 2
names = [tool["function"]["name"] for request in requests for tool in request.get("tools", [])]
assert "tool_describe" in names and "tool_call" in names, names
assert set(names) <= {"tool_search", "tool_describe", "tool_call"}, names
assert "Synthetic Council completed" in result.stdout
print("Isolated Hermes Council discovered and called the synthetic review tool")
'''

command = ["docker", "run", "--rm", "--pull=never", "--init", "-i", "--network", "none",
           "--add-host", "host.docker.internal:127.0.0.1", "--read-only",
           "--tmpfs", "/tmp:rw,nosuid,size=256m", "--user", "10000:10000",
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "128", "--memory", "1g",
           "-e", "OPENROUTER_API_KEY=synthetic-provider", "-e", "RALPH_COUNCIL_JOB_TOKEN=" + "a" * 64,
           "-e", "RALPH_COUNCIL_MCP_TOKEN=synthetic-master",
           "-e", "GH_TOKEN=synthetic-github", "-e", "VIKUNJA_API_TOKEN=synthetic-board",
           "--entrypoint", "python3", IMAGE, "-c", PROBE]
result = subprocess.run(command, input=(ROOT / "council-profile-worker.py").read_text(),
                        text=True, capture_output=True, timeout=140)
if result.returncode:
    raise RuntimeError("Council Docker fixture failed: " + result.stderr[-2500:])
print(result.stdout.strip())

# Exercise the actual profile launcher against the real Node MCP service.
# Only the model endpoint and the service's judge Docker calls are synthetic.
with tempfile.TemporaryDirectory(prefix="ralph-council-bridge-") as directory:
    capture = Path(directory) / "calls"
    server_bin = Path(directory) / "server-bin"
    profile_bin = Path(directory) / "profile-bin"
    state_dir = Path(directory) / "state"
    server_bin.mkdir()
    profile_bin.mkdir()
    state_dir.mkdir(mode=0o700)
    docker = server_bin / "docker"
    docker.write_text("""#!/usr/bin/env python3
import json, os, sys
assert not any(k in os.environ for k in ('GH_TOKEN', 'GITHUB_TOKEN', 'VIKUNJA_API_TOKEN',
                                         'RALPH_COUNCIL_MCP_TOKEN', 'RALPH_COUNCIL_JOB_TOKEN'))
job = json.load(sys.stdin)
with open(%r, 'a') as out:
    out.write(json.dumps(job) + '\\n')
print(json.dumps({'judge_role': job['role'], 'verdict': 'PASS', 'confidence': 0.9,
                  'blocking_issues': [], 'non_blocking_notes': []}))
""" % str(capture))
    docker.chmod(0o755)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {key: value for key, value in os.environ.items()
           if key in ("PATH", "HOME", "DOCKER_CONTEXT", "DOCKER_HOST", "DOCKER_CONFIG")}
    env.update(PATH=str(server_bin) + os.pathsep + env["PATH"], OPENROUTER_API_KEY="synthetic-provider",
               RALPH_COUNCIL_MCP_TOKEN="a" * 64, RALPH_COUNCIL_MCP_PORT=str(port),
               RALPH_COUNCIL_STATE_DIR=str(state_dir),
               GH_TOKEN="synthetic-github", VIKUNJA_API_TOKEN="synthetic-board")
    server = subprocess.Popen(["node", str(ROOT.parents[1] / "coding-harness" / "council-mcp.mjs")],
                              env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError("Synthetic Council MCP server exited")
                time.sleep(0.05)
        else:
            raise RuntimeError("Synthetic Council MCP server did not start")
        worker = (ROOT / "council-profile-worker.py").read_text()
        probe = PROBE.replace("worker = sys.stdin.read()",
                              "job = json.load(sys.stdin)\nos.environ['COUNCIL_FIXTURE_MCP_PORT'] = str(job['port'])\n"
                              "worker = " + repr(worker))
        probe = probe.replace('job = {"model": "test/model", "port": mcp.server_port}',
                              "job = job")
        real_docker = shutil.which("docker")
        wrapper = profile_bin / "docker"
        wrapper.write_text("#!/usr/bin/env python3\nimport os, sys\nargs = sys.argv[1:]\n"
                           "if args[0] == 'run':\n    args[-1] = " + repr(probe) + "\n"
                           "os.execv(" + repr(real_docker) + ", [" + repr(real_docker) + "] + args)\n")
        wrapper.chmod(0o755)
        runner_env = {**env, "PATH": str(profile_bin) + os.pathsep + os.environ["PATH"]}
        script = ('import { runCouncilProfile } from ' + json.dumps((ROOT / "council-profile.mjs").as_uri()) + '; '
                  'const result = await runCouncilProfile({artifact:"synthetic artifact", revisions:0, '
                  'model:"test/model", port:' + str(port) + '}); '
                  'if (result.result.decision !== "advance") throw new Error("missing panel result"); '
                  'const ack = await fetch("http://127.0.0.1:' + str(port) + '/jobs/" + result.jobId, '
                  '{method:"DELETE", headers:{Authorization:"Bearer " + process.env.RALPH_COUNCIL_MCP_TOKEN}}); '
                  'if (ack.status !== 204) throw new Error("ack failed"); '
                  'const second = await runCouncilProfile({artifact:"\\u0001".repeat(16384), revisions:0, '
                  'model:"test/model", port:' + str(port) + '}); '
                  'if (second.result.decision !== "advance") throw new Error("boundary result missing");')
        result = subprocess.run(["node", "--input-type=module", "-e", script], env=runner_env,
                                text=True, capture_output=True, timeout=150)
        if result.returncode:
            raise RuntimeError("Colima Council MCP bridge failed: " + result.stderr[-2000:])
        jobs = [json.loads(line) for line in capture.read_text().splitlines()]
        assert len(jobs) == 6
        assert all(job["artifact"] == "synthetic artifact" for job in jobs[:3])
        assert all(job["artifact"] == "\x01" * 16384 for job in jobs[3:])
        print("Colima Hermes profile reached the real Council MCP and synthetic panel")
    finally:
        server.terminate()
        server.wait(timeout=10)
