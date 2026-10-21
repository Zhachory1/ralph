"""The scanner that keeps internal names out of a public skills tree.

The case worth testing is not "does it find a marker" but what it does when it
cannot see the list it was asked to use. A reviewer passes --markers precisely
because the public subset is not the real check; if a missing file quietly
selected that subset, the run would answer an easier question and answer it
green. That is the regression these tests exist for.

    python3 evals/test_check_skills_public.py
"""

import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECK = ROOT / "evals" / "check-skills-public"


def run(args, cwd):
    return subprocess.run([sys.executable, str(CHECK), *args],
                          capture_output=True, text=True, cwd=cwd)


class MarkerFileTests(unittest.TestCase):
    """An explicitly requested marker file that cannot be used is an error."""

    def test_missing_marker_file_fails(self):
        r = run(["--markers", "/nonexistent/markers.txt"], ROOT)
        self.assertEqual(2, r.returncode)
        self.assertIn("not found", r.stderr)

    def test_empty_marker_file_fails(self):
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "markers.txt"
            f.write_text("")
            r = run(["--markers", str(f)], ROOT)
        self.assertEqual(2, r.returncode)
        self.assertIn("no markers", r.stderr)

    def test_comments_only_marker_file_fails(self):
        """A file of comments has no markers, however many lines it has."""
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "markers.txt"
            f.write_text("# the real list lives in the overlay\n\n")
            r = run(["--markers", str(f)], ROOT)
        self.assertEqual(2, r.returncode)
        self.assertIn("no markers", r.stderr)

    def test_failure_never_reports_a_clean_scan(self):
        """The thing that would be dangerous: exit 0 with a reassuring line."""
        r = run(["--markers", "/nonexistent/markers.txt"], ROOT)
        self.assertNotEqual(0, r.returncode)
        self.assertNotIn("no internal markers found", r.stdout)


class ScanTests(unittest.TestCase):
    """What it does once it has a list."""

    def _tree(self, d, body):
        skills = pathlib.Path(d) / "skills" / "code" / "example"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text(body)
        (pathlib.Path(d) / "evals").mkdir(exist_ok=True)
        check = pathlib.Path(d) / "evals" / "check-skills-public"
        check.write_text(CHECK.read_text())
        check.chmod(0o755)
        return pathlib.Path(d)

    def test_clean_tree_passes(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._tree(d, "---\nname: x\n---\nWrite tests first.\n")
            r = subprocess.run([sys.executable, str(root / "evals" / "check-skills-public")],
                               capture_output=True, text=True)
        self.assertEqual(0, r.returncode)
        self.assertIn("no internal markers found", r.stdout)

    def test_internal_name_fails_and_is_located(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._tree(d, "---\nname: x\n---\nDeploy per Rokt standards.\n")
            r = subprocess.run([sys.executable, str(root / "evals" / "check-skills-public")],
                               capture_output=True, text=True)
        self.assertEqual(1, r.returncode)
        self.assertIn("SKILL.md:4", r.stdout)
        self.assertIn("Rokt", r.stdout)

    def test_custom_markers_are_used(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._tree(d, "---\nname: x\n---\nCall acme-widget-service.\n")
            f = root / "markers.txt"
            f.write_text("acme-widget-service\n")
            r = subprocess.run([sys.executable, str(root / "evals" / "check-skills-public"),
                                "--markers", str(f)], capture_output=True, text=True)
        self.assertEqual(1, r.returncode)
        self.assertIn("acme-widget-service", r.stdout)

    def test_mode_is_always_announced(self):
        """A green build should never mean more than it did."""
        with tempfile.TemporaryDirectory() as d:
            root = self._tree(d, "---\nname: x\n---\nNothing internal here.\n")
            r = subprocess.run([sys.executable, str(root / "evals" / "check-skills-public")],
                               capture_output=True, text=True)
        self.assertIn("public-only", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
