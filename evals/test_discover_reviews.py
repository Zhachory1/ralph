"""Discovery on a timer: the first thing that acts without a human.

No network -- these stub gh and build-review-graph to test the admission
logic itself: the allowlist and the per-run cap. There is no daily cap by
direct instruction, so these tests check that nothing in this file gates
on a count across invocations, not that a count stays under some number.
The real discovery and real graph-building were proven by hand against a
live board and a real PR; see the PR body for that record.

    python3 evals/test_discover_reviews.py
"""

import importlib.machinery
import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bootstrap" / "discover-reviews"

_loader = importlib.machinery.SourceFileLoader("discover_reviews", str(SCRIPT))
_spec = importlib.util.spec_from_loader("discover_reviews", _loader)
discover_reviews = importlib.util.module_from_spec(_spec)
_loader.exec_module(discover_reviews)


def fake_pr(repo, number, title="x"):
    return {"repository": {"nameWithOwner": repo}, "number": number, "title": title,
           "url": f"https://github.com/{repo}/pull/{number}", "updatedAt": "2026-01-01T00:00:00Z"}


class AllowlistTests(unittest.TestCase):
    """The rejected alternative is the fleet's board-engine shape: no
    wildcard, no "all accessible repos" exists anywhere in this script."""

    def test_only_allowlisted_repos_are_admitted(self):
        with tempfile.TemporaryDirectory() as d:
            work_root = pathlib.Path(d)
            with mock.patch.object(discover_reviews, "search_assigned_prs",
                                   return_value=[fake_pr("a/allowed", 1), fake_pr("b/not-allowed", 2)]), \
                mock.patch.object(discover_reviews, "pr_shas", return_value=("0" * 40, "1" * 40)), \
                mock.patch.object(discover_reviews, "ensure_checkout", return_value=pathlib.Path("/x/a/repo")), \
                mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                import sys
                argv = ["discover-reviews", "--allow", "a/allowed", "--checkout-root", "/x",
                       "--work-root", str(work_root), "--once-for-real"]
                with mock.patch.object(sys, "argv", argv):
                    discover_reviews.main()
                calls = [c for c in run.call_args_list if "build-review-graph" in " ".join(c.args[0])]
                self.assertEqual(1, len(calls))
                self.assertIn("a/allowed", calls[0].args[0])


class PerRunCapTests(unittest.TestCase):
    """The one remaining admission bound: how many PRs one invocation can
    build, regardless of how many gh search prs returns."""

    def _run(self, work_root, prs, max_per_run=10):
        import sys
        with mock.patch.object(discover_reviews, "search_assigned_prs", return_value=prs), \
            mock.patch.object(discover_reviews, "pr_shas", return_value=("0" * 40, "1" * 40)), \
                mock.patch.object(discover_reviews, "ensure_checkout", return_value=pathlib.Path("/x/a/repo")), \
            mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            argv = ["discover-reviews", "--allow", "a/repo", "--checkout-root", "/x",
                   "--work-root", str(work_root), "--once-for-real",
                   "--max-per-run", str(max_per_run)]
            with mock.patch.object(sys, "argv", argv):
                discover_reviews.main()
        return [c for c in run.call_args_list if "build-review-graph" in " ".join(c.args[0])]

    def test_per_run_cap_limits_admission_even_with_more_eligible(self):
        with tempfile.TemporaryDirectory() as d:
            prs = [fake_pr("a/repo", i) for i in range(1, 6)]
            calls = self._run(pathlib.Path(d), prs, max_per_run=2)
            self.assertEqual(2, len(calls))

    def test_all_eligible_are_admitted_when_under_the_per_run_cap(self):
        """No daily cap means a repeated run the same day admits every
        eligible PR again, bounded only by max-per-run."""
        with tempfile.TemporaryDirectory() as d:
            prs = [fake_pr("a/repo", i) for i in range(1, 4)]
            calls = self._run(pathlib.Path(d), prs, max_per_run=10)
            self.assertEqual(3, len(calls))


class NoDailyCapTests(unittest.TestCase):
    """By direct instruction: discovery has no daily cap. Every assigned PR
    gets reviewed; concurrency is the only throughput lever."""

    def test_no_daily_cap_argument_exists(self):
        help_text = self._help()
        self.assertNotIn("--daily-cap", help_text)

    def test_no_admissions_lock_exists(self):
        self.assertFalse(hasattr(discover_reviews, "admissions_lock"))

    def test_no_admission_state_functions_exist(self):
        self.assertFalse(hasattr(discover_reviews, "load_admissions"))
        self.assertFalse(hasattr(discover_reviews, "save_admissions"))
        self.assertFalse(hasattr(discover_reviews, "reserve_admission"))

    def test_no_state_file_constant_exists(self):
        self.assertFalse(hasattr(discover_reviews, "STATE_FILE"))

    @staticmethod
    def _help():
        import subprocess
        return subprocess.run(["python3", str(SCRIPT), "--help"],
                              capture_output=True, text=True).stdout


class DryRunTests(unittest.TestCase):
    def test_without_once_for_real_nothing_is_built(self):
        import sys
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(discover_reviews, "search_assigned_prs",
                                   return_value=[fake_pr("a/repo", 1)]), \
                mock.patch.object(discover_reviews, "pr_shas", return_value=("0" * 40, "1" * 40)), \
                mock.patch.object(discover_reviews, "ensure_checkout", return_value=pathlib.Path("/x/a/repo")), \
                mock.patch("subprocess.run") as run:
                argv = ["discover-reviews", "--allow", "a/repo", "--checkout-root", "/x",
                       "--work-root", d]
                with mock.patch.object(sys, "argv", argv):
                    discover_reviews.main()
                run.assert_not_called()


class CapValidationTests(unittest.TestCase):
    """Found by the review council: a negative --max-per-run was accepted,
    and allowed[:budget] with a negative budget admits almost the entire
    eligible list instead of enforcing a smaller ceiling."""

    def test_negative_max_per_run_is_rejected(self):
        import subprocess
        r = subprocess.run(["python3", str(SCRIPT), "--allow", "a/b",
                           "--checkout-root", "/x", "--work-root", "/tmp/x",
                           "--max-per-run", "-5"], capture_output=True, text=True)
        self.assertNotEqual(0, r.returncode)

    def test_zero_max_age_days_is_rejected(self):
        """Zero was previously a documented 'disable the filter' sentinel.
        The age filter is a hard ceiling now, with no escape hatch."""
        import subprocess
        r = subprocess.run(["python3", str(SCRIPT), "--allow", "a/b",
                           "--checkout-root", "/x", "--work-root", "/tmp/x",
                           "--max-age-days", "0"], capture_output=True, text=True)
        self.assertNotEqual(0, r.returncode)

    def test_positive_values_still_work(self):
        self.assertEqual(7, discover_reviews._positive_int("7"))


class RepeatDiscoveryTests(unittest.TestCase):
    """Found by the review council: re-discovering an unchanged PR still
    incremented admitted whenever build-review-graph exited 0, even though
    its own idempotency keys made that a no-op rebuild. There is no cap
    for this to silently consume anymore, but the "admitted" count in the
    printed summary should still reflect genuinely new work, not a repeat."""

    def test_an_already_staged_operation_is_not_counted_as_newly_admitted(self):
        import sys
        with tempfile.TemporaryDirectory() as d:
            work_root = pathlib.Path(d)
            pr = fake_pr("a/repo", 1)
            op_id = discover_reviews.operation_id("a/repo", 1, "1" * 40)
            # Simulate an earlier run having already staged this exact head.
            (work_root / op_id).mkdir(parents=True)

            with mock.patch.object(discover_reviews, "search_assigned_prs", return_value=[pr]), \
                mock.patch.object(discover_reviews, "pr_shas", return_value=("0" * 40, "1" * 40)), \
                mock.patch.object(discover_reviews, "ensure_checkout", return_value=pathlib.Path("/x/a/repo")), \
                mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                argv = ["discover-reviews", "--allow", "a/repo", "--checkout-root", "/x",
                       "--work-root", str(work_root), "--once-for-real"]
                with mock.patch.object(sys, "argv", argv):
                    code = discover_reviews.main()
            self.assertEqual(0, code)
            # build-review-graph is still invoked (it owns the real
            # idempotency key), but the summary should not call this new.
            calls = [c for c in run.call_args_list if "build-review-graph" in " ".join(c.args[0])]
            self.assertEqual(1, len(calls))


class SearchFailureTests(unittest.TestCase):
    """Found by the review council: gh search prs failing and gh search prs
    finding nothing both returned [] and both exited 0. A scheduled run
    cannot tell "nothing assigned" from "stopped working" from its exit code."""

    def test_a_failed_search_returns_none_not_an_empty_list(self):
        with mock.patch.object(discover_reviews, "gh") as gh:
            gh.return_value = mock.Mock(returncode=1, stdout="", stderr="auth error")
            result = discover_reviews.search_assigned_prs(7)
        self.assertIsNone(result)

    def test_a_search_with_no_results_returns_an_empty_list(self):
        with mock.patch.object(discover_reviews, "gh") as gh:
            gh.return_value = mock.Mock(returncode=0, stdout="[]", stderr="")
            result = discover_reviews.search_assigned_prs(7)
        self.assertEqual([], result)

    def test_main_exits_nonzero_when_search_fails(self):
        import sys
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(discover_reviews, "search_assigned_prs", return_value=None):
                argv = ["discover-reviews", "--allow", "a/repo", "--checkout-root", "/x",
                       "--work-root", d, "--once-for-real"]
                with mock.patch.object(sys, "argv", argv):
                    code = discover_reviews.main()
        self.assertNotEqual(0, code)


class AllowFileTests(unittest.TestCase):
    """bootstrap/cutover-repo writes a flat allowlist file; this reads it
    fresh on every run so a cutover takes effect with no restart."""

    def test_allow_file_alone_is_sufficient(self):
        import sys
        with tempfile.TemporaryDirectory() as d:
            allow_file = pathlib.Path(d) / "allow.txt"
            allow_file.write_text("a/repo\n")
            with mock.patch.object(discover_reviews, "search_assigned_prs",
                                   return_value=[fake_pr("a/repo", 1)]), \
                mock.patch.object(discover_reviews, "pr_shas", return_value=("0" * 40, "1" * 40)), \
                mock.patch.object(discover_reviews, "ensure_checkout", return_value=pathlib.Path("/x/a/repo")), \
                mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                argv = ["discover-reviews", "--allow-file", str(allow_file),
                       "--checkout-root", "/x", "--work-root", d, "--once-for-real"]
                with mock.patch.object(sys, "argv", argv):
                    code = discover_reviews.main()
            self.assertEqual(0, code)

    def test_neither_allow_nor_allow_file_is_an_error(self):
        import sys
        with tempfile.TemporaryDirectory() as d:
            argv = ["discover-reviews", "--checkout-root", "/x", "--work-root", d]
            with mock.patch.object(sys, "argv", argv):
                code = discover_reviews.main()
            self.assertNotEqual(0, code)

    def test_a_missing_allow_file_is_not_a_crash(self):
        """cutover-repo may not have run yet; an absent file means an empty
        scope, which fails closed on its own, not a traceback."""
        import sys
        with tempfile.TemporaryDirectory() as d:
            argv = ["discover-reviews", "--allow-file", str(pathlib.Path(d) / "nope.txt"),
                   "--checkout-root", "/x", "--work-root", d]
            with mock.patch.object(sys, "argv", argv):
                code = discover_reviews.main()
            self.assertEqual(2, code)

    def test_allow_and_allow_file_merge(self):
        import sys
        with tempfile.TemporaryDirectory() as d:
            allow_file = pathlib.Path(d) / "allow.txt"
            allow_file.write_text("b/repo\n")
            with mock.patch.object(discover_reviews, "search_assigned_prs",
                                   return_value=[fake_pr("a/repo", 1), fake_pr("b/repo", 2)]), \
                mock.patch.object(discover_reviews, "pr_shas", return_value=("0" * 40, "1" * 40)), \
                mock.patch.object(discover_reviews, "ensure_checkout", return_value=pathlib.Path("/x/a/repo")), \
                mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                argv = ["discover-reviews", "--allow", "a/repo", "--allow-file", str(allow_file),
                       "--checkout-root", "/x", "--work-root", d, "--once-for-real"]
                with mock.patch.object(sys, "argv", argv):
                    discover_reviews.main()
                calls = [c for c in run.call_args_list if "build-review-graph" in " ".join(c.args[0])]
                self.assertEqual(2, len(calls), "both a/repo and b/repo should be admitted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
