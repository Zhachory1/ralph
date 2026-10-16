"""Talk to a Kanban board through the Hermes CLI, for tests.

Deliberately a thin shell wrapper rather than a library import: the tests
exercise the same surface the notifier and the profiles use, so a change in
CLI behaviour fails a test instead of surprising a live run.
"""

from __future__ import annotations

import json
import subprocess


class Board:
    def __init__(self, slug: str, binary: str = "hermes"):
        self.slug = slug
        self.binary = binary

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.binary, "kanban", "--board", self.slug, *args],
            capture_output=True,
            text=True,
            check=check,
        )

    def json(self, *args: str):
        out = self.run(*args, "--json").stdout.strip()
        return json.loads(out) if out else []

    # --- lifecycle -------------------------------------------------------

    def create(self, title: str, **flags) -> str:
        args = ["create", title]
        for key, value in flags.items():
            if value is None:
                continue
            args += [f"--{key.replace('_', '-')}", str(value)]
        return self.json(*args)["id"]

    def status(self, task_id: str) -> str:
        return self.show(task_id)["task"]["status"]

    def show(self, task_id: str) -> dict:
        return self.json("show", task_id)

    def events(self, task_id: str) -> list[str]:
        return [e["kind"] for e in self.show(task_id).get("events", [])]

    def comments(self, task_id: str) -> list[str]:
        return [c["body"] for c in self.show(task_id).get("comments", [])]

    # --- transitions -----------------------------------------------------

    def block(self, task_id: str, reason: str):
        self.run("block", task_id, reason)

    def unblock(self, task_id: str):
        self.run("unblock", task_id)

    def comment(self, task_id: str, body: str):
        self.run("comment", task_id, body)

    def request_review(self, task_id: str, summary: str, reviewer: str | None = None):
        args = ["request-review", task_id, "--summary", summary]
        if reviewer:
            args += ["--reviewer", reviewer]
        self.run(*args)

    def reopen_review(self, task_id: str, reason: str):
        self.run("reopen-review", task_id, "--reason", reason)

    def assign(self, task_id: str, profile: str):
        self.run("assign", task_id, profile)

    def complete(self, task_id: str, summary: str | None = None, check: bool = True):
        args = ["complete", task_id]
        if summary:
            args += ["--summary", summary]
        return self.run(*args, check=check)

    def link(self, parent: str, child: str):
        self.run("link", parent, child)

    def archive(self, task_id: str):
        self.run("archive", task_id, check=False)

    # --- dispatch --------------------------------------------------------

    def dispatch_plan(self) -> dict:
        """What the dispatcher would do. Never spawns a worker, never calls a model."""
        return self.json("dispatch", "--dry-run")

    def would_spawn(self, task_id: str) -> bool:
        return task_id in self.dispatch_plan().get("spawned", [])

    def guard_reason(self, task_id: str) -> str | None:
        for entry in self.dispatch_plan().get("respawn_guarded", []):
            if entry.get("task_id") == task_id:
                return entry.get("reason")
        return None
