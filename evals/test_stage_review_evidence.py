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

import importlib.machinery  # noqa: E402
import importlib.util  # noqa: E402
_loader = importlib.machinery.SourceFileLoader("stage_review_evidence", str(SCRIPT))
_spec = importlib.util.spec_from_loader("stage_review_evidence", _loader)
stage = importlib.util.module_from_spec(_spec)
_loader.exec_module(stage)


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

    def run_it(self, pr_text=None, checks=None, threads=None):
        extra = []
        if pr_text is not None:
            f = self.d / "pr.md"
            f.write_text(pr_text)
            extra = ["--pr-text", str(f)]
        if checks is not None:
            f = self.d / "checks.json"
            f.write_text(checks)
            extra += ["--checks", str(f)]
        if threads is not None:
            f = self.d / "threads.md"
            f.write_text(threads)
            extra += ["--threads", str(f)]
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--checkout", str(self.checkout),
             "--repo", "example/repo", "--number", "1",
             "--base-sha", self.base_sha, "--head-sha", self.head_sha,
             "--work-root", str(self.work_root), *extra],
            capture_output=True, text=True)

    def evidence(self):
        return next(self.work_root.iterdir()) / "evidence"

    def test_pr_text_and_the_doc_it_names_are_staged(self):
        """The case that produced a false finding: a PR scoped by a design
        doc, reviewed without seeing either."""
        (self.checkout / "docs").mkdir()
        (self.checkout / "docs" / "dd.md").write_text("Callers wire this in a later PR.\n")
        git("add", "-A", cwd=self.checkout)
        git("commit", "-q", "-m", "doc", cwd=self.checkout)
        self.head_sha = git("rev-parse", "HEAD", cwd=self.checkout).stdout.strip()
        r = self.run_it("# Add filter\n\nImplements docs/dd.md. Wiring is out of scope.\n")
        self.assertEqual(0, r.returncode, r.stderr)
        ev = self.evidence()
        self.assertIn("out of scope", (ev / "pr.md").read_text())
        self.assertIn("later PR", (ev / "docs" / "docs" / "dd.md").read_text())
        self.assertEqual(["docs/dd.md"], json.loads((ev / "context.json").read_text())["docs"])

    def test_doc_names_resolve_only_inside_the_repository(self):
        """Untrusted text naming a path outside the tree, or one that does
        not exist at head, stages nothing for it."""
        outside = self.d / "secret.md"
        outside.write_text("host file\n")
        r = self.run_it(f"see ../secret.md and {outside} and missing/nope.md\n")
        self.assertEqual(0, r.returncode, r.stderr)
        ev = self.evidence()
        self.assertFalse((ev / "docs").exists())
        self.assertEqual([], json.loads((ev / "context.json").read_text())["docs"])

    def test_checks_and_threads_are_staged(self):
        """Lenses asked for CI results and review discussion they could not
        see; both were on the PR, never staged."""
        checks = '[{"name": "Validate", "bucket": "pass"}]'
        r = self.run_it(checks=checks, threads="## reviewer\nlooks fine\n")
        self.assertEqual(0, r.returncode, r.stderr)
        ev = self.evidence()
        self.assertEqual(checks, (ev / "checks.json").read_text())
        self.assertIn("looks fine", (ev / "threads.md").read_text())
        ctx = json.loads((ev / "context.json").read_text())
        self.assertTrue(ctx["checks"])
        self.assertTrue(ctx["threads"])

    def test_oversized_threads_are_cut_not_dropped(self):
        big = "x" * (stage.MAX_THREADS_BYTES + 5000)
        self.assertEqual(0, self.run_it(threads=big).returncode)
        self.assertEqual(stage.MAX_THREADS_BYTES, len((self.evidence() / "threads.md").read_text()))

    def test_without_checks_or_threads_neither_file_exists(self):
        self.assertEqual(0, self.run_it().returncode)
        ev = self.evidence()
        self.assertFalse((ev / "checks.json").exists())
        self.assertFalse((ev / "threads.md").exists())
        ctx = json.loads((ev / "context.json").read_text())
        self.assertFalse(ctx["checks"])
        self.assertFalse(ctx["threads"])

    def test_without_pr_text_nothing_extra_is_staged(self):
        self.assertEqual(0, self.run_it().returncode)
        self.assertFalse((self.evidence() / "pr.md").exists())

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

    def test_oversized_diff_is_flagged_not_silently_truncated(self):
        """Found by the first real graph this script produced: three lenses
        independently reported the staged diff unreadable, because the
        evidence server's own 1 MiB per-file cap applies to it too."""
        big = self.checkout / "big.txt"
        big.write_text("padding padding padding\n" * 100_000)
        git("add", "-A", cwd=self.checkout)
        git("commit", "-q", "-m", "oversized", cwd=self.checkout)
        head_sha = git("rev-parse", "HEAD", cwd=self.checkout).stdout.strip()

        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--checkout", str(self.checkout),
             "--repo", "example/repo", "--number", "1",
             "--base-sha", self.head_sha, "--head-sha", head_sha,
             "--work-root", str(self.work_root)],
            capture_output=True, text=True)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("1 MiB" in r.stderr or "1,048,576" in r.stderr, [True])
        op_dir = next(self.work_root.iterdir())
        context = json.loads((op_dir / "evidence" / "context.json").read_text())
        self.assertTrue(context["diff_oversized"])
        self.assertGreater(context["diff_bytes"], 1_048_576)

    def test_a_normal_diff_is_not_flagged(self):
        r = self.run_it()
        op_dir = next(self.work_root.iterdir())
        context = json.loads((op_dir / "evidence" / "context.json").read_text())
        self.assertFalse(context["diff_oversized"])

    def test_rejects_a_short_sha(self):
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--checkout", str(self.checkout),
             "--repo", "example/repo", "--number", "1",
             "--base-sha", self.base_sha[:7], "--head-sha", self.head_sha,
             "--work-root", str(self.work_root)],
            capture_output=True, text=True)
        self.assertEqual(2, r.returncode)
        self.assertIn("not a full commit sha", r.stderr)

    def test_binding_matches_what_lens_tools_expects(self):
        """Layout parity between stage-review-evidence's output and
        lens-tools' safe_binding(). A review swarm run on this very PR
        flagged this integration as untested -- correctly, since neither
        script imports the other and nothing had checked they agree.

        A lens profile sets LENS_WORKSPACE=$HERMES_KANBAN_WORKSPACE and
        LENS_EVIDENCE_ROOT=$HERMES_KANBAN_WORKSPACE/evidence. bootstrap/
        build-review-graph creates each card with --workspace dir:<op_root>,
        so HERMES_KANBAN_WORKSPACE resolves to op_root at dispatch time. This
        asserts the binding this script writes is consistent with that
        without needing a live Hermes task to prove it.
        """
        self.run_it()
        op_root = next(self.work_root.iterdir())
        workspace = op_root  # what --workspace dir:<...> would be set to
        evidence_root = workspace / "evidence"  # LENS_EVIDENCE_ROOT
        binding_path = workspace / ".lens-tools.json"  # safe_binding's BINDING

        self.assertTrue(binding_path.is_file(),
                        "lens-tools reads workspace/.lens-tools.json; "
                        "this script must write it there")
        binding = json.loads(binding_path.read_text())
        self.assertEqual(str(evidence_root.resolve()),
                         str(pathlib.Path(binding["evidence_root"]).resolve()),
                         "the binding's evidence_root must equal "
                         "$LENS_WORKSPACE/evidence, or safe_binding's "
                         "relative_to() check fails for every real lens")

    def test_never_clones(self):
        """No upstream argument exists; the only network-shaped input is
        whatever the caller's own checkout already has."""
        help_text = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                                   capture_output=True, text=True).stdout
        self.assertNotIn("--upstream", help_text)
        self.assertNotIn("--clone", help_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
