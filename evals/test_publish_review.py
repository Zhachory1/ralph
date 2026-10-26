"""The deterministic publisher: no model, no prompt, posts what synthesis decided.

No network, no Kanban board: these test the pure logic (body parsing, marker
format, verdict-to-event mapping) that doesn't need a live task or a live PR.
The guarantees that need both -- moved-head refusal, incomplete-task refusal,
dry-run output -- were proven by hand against a real board and a real PR
before this file existed; see the PR body for that record.

    python3 evals/test_publish_review.py
"""

import importlib.machinery
import importlib.util
import pathlib
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bootstrap" / "publish-review"

# No .py suffix, so spec_from_file_location can't infer a loader on its own.
_loader = importlib.machinery.SourceFileLoader("publish_review", str(SCRIPT))
_spec = importlib.util.spec_from_loader("publish_review", _loader)
publish_review = importlib.util.module_from_spec(_spec)
_loader.exec_module(publish_review)


class MarkerTests(unittest.TestCase):
    """The one string every reviewer, old and new, checks before writing."""

    def test_marker_matches_the_fleet_byte_for_byte(self):
        sha = "a" * 40
        self.assertEqual(f"<!-- ai-pr-automation head={sha} -->",
                         publish_review.marker_for(sha))

    def test_marker_is_unique_per_head(self):
        self.assertNotEqual(publish_review.marker_for("a" * 40),
                            publish_review.marker_for("b" * 40))


class VerdictMappingTests(unittest.TestCase):
    def test_all_three_verdicts_map(self):
        self.assertEqual("APPROVE", publish_review.VERDICT_TO_EVENT["approve"])
        self.assertEqual("REQUEST_CHANGES", publish_review.VERDICT_TO_EVENT["request-changes"])
        self.assertEqual("COMMENT", publish_review.VERDICT_TO_EVENT["needs-info"])

    def test_no_fourth_verdict_exists(self):
        """A synthesis card with an unrecognized verdict must be refused, not
        guessed into a default -- this is the boundary that enforces it."""
        self.assertEqual(3, len(publish_review.VERDICT_TO_EVENT))


class BodyParsingTests(unittest.TestCase):
    """The plain key: value shape build-review-graph writes into card bodies."""

    def test_parses_the_real_shape(self):
        body = ("operation_id: review-abc123\nrepo: owner/name\nnumber: 42\n"
               "base_sha: " + "0" * 40 + "\nhead_sha: " + "1" * 40 + "\nrole: synthesis\n")
        fields = publish_review.parse_body(body)
        self.assertEqual("owner/name", fields["repo"])
        self.assertEqual("42", fields["number"])
        self.assertEqual("1" * 40, fields["head_sha"])

    def test_a_short_sha_is_rejected_by_the_pattern(self):
        self.assertIsNone(publish_review.SHA_RE.match("abc123"))
        self.assertIsNotNone(publish_review.SHA_RE.match("a" * 40))

    def test_malformed_body_yields_no_fields(self):
        self.assertEqual({}, publish_review.parse_body("not a key value body at all"))


class HermesArgvTests(unittest.TestCase):
    """The docker-exec bridge, and the bug caught testing it live: forwarding
    an unset host HERMES_HOME blanked out the value the container already had
    correctly, because `docker exec -e HERMES_HOME=` sets it to empty rather
    than leaving it alone."""

    def test_runs_locally_without_a_container(self):
        import os
        old = os.environ.pop("RALPH_HERMES_CONTAINER", None)
        try:
            self.assertEqual(["hermes", "kanban", "show"],
                             publish_review.hermes_argv("kanban", "show"))
        finally:
            if old is not None:
                os.environ["RALPH_HERMES_CONTAINER"] = old

    def test_does_not_forward_an_unset_home(self):
        import os
        old_container = os.environ.get("RALPH_HERMES_CONTAINER")
        old_home = os.environ.pop("HERMES_HOME", None)
        os.environ["RALPH_HERMES_CONTAINER"] = "ralph-hermes-1"
        try:
            argv = publish_review.hermes_argv("kanban", "show")
            self.assertNotIn("-e", argv, "no HERMES_HOME should be forwarded when unset")
            self.assertEqual(["docker", "exec", "ralph-hermes-1", "hermes", "kanban", "show"], argv)
        finally:
            if old_container is not None:
                os.environ["RALPH_HERMES_CONTAINER"] = old_container
            else:
                os.environ.pop("RALPH_HERMES_CONTAINER", None)
            if old_home is not None:
                os.environ["HERMES_HOME"] = old_home

    def test_forwards_a_set_home(self):
        import os
        old_container = os.environ.get("RALPH_HERMES_CONTAINER")
        old_home = os.environ.get("HERMES_HOME")
        os.environ["RALPH_HERMES_CONTAINER"] = "ralph-hermes-1"
        os.environ["HERMES_HOME"] = "/home/hermes/.hermes"
        try:
            argv = publish_review.hermes_argv("kanban", "show")
            self.assertIn("-e", argv)
            self.assertIn("HERMES_HOME=/home/hermes/.hermes", argv)
        finally:
            if old_container is not None:
                os.environ["RALPH_HERMES_CONTAINER"] = old_container
            else:
                os.environ.pop("RALPH_HERMES_CONTAINER", None)
            if old_home is not None:
                os.environ["HERMES_HOME"] = old_home
            else:
                os.environ.pop("HERMES_HOME", None)


class DismissedReviewTests(unittest.TestCase):
    """Found by a review swarm: the fleet's own filter excludes DISMISSED,
    so a dismissed marked review silently stopped counting as already-posted
    -- a second POST would carry an identical marker on an identical head."""

    def test_pending_is_excluded_dismissed_is_not(self):
        import re
        # The jq filter string itself carries the fix; read it directly
        # rather than re-deriving gh's own filtering behavior.
        src = publish_review.__file__
        text = pathlib.Path(src).read_text() if pathlib.Path(src).exists() else \
            pathlib.Path(publish_review.__loader__.path).read_text()
        self.assertIn('state != "PENDING"', text)
        self.assertNotIn('state != "DISMISSED"', text)


class TimeoutHandlingTests(unittest.TestCase):
    """Found by a review swarm: subprocess.run(..., timeout=...) raises
    rather than returning, which would crash past the recheck-then-RECONCILE
    path these functions exist to feed into."""

    def test_post_review_timeout_returns_none_not_an_exception(self):
        import subprocess
        from unittest import mock
        with mock.patch("subprocess.run",
                        side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=60)):
            result = publish_review.post_review("a/b", 1, "0" * 40, "APPROVE", "x")
        self.assertIsNone(result)

    def test_gh_timeout_returns_failed_completedprocess_not_an_exception(self):
        import subprocess
        from unittest import mock
        with mock.patch("subprocess.run",
                        side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=60)):
            result = publish_review.gh("api", "user")
        self.assertEqual(1, result.returncode)


class LoginAssertionTests(unittest.TestCase):
    """Found by a review swarm: --login was trusted as the posting identity
    instead of checked against it, so a wrong --login (or gh changing
    accounts between the check and the POST) silently broke the exactly-once
    guarantee, which checks one identity while writing under another."""

    def test_login_help_text_says_it_is_checked_not_trusted(self):
        import subprocess
        r = subprocess.run(["python3", str(SCRIPT), "--help"],
                           capture_output=True, text=True)
        self.assertIn("checked", r.stdout)
        self.assertIn("never trusted", r.stdout)


class OperationLockTests(unittest.TestCase):
    """Found by a review swarm: the marker check and the POST were two
    separate gh calls with nothing between them, so two concurrent
    invocations for the same task could both see the marker absent before
    either posted."""

    def test_second_concurrent_lock_is_refused(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"TMPDIR": d}):
                with publish_review.operation_lock("t_test123"):
                    with self.assertRaises(SystemExit) as ctx:
                        with publish_review.operation_lock("t_test123"):
                            pass
                    self.assertIn("already running", str(ctx.exception))

    def test_different_tasks_do_not_contend(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"TMPDIR": d}):
                with publish_review.operation_lock("t_aaa"):
                    with publish_review.operation_lock("t_bbb"):
                        pass  # must not raise

    def test_lock_releases_on_normal_exit(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"TMPDIR": d}):
                with publish_review.operation_lock("t_ccc"):
                    pass
                with publish_review.operation_lock("t_ccc"):
                    pass  # must not raise; the first lock released


if __name__ == "__main__":
    unittest.main(verbosity=2)
