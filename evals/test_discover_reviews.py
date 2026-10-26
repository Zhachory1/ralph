"""Discovery on a timer: the first thing that acts without a human.

No network -- these stub gh and build-review-graph to test the admission
logic itself: the allowlist, the per-run cap, and the daily cap persisted
across invocations. The real discovery and real graph-building were proven
by hand against a live board and a real PR; see the PR body for that record.

    python3 evals/test_discover_reviews.py
"""

import importlib.machinery
import importlib.util
import json
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
                mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                import sys
                argv = ["discover-reviews", "--allow", "a/allowed", "--checkout", "/x",
                       "--work-root", str(work_root), "--once-for-real"]
                with mock.patch.object(sys, "argv", argv):
                    discover_reviews.main()
                # Only one build-review-graph call, for the allowed repo.
                calls = [c for c in run.call_args_list if "build-review-graph" in " ".join(c.args[0])]
                self.assertEqual(1, len(calls))
                self.assertIn("a/allowed", calls[0].args[0])


class CapTests(unittest.TestCase):
    def _run(self, work_root, prs, max_per_run=10, daily_cap=25):
        import sys
        with mock.patch.object(discover_reviews, "search_assigned_prs", return_value=prs), \
            mock.patch.object(discover_reviews, "pr_shas", return_value=("0" * 40, "1" * 40)), \
            mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            argv = ["discover-reviews", "--allow", "a/repo", "--checkout", "/x",
                   "--work-root", str(work_root), "--once-for-real",
                   "--max-per-run", str(max_per_run), "--daily-cap", str(daily_cap)]
            with mock.patch.object(sys, "argv", argv):
                discover_reviews.main()
        return [c for c in run.call_args_list if "build-review-graph" in " ".join(c.args[0])]

    def test_per_run_cap_limits_admission_even_with_more_eligible(self):
        with tempfile.TemporaryDirectory() as d:
            prs = [fake_pr("a/repo", i) for i in range(1, 6)]
            calls = self._run(pathlib.Path(d), prs, max_per_run=2)
            self.assertEqual(2, len(calls))

    def test_daily_cap_persists_across_invocations(self):
        with tempfile.TemporaryDirectory() as d:
            work_root = pathlib.Path(d)
            (work_root / discover_reviews.STATE_FILE).write_text(
                json.dumps({"date": discover_reviews.today(), "count": 24}))
            calls = self._run(work_root, [fake_pr("a/repo", 1), fake_pr("a/repo", 2)],
                             max_per_run=10, daily_cap=25)
            # Only one slot left today, so only one graph is built even
            # though two PRs are eligible.
            self.assertEqual(1, len(calls))

    def test_cap_reached_admits_nothing_and_does_not_call_search(self):
        with tempfile.TemporaryDirectory() as d:
            work_root = pathlib.Path(d)
            (work_root / discover_reviews.STATE_FILE).write_text(
                json.dumps({"date": discover_reviews.today(), "count": 25}))
            with mock.patch.object(discover_reviews, "search_assigned_prs") as search:
                import sys
                argv = ["discover-reviews", "--allow", "a/repo", "--checkout", "/x",
                       "--work-root", str(work_root), "--once-for-real", "--daily-cap", "25"]
                with mock.patch.object(sys, "argv", argv):
                    discover_reviews.main()
                search.assert_not_called()

    def test_a_stale_date_resets_the_count(self):
        with tempfile.TemporaryDirectory() as d:
            work_root = pathlib.Path(d)
            (work_root / discover_reviews.STATE_FILE).write_text(
                json.dumps({"date": "2020-01-01", "count": 25}))
            calls = self._run(work_root, [fake_pr("a/repo", 1)])
            self.assertEqual(1, len(calls), "yesterday's count must not block today")


class DryRunTests(unittest.TestCase):
    def test_without_once_for_real_nothing_is_built(self):
        import sys
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(discover_reviews, "search_assigned_prs",
                                   return_value=[fake_pr("a/repo", 1)]), \
                mock.patch.object(discover_reviews, "pr_shas", return_value=("0" * 40, "1" * 40)), \
                mock.patch("subprocess.run") as run:
                argv = ["discover-reviews", "--allow", "a/repo", "--checkout", "/x",
                       "--work-root", d]
                with mock.patch.object(sys, "argv", argv):
                    discover_reviews.main()
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
