"""The one staged-evidence reader `chat-reply` and `chat-route` both call."""

import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

BOOTSTRAP = pathlib.Path(__file__).resolve().parent.parent / "bootstrap"
sys.path.insert(0, str(BOOTSTRAP))
import chat_evidence


class ChatEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.message = "spaces/example/messages/one"
        self.op_id = "chat-" + hashlib.sha256(self.message.encode()).hexdigest()
        self.evidence = self.root / self.op_id / "evidence"
        self.evidence.mkdir(parents=True)
        self.item = {
            "kind": "inbound", "sender_trusted": True,
            "space": "spaces/example", "thread": "spaces/example/threads/first",
            "messages": [{"name": self.message, "text": ["hello"], "from_ralph": False}],
        }
        self.write_item()

    def write_item(self):
        path = self.evidence / "item.json"
        path.write_text(json.dumps(self.item))
        path.chmod(0o600)

    def read(self):
        return chat_evidence.read_item(self.root, self.op_id)

    def test_well_formed_item_is_accepted(self):
        item = self.read()
        self.assertEqual("spaces/example", item["space"])
        self.assertEqual("spaces/example/threads/first", item["thread"])

    def test_symlinked_workspace_is_refused(self):
        moved = self.root / (self.op_id + "-real")
        (self.root / self.op_id).rename(moved)
        (self.root / self.op_id).symlink_to(moved)
        with self.assertRaises(chat_evidence.Invalid):
            self.read()

    def test_symlinked_evidence_dir_is_refused(self):
        workspace = self.root / self.op_id
        moved = workspace / "evidence-real"
        (workspace / "evidence").rename(moved)
        (workspace / "evidence").symlink_to(moved)
        with self.assertRaises(chat_evidence.Invalid):
            self.read()

    def test_symlinked_item_is_refused(self):
        path = self.evidence / "item.json"
        moved = self.evidence / "item-real.json"
        path.rename(moved)
        path.symlink_to(moved)
        with self.assertRaises(chat_evidence.Invalid):
            self.read()

    def test_hard_linked_item_is_refused(self):
        path = self.evidence / "item.json"
        link = self.evidence / "item2.json"
        import os
        os.link(path, link)
        with self.assertRaises(chat_evidence.Invalid):
            self.read()

    def test_oversized_item_is_refused(self):
        (self.evidence / "item.json").write_text(json.dumps(self.item) + " " * 70_000)
        (self.evidence / "item.json").chmod(0o600)
        with self.assertRaises(chat_evidence.Invalid):
            self.read()

    def test_world_readable_item_is_refused(self):
        (self.evidence / "item.json").chmod(0o644)
        with self.assertRaises(chat_evidence.Invalid):
            self.read()

    def test_bad_json_is_refused(self):
        (self.evidence / "item.json").write_text("not json")
        with self.assertRaises(chat_evidence.Invalid):
            self.read()

    def test_kind_not_inbound_is_refused(self):
        self.item["kind"] = "outbound"
        self.write_item()
        with self.assertRaises(chat_evidence.Invalid):
            self.read()

    def test_untrusted_sender_is_refused(self):
        self.item["sender_trusted"] = False
        self.write_item()
        with self.assertRaises(chat_evidence.Invalid):
            self.read()

    def test_thread_outside_its_space_is_refused(self):
        self.item["thread"] = "spaces/other/threads/elsewhere"
        self.write_item()
        with self.assertRaises(chat_evidence.Invalid):
            self.read()

    def test_more_than_twenty_prior_operations_is_refused(self):
        self.item["prior_operations"] = ["chat-" + format(n, "064x") for n in range(21)]
        self.write_item()
        with self.assertRaises(chat_evidence.Invalid):
            self.read()

    def test_prior_operations_containing_this_operation_is_refused(self):
        self.item["prior_operations"] = [self.op_id]
        self.write_item()
        with self.assertRaises(chat_evidence.Invalid):
            self.read()

    def test_operation_id_not_matching_message_hash_is_refused(self):
        self.item["messages"][-1]["name"] = "spaces/example/messages/different"
        self.write_item()
        with self.assertRaises(chat_evidence.Invalid):
            self.read()

    def test_twenty_prior_operations_is_accepted(self):
        self.item["prior_operations"] = ["chat-" + format(n, "064x") for n in range(20)]
        self.write_item()
        self.read()


if __name__ == "__main__":
    unittest.main(verbosity=2)
