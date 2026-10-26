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


if __name__ == "__main__":
    unittest.main(verbosity=2)
