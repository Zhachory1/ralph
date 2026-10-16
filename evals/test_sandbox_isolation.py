"""Isolation tests: escape from where the untrusted code actually runs.

The builder runs dependency installs, test suites, and code it just wrote,
from repositories whose READMEs and Makefiles can carry prompt injection.
All of that happens in a sandbox container, so that is where these probe.

An earlier version probed from the hermes container instead, which holds
Docker API access precisely so it can create sandboxes. It "found" a breach
that the builder could never reach. Probe the sandbox.

    python3 evals/test_sandbox_isolation.py
"""

import json
import pathlib
import subprocess
import unittest

COMPOSE = pathlib.Path(__file__).resolve().parent.parent / "compose"
SANDBOX_IMAGE = "nousresearch/hermes-sandbox"


def compose(*args, timeout=120):
    return subprocess.run(
        ["docker", "compose", *args],
        capture_output=True, text=True, cwd=COMPOSE, timeout=timeout,
    )


def docker(*args, timeout=60):
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout,
    )


def terminal_backend():
    result = compose("exec", "-T", "hermes", "sh", "-c", "echo $TERMINAL_ENV", timeout=30)
    return (result.stdout or "").strip() or "local"


def live_sandbox():
    """A running sandbox container, started on demand if none exists."""
    found = docker(
        "ps", "--filter", f"ancestor={SANDBOX_IMAGE}:desktop",
        "--format", "{{.Names}}").stdout.split()
    if found:
        return found[0]

    compose("exec", "-T", "hermes", "hermes", "--profile", "ralph-author",
            "-z", "Run the shell command: true", "--cli", timeout=240)
    found = docker(
        "ps", "--filter", f"ancestor={SANDBOX_IMAGE}:desktop",
        "--format", "{{.Names}}").stdout.split()
    return found[0] if found else None


class SandboxEscapeTests(unittest.TestCase):
    """What a dependency install script or an injected Makefile can reach."""

    @classmethod
    def setUpClass(cls):
        if terminal_backend() != "docker":
            raise unittest.SkipTest("terminal backend is 'local'; no sandbox in use")
        cls.sandbox = live_sandbox()
        if not cls.sandbox:
            raise unittest.SkipTest("could not start a sandbox container")

    def inside(self, script: str) -> str:
        result = docker("exec", self.sandbox, "sh", "-c", script)
        return (result.stdout + result.stderr).strip()

    def test_cannot_see_the_host_filesystem(self):
        self.assertIn("No such file", self.inside("ls /Users 2>&1 | head -1"))

    def test_has_no_docker_socket(self):
        """With a socket, the sandbox could start a privileged container and
        mount anything — the escape the sandbox exists to prevent."""
        self.assertIn("No such file", self.inside("ls /var/run/docker.sock 2>&1"))

    def test_has_no_docker_client_or_host(self):
        self.assertEqual("", self.inside("command -v docker || true"))
        self.assertEqual("", self.inside("printf %s \"${DOCKER_HOST:-}\""))

    def test_cannot_reach_any_credential_store(self):
        self.assertEqual("", self.inside(
            "find / -maxdepth 7 -name auth.json 2>/dev/null | head -1"))

    def test_cannot_reach_the_board(self):
        self.assertEqual("", self.inside(
            "find / -maxdepth 8 -name 'kanban*.db' 2>/dev/null | head -1"))

    def test_cannot_reach_other_profiles(self):
        """Profiles isolate state, and the sandbox must not undo that."""
        self.assertIn("No such file", self.inside("ls /root/.hermes/profiles 2>&1"))


class SandboxScopeTests(unittest.TestCase):
    """Every mount is under the profile's own directory, so two profiles
    running code never share a sandbox."""

    @classmethod
    def setUpClass(cls):
        if terminal_backend() != "docker":
            raise unittest.SkipTest("terminal backend is 'local'; no sandbox in use")
        cls.sandbox = live_sandbox()
        if not cls.sandbox:
            raise unittest.SkipTest("could not start a sandbox container")
        cls.mounts = json.loads(docker(
            "inspect", cls.sandbox, "--format", "{{json .Mounts}}").stdout or "[]")

    def test_every_mount_is_profile_scoped(self):
        for mount in self.mounts:
            self.assertIn("/profiles/", mount["Source"], mount["Source"])

    def test_writable_mounts_are_workspace_and_home_only(self):
        writable = sorted(m["Destination"] for m in self.mounts if m.get("RW"))
        self.assertEqual(["/root", "/workspace"], writable)


class ProxyRefusalTests(unittest.TestCase):
    """Defence in depth around the orchestrator, not the sandbox boundary."""

    @classmethod
    def setUpClass(cls):
        probe = compose("exec", "-T", "hermes", "sh", "-c",
                        "docker version >/dev/null 2>&1", timeout=60)
        if probe.returncode != 0:
            raise unittest.SkipTest("no Docker API reachable from the hermes container")

    def test_volume_api_is_refused(self):
        result = compose("exec", "-T", "hermes", "sh", "-c", "docker volume ls 2>&1")
        self.assertIn("403", result.stdout + result.stderr)

    def test_daemon_info_is_refused(self):
        """`docker info` leaks host paths, runtime config, and registry auth."""
        result = compose("exec", "-T", "hermes", "sh", "-c",
                         "docker info --format '{{.Name}}' 2>&1")
        self.assertNotIn("Name:", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
