"""Verify localhost bootstrap and persistence on a disposable Compose project."""

import argparse
import http.cookiejar
import json
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4

COMPOSE = Path(__file__).resolve().parents[1] / "paperclip-private.yml"


def check_private_bootstrap(*, postgres=False):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    project = f"ralph-private-canary-{uuid4().hex[:10]}"
    env = {key: os.environ[key] for key in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")
           if key in os.environ}
    env.update(PAPERCLIP_PORT=str(port), PAPERCLIP_AUTH_DISABLE_SIGN_UP="false",
               PAPERCLIP_AGENT_JWT_SECRET=secrets.token_hex(32))
    command = ["docker", "compose", "--env-file", os.devnull, "-p", project, "-f", str(COMPOSE)]
    if postgres:
        command.extend(("-f", str(COMPOSE.with_name("paperclip-postgres.yml"))))
        env.update(PAPERCLIP_PUBLIC_URL=origin, PAPERCLIP_POSTGRES_PASSWORD=secrets.token_hex(32))

    def compose(*args):
        return subprocess.check_output([*command, *args], text=True, env=env, timeout=40).strip()

    def client():
        opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def request(path, body=None, *, request_origin=origin):
            req = Request(f"{origin}/api/{path}", data=None if body is None else json.dumps(body).encode(),
                          headers={"Origin": request_origin, "Content-Type": "application/json"})
            with opener.open(req, timeout=4) as reply:
                return json.load(reply)

        return request

    def wait_for(status):
        anonymous = client()
        for _ in range(30):
            try:
                if anonymous("health")["bootstrapStatus"] == status:
                    return
            except OSError:
                pass
            time.sleep(1)
        raise AssertionError(f"Expected bootstrap state: {status}")

    def denied(call, *codes):
        try:
            call()
        except HTTPError as error:
            assert error.code in codes, error.code
        else:
            raise AssertionError("Expected Paperclip to deny the request")

    compose("config", "--quiet")
    try:
        services = {"paperclip", "postgres"} if postgres else {"paperclip"}
        assert set(compose("config", "--services").splitlines()) == services
        compose("up", "-d", "--no-build", "--pull", "never")
        cid = compose("ps", "-q", "paperclip")
        state = json.loads(subprocess.check_output(["docker", "inspect", cid], env=env, text=True, timeout=10))[0]
        published = {name: value for name, value in state["NetworkSettings"]["Ports"].items() if value}
        assert published == {f"{port}/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}]}, published
        assert len(state["Mounts"]) == 1 and state["Mounts"][0]["Type"] == "volume"
        env_names = {entry.split("=", 1)[0] for entry in state["Config"]["Env"]}
        assert not env_names.intersection({"OPENROUTER_API_KEY", "GH_TOKEN", "GITHUB_TOKEN"})
        wait_for("bootstrap_pending")
        denied(lambda: client()("cli-auth/me"), 401, 403)
        account = {"email": "bootstrap@fixture.invalid", "password": secrets.token_hex(16)}
        denied(lambda: client()("auth/sign-up/email", {**account, "name": "Fixture"},
                                request_origin="http://untrusted.fixture.invalid"), 403)
        board = client()
        board("auth/sign-up/email", {**account, "name": "Fixture"})
        board("bootstrap/claim", {})
        company = board("companies", {"name": "Private bootstrap fixture"})
        assert board("cli-auth/me")["isInstanceAdmin"]

        env["PAPERCLIP_AUTH_DISABLE_SIGN_UP"] = "true"
        compose("up", "-d", "--no-build", "--pull", "never", "--force-recreate")
        wait_for("ready")
        denied(lambda: client()("auth/sign-up/email", {**account, "name": "Fixture", "email": "new@fixture.invalid"}),
               400, 403)
        restored = client()
        restored("auth/sign-in/email", account)
        assert restored(f"companies/{company['id']}")["id"] == company["id"]
        if postgres:
            assert compose("exec", "-T", "postgres", "psql", "-U", "paperclip", "-d", "paperclip",
                           "-Atc", "SELECT id FROM companies") == company["id"]
            db = json.loads(compose("ps", "--format", "json", "postgres"))
            assert all(port["PublishedPort"] == 0 for port in db.get("Publishers") or [])
        print("PASS: localhost-only bootstrap, auth and origin checks, sign-up closed, account and board persisted")
    finally:
        compose("down", "--volumes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres", action="store_true", help="Use the external PostgreSQL overlay.")
    check_private_bootstrap(postgres=parser.parse_args().postgres)
