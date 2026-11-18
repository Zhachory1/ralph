"""Exercise the manual v4 canary with disposable services and no provider credentials."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


V4 = Path(__file__).resolve().parents[1]
POSTGRES = "pgvector/pgvector:pg17"
VIKUNJA = "vikunja/vikunja:0.24.6"
DOCKER = shutil.which("docker")


def command(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=20, **kwargs).strip()


@contextmanager
def container(*options):
    container_id = command([DOCKER, "run", "--rm", "-d", "--pull=never", *options])
    try:
        yield container_id
    finally:
        command([DOCKER, "stop", container_id])


def database(container_id):
    def sql(statement):
        return command([DOCKER, "exec", "-i", container_id, "psql", "-XAtq", "-v", "ON_ERROR_STOP=1",
                        "-U", "postgres", "-d", "agent_memory"], input=statement)

    command([DOCKER, "exec", container_id, "sh", "-ec",
             "for i in $(seq 1 30); do pg_isready -q -h 127.0.0.1 -U postgres && exit 0; sleep .2; done; exit 1"])
    for path in sorted((V4 / "init-db").glob("*.sql")):
        sql(path.read_text())
    return sql


def board(container_id):
    origin = "http://" + command([DOCKER, "port", container_id, "3456/tcp"])
    token = None

    def api(path, method="GET", payload=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(origin + "/api/v1" + path, headers=headers, method=method,
                          data=None if payload is None else json.dumps(payload).encode())
        with urlopen(request, timeout=3) as response:
            return json.load(response)

    for _ in range(25):
        try:
            api("/info")
            break
        except URLError:
            time.sleep(0.15)
    else:
        raise RuntimeError("Vikunja fixture did not start")
    login = {"username": "canary-fixture", "password": secrets.token_hex(16)}
    api("/register", "POST", {**login, "email": "fixture@example.invalid"})
    token = api("/login", "POST", login)["token"]
    project = api("/projects", "PUT", {"title": "Fixture"})["id"]
    view = next(item for item in api(f"/projects/{project}/views") if item["view_kind"] == "kanban")
    route = f"/projects/{project}/views/{view['id']}"
    ready, director, done = [api(route + "/buckets", "PUT", {"title": title})["id"]
                             for title in ("Ready", "Director PR Merge", "Done")]
    api(route, "POST", {**view, "done_bucket_id": done})
    dependency = api(f"/projects/{project}/tasks", "PUT", {"title": "Dependency"})["id"]
    ticket = api(f"/projects/{project}/tasks", "PUT", {"title": "Canary"})["id"]
    api(f"/tasks/{ticket}/relations", "PUT", {"relation_kind": "blocked", "other_task_id": dependency})

    def move(bucket):
        api(f"{route}/buckets/{bucket}/tasks", "POST", {"task_id": ticket, "bucket_id": bucket})

    move(ready)
    return (dict(origin=origin, projectId=project, viewId=view["id"], readyBucketId=ready,
                 directorBucketId=director, doneBucketId=done), token, ticket, dependency, api, move)


def exercise(pg, vk):
    sql = database(pg)
    board_config, token, ticket, dependency, api, move = board(vk)
    repo = "fixture/project"
    branch = f"ralph/vikunja-{ticket}"
    head = "a" * 40
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)

        def executable(name, body):
            path = root / name
            path.write_text(body)
            path.chmod(0o700)
            return path

        executable("docker", f"#!/bin/sh\nshift 6\nexec {shlex.quote(DOCKER)} exec -i {shlex.quote(pg)} \"$@\"\n")
        executable("gh", '''#!/bin/sh
test -z "${VIKUNJA_API_TOKEN+x}${VIKUNJA_DONE_TOKEN+x}" || exit 2
case "$1" in
  pr) if [ -f "$FIXTURE_DRAFT" ]; then cat "$FIXTURE_DRAFT"; else echo '[]'; fi ;;
  api) test "$4" = --method && test "$5" = GET && cat "$FIXTURE_MERGE" ;;
  *) exit 3 ;;
esac
''')
        worker = executable("swe", f'''#!{sys.executable}
import json, os, pathlib, sys
assert not any(key in os.environ for key in ('VIKUNJA_API_TOKEN', 'VIKUNJA_DONE_TOKEN', 'GH_TOKEN'))
payload = json.loads(sys.argv[1])
assert payload['branch'] == {branch!r} and payload['rfc_path'] == '/docs/accepted.md'
with open(os.environ['FIXTURE_RUNS'], 'a') as calls:
    calls.write('attempt\\n')
pathlib.Path(os.environ['FIXTURE_DRAFT']).write_text(json.dumps([dict(number=7, headRefName=payload['branch'],
    headRefOid={head!r}, baseRefName='main', author={{'login':'fixture-bot'}}, isDraft=True, isCrossRepository=False)]))
''')
        config = root / "config.json"
        config.write_text(json.dumps(dict(board=board_config, directors=["fixture-director"], sweCommand=[str(worker)],
            jobs={str(ticket): dict(repo=repo, baseBranch="main", expectedAuthor="fixture-bot", repoPath="/work/project",
                                    product="fixture", rfcPath="/docs/accepted.md", acceptanceCriteria=["Check formatting"])})))
        evidence = root / "merge.json"
        env = {**os.environ, "PATH": f"{root}{os.pathsep}{os.environ['PATH']}", "RALPH_READY_CONFIG": str(config),
               "VIKUNJA_API_TOKEN": token, "VIKUNJA_DONE_TOKEN": token, "GH_TOKEN": "synthetic-fixture",
               "FIXTURE_DRAFT": str(root / "draft.json"), "FIXTURE_RUNS": str(root / "calls"), "FIXTURE_MERGE": str(evidence)}

        def run(script):
            return json.loads(command(["node", str(V4 / script), str(ticket)], env=env))["status"]

        assert run("run-ready-ticket.mjs") == "not_ready"
        assert sql("SELECT count(*) FROM ready_dispatches;") == "0"
        api(f"/tasks/{dependency}", "POST", {**api(f"/tasks/{dependency}"), "done": True})
        assert run("run-ready-ticket.mjs") == "waiting_merge"
        assert run("run-ready-ticket.mjs") == "already_claimed"
        sql("""UPDATE ready_dispatches SET status='running', lease_token=gen_random_uuid(),
            expires_at=clock_timestamp()-INTERVAL '1 second', repo=NULL, pr_number=NULL, head_sha=NULL;""")
        assert run("run-ready-ticket.mjs") == "waiting_merge"
        assert (root / "calls").read_text() == "attempt\n"

        def merge_proof(sha, actor):
            repository = {"full_name": repo}
            evidence.write_text(json.dumps(dict(number=7, state="closed", merged=True,
                merged_at="2026-11-18T01:00:00Z", merge_commit_sha="b" * 40, user={"login": "fixture-bot"},
                merged_by={"login": actor, "type": "User"}, head=dict(sha=sha, ref=branch, repo=repository),
                base=dict(ref="main", repo=repository))))

        merge_proof("c" * 40, "fixture-director")
        assert run("run-ready-merge.mjs") == "stale_head"
        merge_proof(head, "fixture-bot")
        assert run("run-ready-merge.mjs") == "unauthorized_actor"
        assert sql("SELECT count(*) FROM ready_merge_receipts;") == "0"
        merge_proof(head, "fixture-director")
        assert run("run-ready-merge.mjs") == "not_at_director_gate"
        move(board_config["directorBucketId"])
        assert run("run-ready-merge.mjs") == "done"
        assert api(f"/tasks/{ticket}")["done"] is True
        assert run("run-ready-merge.mjs") == "already_done"
        assert sql("SELECT board_done_at IS NOT NULL FROM ready_merge_receipts;") == "t"


if __name__ == "__main__":
    if not DOCKER:
        sys.exit("Docker is required for this fixture")
    with container("--network", "none", "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "-e", "POSTGRES_USER=postgres",
                   "-e", "POSTGRES_DB=agent_memory", POSTGRES) as pg:
        with container("-p", "127.0.0.1::3456", "--tmpfs", "/db:rw,mode=1777", "-e", "VIKUNJA_DATABASE_PATH=/db/fixture.db",
                       "-e", "VIKUNJA_SERVICE_ENABLEREGISTRATION=true", VIKUNJA) as vk:
            exercise(pg, vk)
    print("Ready canary fixture passed (real Vikunja/Postgres, synthetic SWE/GitHub)")
