"""Exercise Chat ingress through a disposable native Paperclip Routine."""
import json
import runpy
import secrets
import select
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
fixture = runpy.run_path(str(HERE / "paperclip-rfc-canary.py"))
request = fixture["request"]

AGENT = r"""
set -eu
pc() { curl -fsS --max-time 10 -H "Authorization: Bearer $PAPERCLIP_API_KEY" -H "X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID" "$@"; }
task=$(pc "$PAPERCLIP_API_URL/api/heartbeat-runs/$PAPERCLIP_RUN_ID" | jq -er '.issueId')
pc --json '{"status":"done","comment":"Chat fixture received."}' -X PATCH "$PAPERCLIP_API_URL/api/issues/$task" >/dev/null
"""

BRIDGE = r"""
import json, runpy, sys
config = json.loads(sys.stdin.readline())
module = runpy.run_path("/tmp/chat-ingress.py")
def verify(token, audience):
    if token != "fixture-valid" or audience != "https://chat.fixture.invalid/chat":
        raise ValueError("invalid fixture token")
    return {"email": "chat@system.gserviceaccount.com", "email_verified": True}
handler = module["make_handler"](
    audience="https://chat.fixture.invalid/chat", verify_token=verify,
    dispatch=module["make_dispatch"]("http://127.0.0.1:3100", config["company"],
                                     config["routine"], "spaces/fixture", config["token"]),
)
server = module["ChatHTTPServer"](("127.0.0.1", 18765), handler)
print("ready", flush=True)
server.serve_forever()
"""


def wait_for(description, read, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError(f"Timed out waiting for {description}")


def check_ingress(container):
    def board(path, body=None, method="POST"):
        return request(container, "director", path, body, method)[1]

    board("auth/sign-up/email", {"name": "director", "email": "director@fixture.invalid",
                                 "password": secrets.token_hex(16)})
    board("bootstrap/claim", {})
    company = board("companies", {"name": "Chat ingress fixture"})
    root = f"companies/{company['id']}"
    key = board("board-api-keys", {"name": "fixture bridge", "requestedCompanyId": company["id"]})["token"]
    agent = board(f"{root}/agents", {"name": "Comms", "adapterType": "process",
                                     "adapterConfig": {"command": "/bin/sh", "args": ["-ec", AGENT]}})
    wait_for("Comms ready", lambda: board(f"agents/{agent['id']}").get("lifecycleState") == "ready")
    routine = board(f"{root}/routines", {"title": "Chat intake", "assigneeAgentId": agent["id"],
                                        "concurrencyPolicy": "always_enqueue"})
    for path in (ROOT / "chat-ingress.py", ROOT / "comms-intake-worker.mjs"):
        subprocess.run(["docker", "exec", "-i", "-u", "node", container, "sh", "-ec",
                        'cat > "$1"', "fixture", f"/tmp/{path.name}"],
                       input=path.read_bytes(), check=True, timeout=10)
    process = subprocess.Popen(
        ["docker", "exec", "-i", "-u", "node", container, "python3", "-c", BRIDGE],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        process.stdin.write(json.dumps({"company": company["id"], "routine": routine["id"], "token": key}) + "\n")
        process.stdin.close()
        readable, _, _ = select.select([process.stdout], [], [], 15)
        assert readable and process.stdout.readline().strip() == "ready", "Chat fixture failed to start"

        event = {
            "type": "MESSAGE", "space": {"name": "spaces/fixture"},
            "message": {"name": "spaces/fixture/messages/message-1",
                        "thread": {"name": "spaces/fixture/threads/thread-1"},
                        "text": "Please help.", "sender": {"type": "HUMAN"}},
        }

        def send(payload, token):
            result = subprocess.run(
                ["docker", "exec", "-i", container, "curl", "-sS", "--max-time", "15",
                 "-o", "/dev/null", "-w", "%{http_code}", "-H", f"Authorization: Bearer {token}",
                 "--json", "@-", "http://127.0.0.1:18765/chat"],
                input=json.dumps(payload), text=True, capture_output=True, timeout=20, check=True,
            )
            return int(result.stdout)

        assert send(event, "fixture-invalid") == 401
        other_space = {
            **event, "space": {"name": "spaces/other"},
            "message": {**event["message"], "name": "spaces/other/messages/message-1",
                        "thread": {"name": "spaces/other/threads/thread-1"}},
        }
        assert send(other_space, "fixture-valid") == 200
        malformed = {**event, "message": {**event["message"], "name": "not-a-message-name"}}
        assert send(malformed, "fixture-valid") == 400
        assert board(f"routines/{routine['id']}/runs") == []
        assert send(event, "fixture-valid") == 200
        runs = board(f"routines/{routine['id']}/runs")
        assert len(runs) == 1 and runs[0]["source"] == "api"
        task = runs[0]["linkedIssueId"]

        def receipt():
            return next((comment for comment in board(f"issues/{task}/comments")
                         if comment.get("authorAgentId") == agent["id"]
                         and comment.get("body") == "Chat fixture received."), None)

        comment = wait_for("native Comms run", receipt)
        assert any(row["runId"] == comment["createdByRunId"] and row["agentId"] == agent["id"]
                   for row in board(f"issues/{task}/runs"))
        assert send(event, "fixture-valid") == 200
        assert len(board(f"routines/{routine['id']}/runs")) == 1
        print("PASS: mock-verified Chat intake dispatched once to the native Comms actor; denied/replayed events did not duplicate it")
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    with fixture["authenticated_fixture"]("chat-ingress") as container:
        check_ingress(container)
