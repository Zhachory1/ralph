"""Verify Chat OIDC handling with the pinned image's real Google library."""
import runpy
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
fixture = runpy.run_path(str(HERE / "paperclip-rfc-canary.py"))
IMAGE = fixture["IMAGE"]
SOURCE = HERE.parent / "chat-ingress.py"

SCRIPT = r"""
import json, sys, time, types
from unittest.mock import patch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import crypt, jwt

module = types.ModuleType("chat_ingress")
module.__file__ = "/tmp/chat-ingress.py"
exec(compile(sys.stdin.read(), module.__file__, "exec"), module.__dict__)

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                       serialization.PublicFormat.SubjectPublicKeyInfo).decode()
signer = crypt.RSASigner.from_string(private, key_id="fixture")
now = int(time.time())
audience = "https://chat.fixture.invalid/chat"
claims = {"iss": "https://accounts.google.com", "aud": audience, "iat": now - 5,
          "exp": now + 300, "email": "chat@system.gserviceaccount.com", "email_verified": True}
event = {"type": "MESSAGE", "space": {"name": "spaces/fixture"},
         "message": {"name": "spaces/fixture/messages/message-1", "text": "Hello",
                     "sender": {"type": "HUMAN"}}}
body = json.dumps(event).encode()
sent = []

def request(url, **kwargs):
    assert url == "https://www.googleapis.com/oauth2/v1/certs"
    assert kwargs["timeout"] == 10
    return types.SimpleNamespace(status=200, data=json.dumps({"fixture": public}).encode())

def check(payload, expected, signing_key=signer):
    token = jwt.encode(signing_key, payload).decode()
    with patch("google.auth.transport.requests.Request", return_value=request):
        status = module.handle_request(authorization="Bearer " + token, body=body,
            audience=audience, verify_token=module.verify_google_token, dispatch=sent.append)
    assert status == expected, (status, expected)

check(claims, 200)
check({**claims, "aud": "https://wrong.fixture.invalid/chat"}, 401)
check({**claims, "iss": "https://wrong.fixture.invalid"}, 401)
check({**claims, "exp": now - 10}, 401)
check({**claims, "email": "other@fixture.invalid"}, 401)
check({**claims, "email_verified": False}, 401)
other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
other_pem = other_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
check(claims, 401, crypt.RSASigner.from_string(other_pem, key_id="fixture"))
assert len(sent) == 1
print("PASS: real Google verifier accepts a signed fixture and rejects wrong signature, audience, issuer, expiry, and identity")
"""


def check_oidc():
    result = subprocess.run(
        ["docker", "run", "--rm", "-i", "--network", "none", "--user", "node",
         "--entrypoint", "/opt/hermes/.venv/bin/python3", IMAGE, "-c", SCRIPT],
        input=SOURCE.read_text(), text=True, capture_output=True, timeout=45,
    )
    if result.returncode:
        raise RuntimeError(f"OIDC canary failed: {result.stderr[-2000:]}")
    print(result.stdout.strip())


if __name__ == "__main__":
    check_oidc()
