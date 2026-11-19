"""Check Paperclip board key authority and rotation on a disposable authenticated board."""

import json
import runpy
import secrets
import subprocess
from pathlib import Path
from urllib.parse import urlencode

FIXTURE = runpy.run_path(str(Path(__file__).with_name("paperclip-rfc-canary.py")))
ORIGIN = FIXTURE["ORIGIN"]


def bearer(container, token, path, *, body=None):
    args = [] if body is None else ["--json", json.dumps(body)]
    # Pass the header through stdin, not Docker arguments or output.
    result = subprocess.run(
        ["docker", "exec", "-i", container, "curl", "-sS", "--max-time", "10",
         "-H", "@-", "-H", f"Origin: {ORIGIN}", "-w", "\n%{http_code}", *args, f"{ORIGIN}/api/{path}"],
        input=f"Authorization: Bearer {token}\n", text=True, capture_output=True, check=True, timeout=15,
    )
    raw, status = result.stdout.rsplit("\n", 1)
    return int(status), json.loads(raw)


def check_rotation(container):
    def board(path, body=None):
        return FIXTURE["request"](container, "key-rotation", path, body)[1]

    board("auth/sign-up/email", {"name": "Key fixture", "email": "keys@fixture.invalid",
                                "password": secrets.token_hex(16)})
    board("bootstrap/claim", {})
    sandbox = board("companies", {"name": "Sandbox fixture"})
    other = board("companies", {"name": "Other fixture"})

    def operator(path, body=None):
        return FIXTURE["request"](container, "sandbox-operator", path, body)[1]

    operator("auth/sign-up/email", {"name": "Sandbox operator", "email": "operator@fixture.invalid",
                                   "password": secrets.token_hex(16)})
    invite = board(f"companies/{sandbox['id']}/invites", {"allowedJoinTypes": "human", "humanRole": "operator"})
    assert operator(f"invites/{invite['token']}/accept", {"requestType": "human"})["status"] == "approved"

    def authorize(actor, name):
        challenge = actor("cli-auth/challenges", {"command": "Verify synthetic credential rotation",
            "clientName": name, "requestedAccess": "board", "requestedCompanyId": sandbox["id"]})
        path = f"cli-auth/challenges/{challenge['id']}"
        approval = actor(f"{path}/approve", {"token": challenge["token"]})
        assert approval["approved"]
        poll = f"{path}?" + urlencode({"token": challenge["token"]})
        assert actor(poll)["status"] == "approved"
        return challenge["boardApiToken"]

    target = f"companies/{sandbox['id']}"
    other_path = f"companies/{other['id']}"
    admin = authorize(board, "Admin fixture key")
    assert bearer(container, admin, other_path)[0] == 200
    assert bearer(container, admin, "cli-auth/revoke-current", body={})[0] == 200
    print("PASS: requesting a company does not restrict an admin's board key", flush=True)

    old = authorize(operator, "Old fixture key")
    replacement = authorize(operator, "Replacement fixture key")
    assert bearer(container, old, target)[0] == 200
    assert bearer(container, replacement, target)[0] == 200
    assert bearer(container, old, other_path)[0] in (403, 404)
    assert bearer(container, replacement, other_path)[0] in (403, 404)
    print("PASS: sandbox-only operator keys cannot read the other company", flush=True)
    assert bearer(container, old, "cli-auth/revoke-current", body={})[0] == 200
    assert bearer(container, old, target)[0] in (401, 403)
    assert bearer(container, replacement, target)[0] == 200
    assert bearer(container, replacement, "cli-auth/revoke-current", body={})[0] == 200
    assert bearer(container, replacement, target)[0] in (401, 403)
    print("PASS: revoked key rejected; replacement remained usable until revoked", flush=True)


if __name__ == "__main__":
    with FIXTURE["authenticated_fixture"]("board-key") as fixture:
        check_rotation(fixture)
