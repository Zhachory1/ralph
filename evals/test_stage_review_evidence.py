"""The script that stages one pull request's evidence for a review lens.

No network: builds a real local git repository with two real commits, so the
diff, the context, and the binding file are all exercised against git itself.

    python3 evals/test_stage_review_evidence.py
"""

import json
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bootstrap" / "stage-review-evidence"


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


class StageReviewEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.tmp.name)
        self.checkout = self.d / "repo"
        self.checkout.mkdir()
        for c in (["init", "-q", "-b", "main"], ["config", "user.email", "t@e.com"],
                  ["config", "user.name", "T"]):
            git(*c, cwd=self.checkout)
        (self.checkout / "a.txt").write_text("one\n")
        git("add", "-A", cwd=self.checkout)
        git("commit", "-q", "-m", "base", cwd=self.checkout)
        self.base_sha = git("rev-parse", "HEAD", cwd=self.checkout).stdout.strip()
        (self.checkout / "a.txt").write_text("one\ntwo\n")
        (self.checkout / "auth.py").write_text("# touches an auth path\n")
        git("add", "-A", cwd=self.checkout)
        git("commit", "-q", "-m", "head", cwd=self.checkout)
        self.head_sha = git("rev-parse", "HEAD", cwd=self.checkout).stdout.strip()
        self.work_root = self.d / "work"

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--checkout", str(self.checkout),
             "--repo", "example/repo", "--number", "1",
             "--base-sha", self.base_sha, "--head-sha", self.head_sha,
             "--work-root", str(self.work_root)],
            capture_output=True, text=True)

    def test_stages_diff_and_context(self):
        r = self.run_it()
        self.assertEqual(0, r.returncode, r.stderr)
        op_dirs = list(self.work_root.iterdir())
        self.assertEqual(1, len(op_dirs))
        evidence = op_dirs[0] / "evidence"
        self.assertIn("+two", (evidence / "diff.patch").read_text())
        context = json.loads((evidence / "context.json").read_text())
        self.assertEqual(["a.txt", "auth.py"], sorted(context["changed_paths"]))
        self.assertTrue(context["security_relevant"], "auth.py should trip the security marker")

    def test_binding_file_mode_is_0440(self):
        self.run_it()
        op_dir = next(self.work_root.iterdir())
        binding = op_dir / ".lens-tools.json"
        mode = stat.S_IMODE(binding.stat().st_mode)
        self.assertEqual(0o440, mode)

    def test_binding_points_inside_its_own_operation(self):
        self.run_it()
        op_dir = next(self.work_root.iterdir())
        binding = json.loads((op_dir / ".lens-tools.json").read_text())
        self.assertEqual(str((op_dir / "evidence").resolve()),
                         str(pathlib.Path(binding["evidence_root"]).resolve()))

    def test_second_run_is_a_no_op_not_a_crash(self):
        """The bug found while testing this by hand: chmod 0440 then a second
        write without restoring write access first raised PermissionError."""
        self.assertEqual(0, self.run_it().returncode)
        r2 = self.run_it()
        self.assertEqual(0, r2.returncode, r2.stderr)
        op_dirs = list(self.work_root.iterdir())
        self.assertEqual(1, len(op_dirs), "same head should stage to the same operation")

    def test_rejects_a_short_sha(self):
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--checkout", str(self.checkout),
             "--repo", "example/repo", "--number", "1",
             "--base-sha", self.base_sha[:7], "--head-sha", self.head_sha,
             "--work-root", str(self.work_root)],
            capture_output=True, text=True)
        self.assertEqual(2, r.returncode)
        self.assertIn("not a full commit sha", r.stderr)

    def test_never_clones(self):
        """No upstream argument exists; the only network-shaped input is
        whatever the caller's own checkout already has."""
        help_text = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                                   capture_output=True, text=True).stdout
        self.assertNotIn("--upstream", help_text)
        self.assertNotIn("--clone", help_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
