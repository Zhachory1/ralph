"""The graph-completeness guard: absent, partial, and complete are different things.

Hermes' own --help: "If a non-archived task with this key exists, its id is
returned instead of creating a duplicate." Confirmed live: archiving a card
and recreating one with its exact key creates a SECOND task, not the first
back. graph_status() closes that by checking archived cards too, independent
of the create-time dedup Hermes already does.

Two findings from a real review of this file fixed here: "any card with this
operation id" was too coarse -- a crash after the first role's card left
exactly that, and the old check declared the whole graph built, permanently
blocking a retry from creating the missing roles. And a read/parse failure
used to return the same thing as "confirmed absent", so a transient failure
could let the script recreate an already-archived operation.

    python3 evals/test_build_review_graph.py
"""

import importlib.machinery
import importlib.util
import json
import pathlib
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bootstrap" / "build-review-graph"

_loader = importlib.machinery.SourceFileLoader("build_review_graph", str(SCRIPT))
_spec = importlib.util.spec_from_loader("build_review_graph", _loader)
build_review_graph = importlib.util.module_from_spec(_spec)
_loader.exec_module(build_review_graph)


def card(op_id: str, role: str) -> dict:
    return {"body": f"operation_id: {op_id}\nrepo: a/b\nnumber: 1\nrole: {role}\n"}


class AbsentTests(unittest.TestCase):
    def test_no_matching_cards_is_absent(self):
        with mock.patch.object(build_review_graph, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(returncode=0, stdout="[]")
            status = build_review_graph.graph_status("queues", "review-abc", {"generalist"})
        self.assertEqual("absent", status)

    def test_a_different_operation_id_is_not_a_match(self):
        cards = [card("review-other", "generalist")]
        with mock.patch.object(build_review_graph, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(returncode=0, stdout=json.dumps(cards))
            status = build_review_graph.graph_status("queues", "review-abc", {"generalist"})
        self.assertEqual("absent", status)


class PartialGraphTests(unittest.TestCase):
    """The review's central finding: a crash mid-graph must resume, not be
    mistaken for a finished graph."""

    def test_one_role_out_of_three_is_partial_not_complete(self):
        cards = [card("review-abc", "generalist")]
        with mock.patch.object(build_review_graph, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(returncode=0, stdout=json.dumps(cards))
            status = build_review_graph.graph_status(
                "queues", "review-abc", {"generalist", "reliability", "mvp"})
        self.assertEqual("partial", status)

    def test_all_roles_but_missing_synthesis_is_partial(self):
        cards = [card("review-abc", r) for r in ("generalist", "reliability", "mvp")]
        with mock.patch.object(build_review_graph, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(returncode=0, stdout=json.dumps(cards))
            status = build_review_graph.graph_status(
                "queues", "review-abc", {"generalist", "reliability", "mvp"})
        self.assertEqual("partial", status)


class CompleteGraphTests(unittest.TestCase):
    def test_every_role_plus_synthesis_is_complete(self):
        cards = [card("review-abc", r) for r in
                ("generalist", "reliability", "mvp", "synthesis")]
        with mock.patch.object(build_review_graph, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(returncode=0, stdout=json.dumps(cards))
            status = build_review_graph.graph_status(
                "queues", "review-abc", {"generalist", "reliability", "mvp"})
        self.assertEqual("complete", status)

    def test_the_security_lens_is_part_of_the_expected_set_when_passed(self):
        """Complete means all EXPECTED roles -- security only counts as
        missing when it was actually expected (the conditional lens)."""
        cards = [card("review-abc", r) for r in
                ("generalist", "reliability", "mvp", "synthesis")]
        with mock.patch.object(build_review_graph, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(returncode=0, stdout=json.dumps(cards))
            status = build_review_graph.graph_status(
                "queues", "review-abc", {"generalist", "reliability", "mvp", "security"})
        self.assertEqual("partial", status, "security was expected and is missing")


class ReadFailureTests(unittest.TestCase):
    """The review's second finding: a failure to read must never be treated
    as confirmed absence, or a transient hiccup can recreate an already-
    archived operation."""

    def test_a_failed_hermes_call_raises_rather_than_returning_absent(self):
        with mock.patch.object(build_review_graph, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(returncode=1, stdout="", stderr="boom")
            with self.assertRaises(build_review_graph.GraphReadError):
                build_review_graph.graph_status("queues", "review-abc", {"generalist"})

    def test_malformed_json_raises_rather_than_returning_absent(self):
        with mock.patch.object(build_review_graph, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(returncode=0, stdout="not json")
            with self.assertRaises(build_review_graph.GraphReadError):
                build_review_graph.graph_status("queues", "review-abc", {"generalist"})

    def test_main_exits_nonzero_on_a_read_failure_rather_than_rebuilding(self):
        """The integration point: main() must not catch GraphReadError and
        silently fall through to building a duplicate graph."""
        import subprocess
        source = SCRIPT.read_text()
        self.assertIn("GraphReadError", source)
        self.assertIn("return 1", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
