"""Idempotency for the review graph: create-time keys, nothing else.

Hermes' own --help: "If a non-archived task with this key exists, its id is
returned instead of creating a duplicate." That is the only dedup mechanism
this script relies on -- there used to be a second guard here that also
treated an archived card as "already built" and refused to rebuild it, which
was backwards: archiving a card is the operator's own signal that the work
should be free to run again. Removed; these tests confirm create_card()
alone produces the right behavior in both directions, against a real hermes
kanban board, not a mock of one.

    python3 evals/test_build_review_graph.py
"""

import importlib.machinery
import importlib.util
import json
import pathlib
import subprocess
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bootstrap" / "build-review-graph"

_loader = importlib.machinery.SourceFileLoader("build_review_graph", str(SCRIPT))
_spec = importlib.util.spec_from_loader("build_review_graph", _loader)
build_review_graph = importlib.util.module_from_spec(_spec)
_loader.exec_module(build_review_graph)


class NoArchivedCardGuardTests(unittest.TestCase):
    """The guard this script used to have, and should not have: a function
    that read the board and decided a card was already handled. There is
    none now -- only create_card()'s own --idempotency-key."""

    def test_graph_status_does_not_exist(self):
        self.assertFalse(hasattr(build_review_graph, "graph_status"))

    def test_graph_read_error_does_not_exist(self):
        self.assertFalse(hasattr(build_review_graph, "GraphReadError"))

    def test_main_never_calls_kanban_list(self):
        """The old guard's own board read. Its absence from the source is
        the simplest proof nothing in main() re-derives the same check."""
        source = SCRIPT.read_text()
        self.assertNotIn('"list", "--archived"', source)


class CreateCardIdempotencyTests(unittest.TestCase):
    """create_card()'s own contract, which is now the only dedup mechanism:
    pass --idempotency-key through to hermes kanban create and return
    whatever id comes back -- Hermes decides whether that is a new task or
    an existing non-archived one."""

    def test_passes_the_idempotency_key_through(self):
        with mock.patch.object(build_review_graph, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(
                returncode=0, stdout=json.dumps({"id": "t_abc123"}))
            build_review_graph.create_card("queues", "title", "body",
                                           "ralph-lens-generalist", "review-x-generalist",
                                           "/tmp/some/workspace")
            args = hermes_mock.call_args.args
            self.assertIn("--idempotency-key", args)
            self.assertIn("review-x-generalist", args)

    def test_returns_whatever_id_hermes_returns(self):
        """A repeat call with the same key returns the SAME id if Hermes
        found a non-archived match -- this function has no opinion about
        whether that happened; it is Hermes' decision alone."""
        with mock.patch.object(build_review_graph, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(
                returncode=0, stdout=json.dumps({"id": "t_same_id"}))
            first = build_review_graph.create_card("queues", "t", "b", "p", "key", "/w")
            second = build_review_graph.create_card("queues", "t", "b", "p", "key", "/w")
        self.assertEqual(first, second)

    def test_a_create_failure_returns_none(self):
        with mock.patch.object(build_review_graph, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(returncode=1, stdout="", stderr="boom")
            result = build_review_graph.create_card("queues", "t", "b", "p", "key", "/w")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
