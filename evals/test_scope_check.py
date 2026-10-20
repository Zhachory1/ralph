"""The scope check catches what a builder adds without being asked.

The design calls for a test where a builder sneaks in an unrequested
change, and the suite passes only if that change is caught. That is
`test_catches_a_sneaked_in_change` below; the rest exist so the check
cannot be defeated by an empty plan or an unreadable one.

    python3 evals/test_scope_check.py
"""

import importlib.machinery
import importlib.util
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bootstrap" / "scope-check"

# The script has no .py suffix, so name the loader explicitly.
_loader = importlib.machinery.SourceFileLoader("scope_check", str(SCRIPT))
_spec = importlib.util.spec_from_loader("scope_check", _loader)
scope_check = importlib.util.module_from_spec(_spec)
_loader.exec_module(scope_check)


class CheckTests(unittest.TestCase):
    """The rules, without a repository."""

    def check(self, files, lines=10, planned=("src/digest.py",), **kw):
        return scope_check.check(list(files), lines, {"files": list(planned)},
                                 kw.get("max_lines", 400))

    def test_planned_files_pass(self):
        self.assertEqual([], self.check(["src/digest.py"]))

    def test_catches_a_sneaked_in_change(self):
        """The case the design names: the builder also 'improved' something
        nearby. Honest intent, unreviewed diff."""
        flags = self.check(["src/digest.py", "src/unrelated.py"])
        self.assertTrue(any("src/unrelated.py" in f for f in flags), flags)

    def test_catches_a_dependency_added_quietly(self):
        """A lockfile edit is how an unrelated change rides along."""
        flags = self.check(["src/digest.py", "package-lock.json"])
        self.assertTrue(any("package-lock.json" in f for f in flags), flags)

    def test_catches_a_workflow_change(self):
        """CI config decides what the other checks even run."""
        flags = self.check(["src/digest.py", ".github/workflows/ci.yml"])
        self.assertTrue(flags)

    def test_catches_an_oversized_diff(self):
        flags = self.check(["src/digest.py"], lines=900)
        self.assertTrue(any("900" in f for f in flags), flags)

    def test_a_directory_plan_covers_its_files(self):
        self.assertEqual([], self.check(["src/a.py", "src/b.py"], planned=["src/"]))

    def test_a_glob_plan_covers_its_matches(self):
        self.assertEqual([], self.check(["tests/test_x.py"], planned=["tests/*.py"]))

    def test_an_empty_plan_fails_rather_than_passes(self):
        """A card nobody scoped must not sail through: a check that passes
        when it has nothing to check is not a check."""
        flags = scope_check.check(["src/digest.py"], 10, {"files": []}, 400)
        self.assertTrue(flags)

    def test_a_route_directory_in_brackets_matches_literally(self):
        """Next.js names a dynamic route `[id]`, which fnmatch reads as a
        character set. Globbing first made the check flag the very file the
        plan authorised."""
        planned = ["apps/hub/app/a/[id]/edit/page.tsx"]
        self.assertEqual([], self.check(planned, planned=planned))

    def test_a_planned_dependency_file_is_allowed(self):
        """Adding a dependency is fine when the design said to."""
        self.assertEqual([], self.check(["package.json"], planned=["package.json"]))


class CommitPlanTests(unittest.TestCase):
    """CI cannot reach the board: it is a SQLite file on one machine. The plan
    has to travel in something a runner can see, which is the commit."""

    def test_reads_a_planned_files_block_from_text(self):
        plan = scope_check.plan_from_text(
            "feat: a thing\n\n<planned_files>\n- src/a.py\n- src/b.py\n</planned_files>\n")
        self.assertEqual(["src/a.py", "src/b.py"], plan["files"])

    def test_no_block_is_an_empty_plan_not_a_pass(self):
        """An empty plan fails the check; it must not read as unscoped-and-fine."""
        self.assertEqual([], scope_check.plan_from_text("feat: no block here")["files"])

    def test_ignores_prose_around_the_block(self):
        plan = scope_check.plan_from_text(
            "why\n<planned_files>\n- a.py\n</planned_files>\nand some trailing notes\n")
        self.assertEqual(["a.py"], plan["files"])


class RepositoryTests(unittest.TestCase):
    """End to end against a real git diff."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "digest.py").write_text("x = 1\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True,
                       capture_output=True, text=True)

    def plan(self, *files):
        path = self.repo / "plan.json"
        path.write_text('{"files": %s}' % list(files).__repr__().replace("'", '"'))
        return path

    def run_check(self, plan_path, *extra):
        return subprocess.run(
            [str(SCRIPT), "--base", "main", "--head", "HEAD",
             "--plan", str(plan_path), *extra],
            cwd=self.repo, capture_output=True, text=True)

    def test_in_scope_change_passes(self):
        (self.repo / "src" / "digest.py").write_text("x = 2\n")
        self.git("checkout", "-qb", "work")
        self.git("commit", "-aqm", "change")

        result = self.run_check(self.plan("src/digest.py"))
        self.assertEqual(0, result.returncode, result.stdout)

    def test_out_of_scope_change_fails(self):
        self.git("checkout", "-qb", "work")
        (self.repo / "src" / "sneaky.py").write_text("y = 1\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "also improved something")

        result = self.run_check(self.plan("src/digest.py"))
        self.assertEqual(1, result.returncode)
        self.assertIn("sneaky.py", result.stdout)

    def test_scope_approved_label_overrides(self):
        """A human accepting the diff is a decision with their name on it."""
        self.git("checkout", "-qb", "work")
        (self.repo / "src" / "sneaky.py").write_text("y = 1\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "intentional")

        result = self.run_check(self.plan("src/digest.py"),
                                "--labels", "agent-authored,scope-approved")
        self.assertEqual(0, result.returncode, result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
