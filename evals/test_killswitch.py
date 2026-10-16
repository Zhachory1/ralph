"""The kill switch does what it claims, and the drill is safe to run.

A kill switch nobody rehearses is a kill switch nobody can rely on, and a
drill that quietly changes something is worse than no drill. These run the
real script in drill mode and check that the system is untouched afterwards.

    python3 evals/test_killswitch.py        # from the repo root
"""

import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
STOP = ROOT / "bootstrap" / "stop"
ENV = ROOT / "compose" / ".env"


def drill(*extra):
    return subprocess.run(
        [str(STOP), "--drill", *extra],
        capture_output=True, text=True, cwd=ROOT,
    )


class DrillSafetyTests(unittest.TestCase):
    """A drill must be boring: it reports, and changes nothing."""

    def test_drill_succeeds(self):
        self.assertEqual(0, drill().returncode, drill().stderr)

    def test_drill_changes_no_credentials(self):
        if not ENV.exists():
            self.skipTest("no compose/.env on this machine")
        with tempfile.TemporaryDirectory() as tmp:
            backup = pathlib.Path(tmp) / "env"
            shutil.copy2(ENV, backup)
            drill()
            self.assertEqual(
                backup.read_text(), ENV.read_text(),
                "the drill modified compose/.env",
            )

    def test_drill_leaves_containers_running(self):
        before = subprocess.run(
            ["docker", "compose", "ps", "--format", "{{.Service}}"],
            capture_output=True, text=True, cwd=ROOT / "compose",
        ).stdout
        drill()
        after = subprocess.run(
            ["docker", "compose", "ps", "--format", "{{.Service}}"],
            capture_output=True, text=True, cwd=ROOT / "compose",
        ).stdout
        self.assertEqual(before, after, "the drill stopped a container")

    def test_drill_announces_itself(self):
        self.assertIn("DRILL", drill().stdout)


class DrillCoverageTests(unittest.TestCase):
    """The drill has to show every destructive step, or it rehearses nothing."""

    def setUp(self):
        self.out = drill().stdout

    def test_shows_the_pause(self):
        self.assertIn("hermes pause", self.out)

    def test_shows_the_container_stop(self):
        self.assertIn("docker compose stop", self.out)

    def test_reports_in_flight_work(self):
        """An operator needs to know what was interrupted before killing it."""
        self.assertIn("In-flight work", self.out)

    def test_names_where_credentials_must_be_revoked(self):
        """Clearing .env is not revocation: a stopped container still holds the
        token, and so does GitHub."""
        self.assertIn("github.com/settings/tokens", self.out)

    def test_covers_open_pull_requests(self):
        self.assertIn("pull request", self.out.lower())


class InterfaceTests(unittest.TestCase):
    def test_script_is_executable(self):
        self.assertTrue(os.access(STOP, os.X_OK), "bootstrap/stop is not executable")

    def test_help_does_not_run_anything(self):
        result = subprocess.run(
            [str(STOP), "--help"], capture_output=True, text=True, cwd=ROOT,
        )
        self.assertEqual(0, result.returncode)
        self.assertNotIn("would run", result.stdout)

    def test_unknown_option_is_refused(self):
        """A typo in an incident must not silently become a real stop."""
        result = subprocess.run(
            [str(STOP), "--dril"], capture_output=True, text=True, cwd=ROOT,
        )
        self.assertNotEqual(0, result.returncode)


if __name__ == "__main__":
    unittest.main(verbosity=2)
