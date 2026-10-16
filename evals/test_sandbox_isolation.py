"""Isolation tests: try to escape, and fail.

A configuration test would have passed on the day the sandbox was wide open,
because the proxy was configured exactly as documented and the documentation
was misleading. These attempt the escape instead.

Skipped unless the Docker terminal backend is actually enabled, so they are
harmless on an install still running commands locally.

    python3 evals/test_sandbox_isolation.py
"""

import pathlib
import subprocess
import unittest

COMPOSE = pathlib.Path(__file__).resolve().parent.parent / "compose"
PROBE_IMAGE = "alpine:3"


def in_hermes(*args, timeout=120):
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "hermes", *args],
        capture_output=True, text=True, cwd=COMPOSE, timeout=timeout,
    )


def terminal_backend():
    result = in_hermes("sh", "-c", "echo $TERMINAL_ENV", timeout=30)
    return (result.stdout or "").strip() or "local"


class ProxyRefusalTests(unittest.TestCase):
    """The proxy must refuse the APIs that hand over the daemon."""

    @classmethod
    def setUpClass(cls):
        if in_hermes("sh", "-c", "docker version >/dev/null 2>&1", timeout=60).returncode != 0:
            raise unittest.SkipTest("no Docker API reachable from the hermes container")

    def test_volume_api_is_refused(self):
        result = in_hermes("sh", "-c", "docker volume ls 2>&1")
        self.assertIn("403", result.stdout + result.stderr)

    def test_daemon_info_is_refused(self):
        """`docker info` leaks host paths, runtime config, and registry auth."""
        result = in_hermes("sh", "-c", "docker info --format '{{.Name}}' 2>&1")
        self.assertNotIn("Name:", result.stdout)


class EscapeTests(unittest.TestCase):
    """The tests that matter: a sandbox must not reach the machine.

    These are expected to FAIL today. The socket proxy blocks the volume API
    but not a bind mount on container create, so `docker run -v /:/host`
    still reaches the host. Recorded in docs/spikes/sandbox-isolation.md;
    TERMINAL_ENV stays 'local' until they pass.
    """

    @classmethod
    def setUpClass(cls):
        if terminal_backend() != "docker":
            raise unittest.SkipTest(
                "terminal backend is 'local'; sandboxes are not in use yet")

    def test_cannot_bind_mount_host_root(self):
        result = in_hermes(
            "sh", "-c",
            f"docker run --rm -v /:/host {PROBE_IMAGE} ls /host 2>&1")
        self.assertNotIn("etc", result.stdout.split(),
                         "a sandbox bind-mounted the host filesystem")

    def test_cannot_write_to_the_host(self):
        result = in_hermes(
            "sh", "-c",
            f"docker run --rm -v /:/host {PROBE_IMAGE} "
            "sh -c 'touch /host/tmp/ralph-escape && echo WRITABLE' 2>&1")
        self.assertNotIn("WRITABLE", result.stdout,
                         "a sandbox wrote to the host filesystem")

    def test_cannot_reach_the_hermes_state(self):
        """The credentials, board, and memory the sandbox exists to be kept from."""
        result = in_hermes(
            "sh", "-c",
            f"docker run --rm -v /:/host {PROBE_IMAGE} "
            "find /host -maxdepth 6 -name auth.json 2>/dev/null | head -1")
        self.assertEqual("", result.stdout.strip(),
                         "a sandbox reached a Hermes auth.json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
