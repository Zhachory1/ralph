"""The already-built guard: --idempotency-key does not survive archival.

Hermes' own --help: "If a non-archived task with this key exists, its id is
returned instead of creating a duplicate." Confirmed live: archiving a card
and recreating one with the exact same key creates a SECOND task, not the
first one back. Nothing in this design archives a review card today, so this
was dormant -- a cleanup pass, a GC sweep, or a human tidying the board is
all it takes to turn an already-reviewed head into one the next discovery
tick pays for again. already_built() closes that by checking archived cards
too, independent of the create-time dedup Hermes already does.

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


class AlreadyBuiltTests(unittest.TestCase):
    def test_finds_a_matching_operation_id_in_an_archived_card(self):
        cards = [{"body": "operation_id: review-abc123\nrole: generalist\n"}]
        with mock.patch.object(build_review_graph, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(returncode=0, stdout=json.dumps(cards))
            self.assertTrue(build_review_graph.already_built("queues", "review-abc123"))

    def test_a_different_operation_id_is_not_a_match(self):
        cards = [{"body": "operation_id: review-other\nrole: generalist\n"}]
        with mock.patch.object(build_review_graph, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(returncode=0, stdout=json.dumps(cards))
            self.assertFalse(build_review_graph.already_built("queues", "review-abc123"))

    def test_no_cards_at_all_is_not_built(self):
        with mock.patch.object(build_review_graph, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(returncode=0, stdout="[]")
            self.assertFalse(build_review_graph.already_built("queues", "review-abc123"))

    def test_a_hermes_read_failure_does_not_block_a_real_build(self):
        """Cannot verify -> fall through to create_card(), which carries its
        own idempotency key and is the real guard when this one cannot see."""
        with mock.patch.object(build_review_graph, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(returncode=1, stdout="")
            self.assertFalse(build_review_graph.already_built("queues", "review-abc123"))

    def test_malformed_json_does_not_block_a_real_build(self):
        with mock.patch.object(build_review_graph, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(returncode=0, stdout="not json")
            self.assertFalse(build_review_graph.already_built("queues", "review-abc123"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
