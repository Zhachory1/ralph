"""Discovery on a timer: the first thing that acts without a human.

No network -- these stub gh and the review-workflow handoff to test the
admission logic itself: the allowlist and the per-run cap. There is no
daily cap by direct instruction, so these tests check that nothing in this
file gates on a count across invocations, not that a count stays under
some number. The real discovery was proven by hand against a live board
and a real PR; see the PR body for that record.

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


def setUpModule():
    """Tests that do not exercise the board read see an empty, readable board."""
    patcher = mock.patch.object(discover_reviews, "review_graphs", return_value=(set(), False))
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


def fake_pr(repo, number, title="x"):
    return {"repository": {"nameWithOwner": repo}, "number": number, "title": title,
           "url": f"https://github.com/{repo}/pull/{number}", "updatedAt": "2026-01-01T00:00:00Z"}


def is_handoff(argv):
    """The one hermes kanban create call that hands a discovered head off
    to review-workflow, identified by its fixed assignee."""
    return "kanban" in argv and "create" in argv and "ralph-review-workflow" in argv


def handoff_calls(calls):
    """calls: an iterable of mock.call objects (run.call_args_list) or of
    already-joined argv lists, whichever the test already collected."""
    return [c for c in calls if is_handoff(c.args[0] if hasattr(c, "args") else c)]


def body_field(argv, field):
    """One `field: value` line from the --body argument of a handoff call."""
    body = argv[argv.index("--body") + 1]
    for line in body.splitlines():
        if line.startswith(field + ": "):
            return line.split(": ", 1)[1]
    return None


class AllowlistTests(unittest.TestCase):
    """The rejected alternative is the fleet's board-engine shape: no
    "all accessible repos" exists anywhere in this script. owner/* covers
    one owner only."""

    def test_only_allowlisted_repos_are_admitted(self):
        with tempfile.TemporaryDirectory() as d:
            work_root = pathlib.Path(d)
            with mock.patch.object(discover_reviews, "search_assigned_prs",
                                   return_value=[fake_pr("a/allowed", 1), fake_pr("b/not-allowed", 2)]), \
                mock.patch.object(discover_reviews, "pr_shas", return_value=("0" * 40, "1" * 40)), \
                mock.patch.object(discover_reviews, "pr_text", return_value=""), \
                mock.patch.object(discover_reviews, "reviews_by_me", return_value={}), \
                mock.patch.object(discover_reviews, "ensure_checkout", return_value=pathlib.Path("/x/a/repo")), \
                mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                import sys
                argv = ["discover-reviews", "--allow", "a/allowed", "--checkout-root", "/x",
                       "--work-root", str(work_root), "--once-for-real"]
                with mock.patch.object(sys, "argv", argv):
                    discover_reviews.main()
                calls = handoff_calls(run.call_args_list)
                self.assertEqual(1, len(calls))
                self.assertEqual("a/allowed", body_field(calls[0].args[0], "repo"))


    def test_owner_wildcard_covers_that_owner_only(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(discover_reviews, "search_assigned_prs",
                                   return_value=[fake_pr("a/one", 1), fake_pr("a/two", 2),
                                                 fake_pr("ab/x", 3), fake_pr("b/y", 4)]), \
                mock.patch.object(discover_reviews, "pr_shas", return_value=("0" * 40, "1" * 40)), \
                mock.patch.object(discover_reviews, "pr_text", return_value=""), \
                mock.patch.object(discover_reviews, "reviews_by_me", return_value={}), \
                mock.patch.object(discover_reviews, "ensure_checkout", return_value=pathlib.Path("/x/a")), \
                mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                import sys
                argv = ["discover-reviews", "--allow", "a/*", "--checkout-root", "/x",
                       "--work-root", d, "--once-for-real"]
                with mock.patch.object(sys, "argv", argv):
                    discover_reviews.main()
                repos = [body_field(c.args[0], "repo") for c in handoff_calls(run.call_args_list)]
                self.assertEqual(["a/one", "a/two"], repos)


class ReviewRoundCapTests(unittest.TestCase):
    """No cap on review rounds: each new head is reviewed once."""

    def _admitted(self, heads):
        import sys
        with tempfile.TemporaryDirectory() as d, \
            mock.patch.object(discover_reviews, "search_assigned_prs", return_value=[fake_pr("a/repo", 1)]), \
            mock.patch.object(discover_reviews, "pr_shas", return_value=("0" * 40, "1" * 40)), \
                mock.patch.object(discover_reviews, "pr_text", return_value=""), \
            mock.patch.object(discover_reviews, "reviews_by_me", return_value=(None if heads is None else {h: 0 for h in heads})), \
            mock.patch.object(discover_reviews, "ensure_checkout", return_value=pathlib.Path("/x")), \
            mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            argv = ["discover-reviews", "--allow", "a/repo", "--checkout-root", "/x",
                    "--work-root", d, "--once-for-real"]
            with mock.patch.object(sys, "argv", argv):
                discover_reviews.main()
            return len(handoff_calls(run.call_args_list))

    def test_a_fourth_round_is_admitted(self):
        self.assertEqual(1, self._admitted({"a" * 40, "b" * 40, "c" * 40}))

    def test_an_already_reviewed_head_is_not_reviewed_again(self):
        """Each head is reviewed once."""
        self.assertEqual(0, self._admitted({"a" * 40, "b" * 40, "1" * 40}))

    def test_unreadable_reviews_skip_rather_than_spend(self):
        self.assertEqual(0, self._admitted(None))


class ReviewSpacingTests(unittest.TestCase):
    """A new head is reviewed on the next pass, however recent the last
    review: a fix pushed in answer to a review must not wait a day for one."""

    NOW = 1_800_000_000

    def admitted(self, reviews):
        import sys
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(discover_reviews, "search_assigned_prs", return_value=[fake_pr("a/repo", 1)]), \
                mock.patch.object(discover_reviews, "pr_shas", return_value=("0" * 40, "1" * 40)), \
                mock.patch.object(discover_reviews, "pr_text", return_value=""), \
                mock.patch.object(discover_reviews, "pr_checks", return_value=None), \
                mock.patch.object(discover_reviews, "pr_threads", return_value=None), \
                mock.patch.object(discover_reviews, "reviews_by_me", return_value=reviews), \
                mock.patch.object(discover_reviews, "ensure_checkout", return_value=pathlib.Path("/x")), \
                mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            argv = ["discover-reviews", "--allow", "a/repo", "--checkout-root", "/x",
                    "--work-root", d, "--once-for-real"]
            with mock.patch.object(sys, "argv", argv):
                discover_reviews.main()
            return len(handoff_calls(run.call_args_list))

    def ago(self, hours):
        return self.NOW - hours * 3600

    def test_a_new_head_soon_after_a_review_is_reviewed(self):
        self.assertEqual(1, self.admitted({"a" * 40: self.ago(1)}))

    def test_a_new_head_waits_while_a_review_is_in_flight(self):
        """#81 review: one graph per PR at a time, so pushes cannot queue many."""
        with mock.patch.object(discover_reviews, "review_graphs", return_value=({"aaaaaaaa"}, True)):
            self.assertEqual(0, self.admitted({}))

    def test_an_unreadable_board_skips_rather_than_spends(self):
        """#85 review: a failed board read bypassed the in-flight gate."""
        with mock.patch.object(discover_reviews, "review_graphs", return_value=None):
            self.assertEqual(0, self.admitted({}))

    def test_many_finished_graphs_do_not_block_a_new_head(self):
        with mock.patch.object(discover_reviews, "review_graphs",
                               return_value=({"aaaaaaaa", "bbbbbbbb", "cccccccc"}, False)):
            self.assertEqual(1, self.admitted({}))

    def test_an_already_reviewed_head_is_not_reviewed_again(self):
        self.assertEqual(0, self.admitted({"1" * 40: self.ago(1)}))


class ReviewGraphsStatusTests(unittest.TestCase):
    """The real review_graphs, against a fake board. A synthesis card that is
    blocked (a lens returned malformed output and needs a human) never
    finishes on its own, so counting it as in flight starved every later head
    of the PR: PR #132 was never reviewed again until the card was archived."""

    def graphs(self, *cards):
        import json
        loader = importlib.machinery.SourceFileLoader("dr_real", str(SCRIPT))
        spec = importlib.util.spec_from_loader("dr_real", loader)
        real = importlib.util.module_from_spec(spec)
        loader.exec_module(real)
        board = json.dumps({"tasks": [
            {"title": f"Synthesize review o/r#7 @ {sha}", "status": status}
            for sha, status in cards]})
        done = mock.Mock(returncode=0, stdout=board)
        with mock.patch.object(real.subprocess, "run", return_value=done):
            return real.review_graphs("queues", "o/r", 7)

    def test_a_running_synthesis_is_in_flight(self):
        self.assertEqual(({"aaaaaaaa"}, True), self.graphs(("aaaaaaaa", "running")))

    def test_a_ready_synthesis_is_in_flight(self):
        self.assertTrue(self.graphs(("aaaaaaaa", "ready"))[1])

    def test_a_blocked_synthesis_is_not_in_flight(self):
        heads, running = self.graphs(("aaaaaaaa", "blocked"))
        self.assertFalse(running)

    def test_a_blocked_synthesis_still_counts_as_a_graph_for_its_own_head(self):
        """The same head is not rebuilt behind a human's back; only a newer one proceeds."""
        heads, _ = self.graphs(("aaaaaaaa", "blocked"))
        self.assertIn("aaaaaaaa", heads)

    def test_done_and_archived_are_not_in_flight(self):
        self.assertFalse(self.graphs(("aaaaaaaa", "done"), ("bbbbbbbb", "archived"))[1])

    def test_a_blocked_card_does_not_hide_a_running_one(self):
        self.assertTrue(self.graphs(("aaaaaaaa", "blocked"), ("bbbbbbbb", "running"))[1])


def load_real_discover():
    loader = importlib.machinery.SourceFileLoader("dr_real2", str(SCRIPT))
    spec = importlib.util.spec_from_loader("dr_real2", loader)
    real = importlib.util.module_from_spec(spec)
    loader.exec_module(real)
    return real


class StaleBlockedSynthesisTests(unittest.TestCase):
    """A blocked synthesis card for an older head stays resumable. Unblocking
    it later would run a second review for one PR, so admitting a newer head
    archives it (PR #140 review). Same-head and in-flight cards are left alone."""

    HEAD = "b" * 40

    def card(self, id_, sha, status):
        return {"id": id_, "title": f"Synthesize review o/r#7 @ {sha}", "status": status}

    def stale(self, *cards):
        import json
        real = load_real_discover()
        board = mock.Mock(returncode=0, stdout=json.dumps({"tasks": list(cards)}))
        with mock.patch.object(real.subprocess, "run", return_value=board):
            return real.stale_blocked_synthesis("queues", "o/r", 7, self.HEAD)

    def test_a_blocked_card_for_an_older_head_is_stale(self):
        self.assertEqual(["t_old"], self.stale(self.card("t_old", "aaaaaaaa", "blocked")))

    def test_a_blocked_card_for_the_current_head_is_kept(self):
        self.assertEqual([], self.stale(self.card("t_cur", "bbbbbbbb", "blocked")))

    def test_running_ready_done_and_archived_cards_are_kept(self):
        cards = [self.card(f"t_{s}", "aaaaaaaa", s) for s in ("running", "ready", "todo", "done", "archived")]
        self.assertEqual([], self.stale(*cards))

    def test_another_prs_blocked_card_is_kept(self):
        other = {"id": "t_x", "title": "Synthesize review o/r#8 @ aaaaaaaa", "status": "blocked"}
        self.assertEqual([], self.stale(other))

    def test_an_unreadable_board_finds_nothing_to_archive(self):
        real = load_real_discover()
        with mock.patch.object(real.subprocess, "run", return_value=mock.Mock(returncode=1, stdout="")):
            self.assertEqual([], real.stale_blocked_synthesis("queues", "o/r", 7, self.HEAD))


class ArchiveOnAdmissionTests(unittest.TestCase):
    """Wired into main: archived after the new head's handoff card is
    created, not before."""

    def run_main(self, stale_ids, build_rc=0, archive_rc=0):
        import sys
        calls = []

        def fake_run(argv, **kw):
            calls.append(list(argv))
            if is_handoff(argv):
                return mock.Mock(returncode=build_rc, stdout="", stderr="boom" if build_rc else "")
            if "archive" in argv:
                return mock.Mock(returncode=archive_rc, stdout="", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(discover_reviews, "search_assigned_prs", return_value=[fake_pr("a/repo", 1)]), \
                mock.patch.object(discover_reviews, "pr_shas", return_value=("0" * 40, "1" * 40)), \
                mock.patch.object(discover_reviews, "pr_text", return_value=""), \
                mock.patch.object(discover_reviews, "pr_checks", return_value=None), \
                mock.patch.object(discover_reviews, "pr_threads", return_value=None), \
                mock.patch.object(discover_reviews, "reviews_by_me", return_value={}), \
                mock.patch.object(discover_reviews, "ensure_checkout", return_value=pathlib.Path("/x")), \
                mock.patch.object(discover_reviews, "stale_blocked_synthesis", return_value=stale_ids), \
                mock.patch("subprocess.run", side_effect=fake_run):
            argv = ["discover-reviews", "--allow", "a/repo", "--checkout-root", "/x",
                    "--work-root", d, "--once-for-real"]
            with mock.patch.object(sys, "argv", argv):
                rc = discover_reviews.main()
        return rc, calls

    def archives(self, calls):
        return [c for c in calls if "kanban" in c and "archive" in c]

    def test_stale_blocked_cards_are_archived_once_the_new_graph_is_built(self):
        rc, calls = self.run_main(["t_old1", "t_old2"])
        self.assertEqual(0, rc)
        archive = self.archives(calls)
        self.assertEqual(1, len(archive))
        self.assertEqual(archive[0][-2:], ["t_old1", "t_old2"])
        build = next(i for i, c in enumerate(calls) if is_handoff(c))
        self.assertGreater(calls.index(archive[0]), build)

    def test_nothing_is_archived_when_there_is_nothing_stale(self):
        _, calls = self.run_main([])
        self.assertEqual([], self.archives(calls))

    def test_nothing_is_archived_when_the_new_graph_failed_to_build(self):
        _, calls = self.run_main(["t_old"], build_rc=1)
        self.assertEqual([], self.archives(calls))

    def test_a_failed_archive_does_not_fail_discovery(self):
        rc, _ = self.run_main(["t_old"], archive_rc=1)
        self.assertEqual(0, rc)


class PerRunCapTests(unittest.TestCase):
    """The one remaining admission bound: how many PRs one invocation can
    build, regardless of how many gh search prs returns."""

    def _run(self, work_root, prs, max_per_run=10):
        import sys
        with mock.patch.object(discover_reviews, "search_assigned_prs", return_value=prs), \
            mock.patch.object(discover_reviews, "pr_shas", return_value=("0" * 40, "1" * 40)), \
                mock.patch.object(discover_reviews, "pr_text", return_value=""), \
                mock.patch.object(discover_reviews, "reviews_by_me", return_value={}), \
                mock.patch.object(discover_reviews, "ensure_checkout", return_value=pathlib.Path("/x/a/repo")), \
            mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            argv = ["discover-reviews", "--allow", "a/repo", "--checkout-root", "/x",
                   "--work-root", str(work_root), "--once-for-real",
                   "--max-per-run", str(max_per_run)]
            with mock.patch.object(sys, "argv", argv):
                discover_reviews.main()
        return handoff_calls(run.call_args_list)

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
                mock.patch.object(discover_reviews, "pr_text", return_value=""), \
                mock.patch.object(discover_reviews, "reviews_by_me", return_value={}), \
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
    incremented admitted whenever the handoff call exited 0, even though
    its own idempotency key made that a no-op rebuild. There is no cap
    for this to silently consume anymore, but the "admitted" count in the
    printed summary should still reflect genuinely new work, not a repeat."""

    def _built(self, prs, reviewed=(), on_board=(), max_per_run=1):
        import sys
        with tempfile.TemporaryDirectory() as d, \
            mock.patch.object(discover_reviews, "search_assigned_prs", return_value=prs), \
            mock.patch.object(discover_reviews, "pr_shas", return_value=("0" * 40, "1" * 40)), \
            mock.patch.object(discover_reviews, "pr_text", return_value=""), \
            mock.patch.object(discover_reviews, "reviews_by_me",
                              side_effect=lambda r, n: {"1" * 40: 0} if n in reviewed else {}), \
            mock.patch.object(discover_reviews, "review_graphs",
                              side_effect=lambda b, r, n: ({"11111111"} if n in on_board else set(), False)), \
            mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            argv = ["discover-reviews", "--allow", "a/repo", "--checkout-root", "/x",
                    "--work-root", d, "--once-for-real", "--max-per-run", str(max_per_run)]
            with mock.patch.object(sys, "argv", argv):
                discover_reviews.main()
            return [body_field(c.args[0], "pr") for c in handoff_calls(run.call_args_list)]

    def setUp(self):
        patcher = mock.patch.object(discover_reviews, "ensure_checkout", return_value=pathlib.Path("/x"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reviewed_prs_do_not_use_up_the_per_run_cap(self):
        """Live: 10 already-reviewed PRs filled every pass; 90 were never reached."""
        prs = [fake_pr("a/repo", n) for n in (1, 2, 3)]
        self.assertEqual(["3"], self._built(prs, reviewed={1, 2}))

    def test_a_head_already_on_the_board_takes_no_slot(self):
        prs = [fake_pr("a/repo", n) for n in (1, 2)]
        self.assertEqual(["2"], self._built(prs, on_board={1}))

    def test_a_failed_checkout_does_not_spend_a_slot(self):
        """#89 review: an early failure must not starve the next PR."""
        prs = [fake_pr("a/repo", n) for n in (1, 2)]
        with mock.patch.object(discover_reviews, "ensure_checkout",
                               side_effect=lambda root, r, n, b, h: None if n == 1 else pathlib.Path("/x")):
            self.assertEqual(["2"], self._built(prs))

    def test_a_staged_head_without_a_graph_is_retried(self):
        """#89 review: a partial staging must not suppress the review forever."""
        self.assertEqual(["1"], self._built([fake_pr("a/repo", 1)]))


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
                mock.patch.object(discover_reviews, "pr_text", return_value=""), \
                mock.patch.object(discover_reviews, "reviews_by_me", return_value={}), \
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
                mock.patch.object(discover_reviews, "pr_text", return_value=""), \
                mock.patch.object(discover_reviews, "reviews_by_me", return_value={}), \
                mock.patch.object(discover_reviews, "ensure_checkout", return_value=pathlib.Path("/x/a/repo")), \
                mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                argv = ["discover-reviews", "--allow", "a/repo", "--allow-file", str(allow_file),
                       "--checkout-root", "/x", "--work-root", d, "--once-for-real"]
                with mock.patch.object(sys, "argv", argv):
                    discover_reviews.main()
                calls = handoff_calls(run.call_args_list)
                self.assertEqual(2, len(calls), "both a/repo and b/repo should be admitted")


class CloneTimeoutTests(unittest.TestCase):
    """The live case: ROKT/ml took longer than gh's 90s to clone, the
    TimeoutExpired escaped, and the whole pass crashed -- every pass, since
    the next one hit the same clone first."""

    def test_a_gh_timeout_is_a_failed_call_not_an_exception(self):
        import subprocess
        with mock.patch("subprocess.run",
                        side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=90)):
            r = discover_reviews.gh("repo", "clone", "o/r", "/x")
        self.assertNotEqual(0, r.returncode)

    def test_clone_gets_the_long_timeout(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("subprocess.run",
                           return_value=subprocess.CompletedProcess([], 1, "", "x")) as run:
            discover_reviews.ensure_checkout(pathlib.Path(d), "o/r", 1, "a" * 40, "b" * 40)
        self.assertEqual(discover_reviews.CLONE_TIMEOUT, run.call_args.kwargs["timeout"])

    def test_a_failed_clone_leaves_nothing_behind(self):
        """A half-written clone has .git, so the next pass would skip the
        clone and fetch into a broken repository forever."""
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)

            def half_clone(argv, **kw):
                (root / "o" / "r" / ".git").mkdir(parents=True)
                raise subprocess.TimeoutExpired(cmd="gh", timeout=kw["timeout"])

            with mock.patch("subprocess.run", side_effect=half_clone):
                self.assertIsNone(discover_reviews.ensure_checkout(root, "o/r", 1, "a" * 40, "b" * 40))
            self.assertFalse((root / "o" / "r").exists())

    def test_a_fetch_timeout_skips_the_pr(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / "o" / "r" / ".git").mkdir(parents=True)
            with mock.patch("subprocess.run",
                            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=300)):
                self.assertIsNone(discover_reviews.ensure_checkout(root, "o/r", 1, "a" * 40, "b" * 40))


class ChecksAndThreadsTests(unittest.TestCase):
    """Lenses asked for CI results and review discussion that were on the PR
    but never staged. These fetch both; a failure only drops that file."""

    def gh_returning(self, stdout, code=0):
        import subprocess
        return mock.patch.object(discover_reviews, "gh",
                                 return_value=subprocess.CompletedProcess([], code, stdout, ""))

    def test_checks_are_kept_even_while_pending(self):
        """gh exits 8 while checks are pending; its JSON is still valid."""
        with self.gh_returning('[{"name": "Validate", "bucket": "pending"}]', code=8):
            out = discover_reviews.pr_checks("o/r", 1)
        self.assertIn("Validate", out)

    def test_unreadable_checks_are_none(self):
        with self.gh_returning("not json", code=1):
            self.assertIsNone(discover_reviews.pr_checks("o/r", 1))

    def graphql(self, threads=(), comments=(), reviews=()):
        import json
        return json.dumps({"data": {"repository": {"pullRequest": {
            "reviewThreads": {"nodes": list(threads)},
            "comments": {"nodes": list(comments)},
            "reviews": {"nodes": list(reviews)}}}}})

    def test_own_account_is_excluded(self):
        """Re-reading its own earlier findings would echo them back as someone else's."""
        body = self.graphql(
            comments=[{"author": {"login": "zhach1"}, "body": "mine"},
                      {"author": {"login": "colleague"}, "body": "theirs"}],
            reviews=[{"author": {"login": "ZHACH1"}, "state": "COMMENTED", "body": "mine too"}],
            threads=[{"isResolved": False, "isOutdated": False, "path": "a.py", "line": 3,
                      "comments": {"nodes": [{"author": {"login": "zhach1"}, "body": "x"}]}}])
        with self.gh_returning(body), mock.patch.object(discover_reviews, "_login", return_value="zhach1"):
            out = discover_reviews.pr_threads("o/r", 1)
        self.assertIn("theirs", out)
        self.assertNotIn("mine", out)
        self.assertNotIn("a.py", out, "a thread with only our own comments is dropped")

    def test_thread_state_is_shown(self):
        body = self.graphql(threads=[{"isResolved": True, "isOutdated": False, "path": "a.py",
                                      "line": 3, "comments": {"nodes": [
                                          {"author": {"login": "c"}, "body": "fix this"}]}}])
        with self.gh_returning(body), mock.patch.object(discover_reviews, "_login", return_value="me"):
            out = discover_reviews.pr_threads("o/r", 1)
        self.assertIn("a.py:3 (resolved)", out)
        self.assertIn("fix this", out)

    def test_a_failed_read_is_none(self):
        with self.gh_returning("", code=1):
            self.assertIsNone(discover_reviews.pr_threads("o/r", 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
