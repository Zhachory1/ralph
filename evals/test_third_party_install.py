"""The installer for skills the repository references instead of holding.

No network: each test builds a real git repository in a temporary directory
and points a manifest at it, so pinning and the refusal to overwrite committed
work are exercised against git itself rather than a mock of it.

    python3 evals/test_third_party_install.py
"""

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bootstrap" / "install-third-party-skills"


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.tmp.name)
        self.repo = self.d / "repo"
        (self.repo / "skills" / "write").mkdir(parents=True)
        (self.repo / "bootstrap").mkdir(parents=True)
        self.script = self.repo / "bootstrap" / SCRIPT.name
        self.script.write_text(SCRIPT.read_text())
        for c in (["init", "--quiet"], ["config", "user.email", "t@e.com"],
                  ["config", "user.name", "T"], ["add", "-A"],
                  ["commit", "--quiet", "-m", "base"]):
            git(*c, cwd=self.repo)

        self.up = self.d / "up"
        self.up.mkdir()
        for c in (["init", "--quiet"], ["config", "user.email", "t@e.com"],
                  ["config", "user.name", "T"]):
            git(*c, cwd=self.up)
        (self.up / "SKILL.md").write_text("---\nname: thing\n---\nupstream\n")
        git("add", "-A", cwd=self.up)
        git("commit", "--quiet", "-m", "one", cwd=self.up)
        self.sha = git("rev-parse", "HEAD", cwd=self.up).stdout.strip()

    def tearDown(self):
        self.tmp.cleanup()

    def manifest(self, commit=None, upstream=None):
        p = self.d / "m.yaml"
        p.write_text("skills:\n"
                     "  - name: thing\n"
                     "    lane: write\n"
                     f"    upstream: {upstream or self.up}\n"
                     f"    commit: {commit or self.sha}\n")
        return p

    def run_it(self, m, *extra):
        return subprocess.run([sys.executable, str(self.script), "--manifest",
                               str(m), *extra],
                              capture_output=True, text=True, cwd=self.repo)

    def test_installs_at_the_pinned_commit(self):
        r = self.run_it(self.manifest())
        self.assertEqual(0, r.returncode, r.stderr)
        t = self.repo / "skills" / "write" / "thing"
        self.assertTrue((t / "SKILL.md").is_file())
        self.assertEqual(self.sha,
                         json.loads((t / ".third-party.json").read_text())["commit"])

    def test_second_run_changes_nothing(self):
        m = self.manifest()
        self.run_it(m)
        self.assertIn("already at", self.run_it(m).stdout)

    def test_check_fails_on_drift_and_passes_once_installed(self):
        m = self.manifest()
        self.assertEqual(1, self.run_it(m, "--check").returncode)
        self.run_it(m)
        self.assertEqual(0, self.run_it(m, "--check").returncode)

    def test_refuses_to_overwrite_a_committed_skill(self):
        """The dangerous case: a manifest name colliding with real work."""
        t = self.repo / "skills" / "write" / "thing"
        t.mkdir(parents=True)
        (t / "SKILL.md").write_text("---\nname: thing\n---\ncommitted work\n")
        git("add", "-A", cwd=self.repo)
        git("commit", "--quiet", "-m", "tracked", cwd=self.repo)

        r = self.run_it(self.manifest())
        self.assertIn("refusing to overwrite", r.stdout + r.stderr)
        self.assertIn("committed work", (t / "SKILL.md").read_text())

    def test_a_branch_is_not_a_pin(self):
        r = self.run_it(self.manifest(commit="main"))
        self.assertEqual(2, r.returncode)
        self.assertIn("not a pin", r.stderr)

    def test_unreachable_upstream_warns_but_does_not_stop_a_stack(self):
        bad = self.manifest(upstream=self.d / "no-such-repo")
        self.assertEqual(0, self.run_it(bad).returncode,
                         "an install run must not fail the stack")
        self.assertEqual(1, self.run_it(bad, "--check").returncode,
                         "but CI should know")


if __name__ == "__main__":
    unittest.main(verbosity=2)
