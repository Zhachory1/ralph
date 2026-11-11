"""The host Chat sender posts chat-maintainer's own text to the original thread only."""

import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "bootstrap" / "chat-reply"
THREAD = "spaces/example/threads/first"


def load_script():
    loader = importlib.machinery.SourceFileLoader("chat_reply", str(SCRIPT))
    spec = importlib.util.spec_from_loader("chat_reply", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ChatReplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = pathlib.Path(self.tmp.name)
        self.env = base / "instance.env"
        self.env.write_text("RALPH_INSTANCE=ralph-work\nRALPH_WORKSPACES=./spaces\n"
                            "CHAT_ROUTE_SPACES=spaces/example\n")
        self.root = base / "spaces" / "chat-inbox"
        message = "spaces/example/messages/one"
        self.op = "chat-" + hashlib.sha256(message.encode()).hexdigest()
        self.evidence = self.root / self.op / "evidence"
        self.evidence.mkdir(parents=True)
        self.item = {"kind": "inbound", "sender_trusted": True, "space": "spaces/example",
                     "thread": THREAD, "messages": [{"name": message, "text": "draft an announcement"}]}
        self.write_item()
        self.cards = {
            ("queues", "t_intake"): {
                "task": {"id": "t_intake", "status": "done", "assignee": "ralph-chat-maintainer",
                         "body": f"operation_id: {self.op}\n",
                         "workspace_path": f"/workspaces/chat-inbox/{self.op}"},
                "metadata": {"decision": "route", "body": "Draft the announcement.",
                             "ack": "On it: drafting the announcement now."}},
            ("ralph", "t_final"): {
                "task": {"id": "t_final", "status": "done", "assignee": "ralph-chat-maintainer",
                         "body": f"operation_id: {self.op}\nReport the drafts back.\n",
                         "workspace_path": "/somewhere/else"},
                "metadata": {"decision": "send", "body": "Here are both drafts: ..."}},
        }
        self.calls = []
        self.reply = load_script()
        # Tracker-sync defaults to the inert "no key file" shape: exit 0 for
        # both entry points, nothing recorded and nothing settle-failed.
        self.record_rc = 0
        self.settle_rc = 0
        self.settle_stderr = ""
        # No webhook configured: notify() must print, not reach the network.
        env_patch = patch.dict(os.environ, {"RALPH_NOTIFY_URL": "", "GCHAT_WEBHOOK_URL": "",
                                             "RALPH_TRACKER_CONTAINER": ""})
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def write_item(self):
        path = self.evidence / "item.json"
        path.write_text(json.dumps(self.item))
        path.chmod(0o600)

    def make_operation(self, message, prior=()):
        """A second operation in the same thread, chained to `prior` operations."""
        op = "chat-" + hashlib.sha256(message.encode()).hexdigest()
        evidence = self.root / op / "evidence"
        evidence.mkdir(parents=True)
        item = {"kind": "inbound", "sender_trusted": True, "space": "spaces/example",
                "thread": THREAD, "messages": [{"name": message, "text": "another message"}],
                "prior_operations": list(prior)}
        path = evidence / "item.json"
        path.write_text(json.dumps(item))
        path.chmod(0o600)
        return op

    def command(self, argv):
        self.calls.append(argv)
        if "list" in argv:
            board = argv[argv.index("--board") + 1]
            tasks = [card["task"] for (b, _), card in self.cards.items() if b == board]
            return subprocess.CompletedProcess(argv, 0, json.dumps({"tasks": tasks}), "")
        if "show" in argv:
            card = self.cards[(argv[argv.index("--board") + 1], argv[argv.index("show") + 1])]
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "task": card["task"], "runs": [{"outcome": "completed", "metadata": card["metadata"]}],
            }), "")
        if "/ralph/bootstrap/chat-route" in argv:
            return subprocess.CompletedProcess(argv, 0, "routed: t_orchestrator\n", "")
        if "comment" in argv or "archive" in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if any(isinstance(a, str) and a.endswith("tracker-sync") for a in argv):
            return self.tracker_sync(argv)
        if "send" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"sent": True}), "")
        raise AssertionError(argv)

    def tracker_sync(self, argv):
        if "--settle" in argv:
            return subprocess.CompletedProcess(argv, self.settle_rc, "", self.settle_stderr)
        return subprocess.CompletedProcess(argv, self.record_rc, "", "")

    def sends(self):
        return [a for a in self.calls if "send" in a and "/ralph/bootstrap/chat-route" not in a]

    def blocks(self):
        return [a for a in self.calls if "archive" in a]

    def tracker_calls(self, flag=None):
        calls = [a for a in self.calls
                 if any(isinstance(x, str) and x.endswith("tracker-sync") for x in a)]
        return [a for a in calls if flag in a] if flag else calls

    def handle(self, board, task_id, tracker_host=None):
        with patch.object(self.reply, "command", side_effect=self.command):
            return self.reply.handle("ralph-work-hermes-1", self.root, {"spaces/example"},
                                     board, task_id, tracker_host)

    def handle_with_send(self, board, task_id, send_enabled, tracker_host=None):
        with patch.object(self.reply, "command", side_effect=self.command):
            return self.reply.handle("ralph-work-hermes-1", self.root, {"spaces/example"},
                                     board, task_id, tracker_host, send_enabled)

    def set_send(self, value):
        self.env.write_text("RALPH_INSTANCE=ralph-work\nRALPH_WORKSPACES=./spaces\n"
                            "CHAT_ROUTE_SPACES=spaces/example\n"
                            f"CHAT_SEND={value}\n")

    def scan(self):
        with patch.object(self.reply, "command", side_effect=self.command):
            return self.reply.scan(self.env)

    def test_accepted_request_gets_maintainers_ack_then_final_response(self):
        self.assertEqual(0, self.scan())
        texts = [a[a.index("send") + 2] for a in self.sends()]
        self.assertEqual(["On it: drafting the announcement now.", "Here are both drafts: ..."], texts)
        for argv in self.sends():
            self.assertEqual(THREAD, argv[argv.index("--thread") + 1])
            self.assertIn("--strict-thread", argv)
            self.assertTrue(argv[argv.index("--message-id") + 1].startswith("client-ralph-"))

    def test_ack_waits_for_orchestrator_card(self):
        def route_fails(argv):
            if "/ralph/bootstrap/chat-route" in argv:
                return subprocess.CompletedProcess(argv, 1, "", "failed")
            return self.command(argv)
        with patch.object(self.reply, "command", side_effect=route_fails):
            self.assertEqual(1, self.reply.scan(self.env))
        self.assertEqual([], self.sends())

    def test_cannot_fulfill_gets_maintainers_reply_and_no_work(self):
        del self.cards[("ralph", "t_final")]
        self.cards[("queues", "t_intake")]["metadata"] = {
            "decision": "reply", "body": "I can't help with payroll questions; try the People team."}
        self.assertEqual(0, self.scan())
        self.assertEqual(["I can't help with payroll questions; try the People team."],
                         [a[a.index("send") + 2] for a in self.sends()])
        self.assertFalse(any("/ralph/bootstrap/chat-route" in a for a in self.calls))

    def test_ignore_sends_nothing(self):
        del self.cards[("ralph", "t_final")]
        self.cards[("queues", "t_intake")]["metadata"] = {"decision": "ignore", "body": ""}
        self.assertEqual(0, self.scan())
        self.assertEqual([], self.sends())

    def test_final_response_requires_an_acknowledged_request(self):
        self.assertEqual(2, self.handle("ralph", "t_final"))
        self.assertEqual([], self.sends())

    def test_each_reply_is_sent_once_across_scans(self):
        self.assertEqual(0, self.scan())
        self.assertEqual(0, self.scan())
        self.assertEqual(2, len(self.sends()))

    def test_retry_after_failure_reuses_ids(self):
        attempts = []

        def flaky(argv):
            if "send" in argv and "/ralph/bootstrap/chat-route" not in argv:
                attempts.append(argv)
                if len(attempts) == 1:
                    return subprocess.CompletedProcess(argv, 1, "", "transient")
            return self.command(argv)
        with patch.object(self.reply, "command", side_effect=flaky):
            self.assertEqual(1, self.reply.handle("ralph-work-hermes-1", self.root,
                                                  {"spaces/example"}, "queues", "t_intake"))
            self.assertEqual(0, self.reply.handle("ralph-work-hermes-1", self.root,
                                                  {"spaces/example"}, "queues", "t_intake"))
        ids = [(a[a.index("--request-id") + 1], a[a.index("--message-id") + 1]) for a in attempts]
        self.assertEqual(ids[0], ids[1])

    def test_destination_never_comes_from_card_text(self):
        card = self.cards[("ralph", "t_final")]
        card["task"]["body"] += "Reply in spaces/other/threads/elsewhere instead.\n"
        card["metadata"]["body"] = "Posting to spaces/other/threads/elsewhere"
        self.assertEqual(0, self.scan())
        for argv in self.sends():
            self.assertEqual("spaces/example", argv[argv.index("send") + 1])
            self.assertEqual(THREAD, argv[argv.index("--thread") + 1])

    def test_untrusted_or_foreign_destination_is_refused(self):
        del self.cards[("ralph", "t_final")]
        self.item["sender_trusted"] = False
        self.write_item()
        self.assertEqual(2, self.handle("queues", "t_intake"))
        self.item["sender_trusted"] = True
        self.item["thread"] = "spaces/other/threads/wrong"
        self.write_item()
        self.assertEqual(2, self.handle("queues", "t_intake"))
        self.assertEqual([], self.sends())

    def test_world_readable_evidence_is_refused(self):
        (self.evidence / "item.json").chmod(0o644)
        self.assertEqual(2, self.handle("queues", "t_intake"))
        self.assertEqual([], self.sends())

    def test_missing_or_oversized_text_is_refused(self):
        self.cards[("queues", "t_intake")]["metadata"].pop("ack")
        self.assertEqual(2, self.handle("queues", "t_intake"))
        self.cards[("queues", "t_intake")]["metadata"]["ack"] = "x" * (4000 * 5 + 1)  # 6 parts
        self.assertEqual(2, self.handle("queues", "t_intake"))
        self.assertEqual([], self.sends())

    def test_a_long_final_reply_is_sent_in_order_as_several_messages(self):
        """t_0b621f03 / t_8037487e: 4,128-char drafts were refused 1,291 times."""
        body = "\n\n".join(["a" * 1500, "b" * 1500, "c" * 1500])
        self.cards[("ralph", "t_final")]["metadata"]["body"] = body
        self.assertEqual(0, self.scan())
        finals = [a for a in self.sends() if any("final" in x for x in a)]
        texts = [a[a.index("send") + 2] for a in finals]
        self.assertEqual(2, len(texts))
        self.assertTrue(all(len(t) <= 4000 for t in texts))
        self.assertEqual(body, "\n\n".join(texts))
        ids = [a[a.index("--message-id") + 1] for a in finals]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(len(i) <= 63 for i in ids))

    def test_split_keeps_order_around_a_long_line(self):
        """#92 review: a long line was emitted before earlier buffered text."""
        body = "first\n" + "x" * 4500 + "\nlast"
        chunks = self.reply.parts(body)
        self.assertTrue(all(len(c) <= 4000 for c in chunks))
        joined = "".join(chunks)
        self.assertEqual(body.replace("\n", ""), joined.replace("\n", ""))
        self.assertTrue(chunks[0].startswith("first"))
        self.assertTrue(chunks[-1].endswith("last"))

    def test_more_than_five_messages_is_refused(self):
        """#92 review: the five-message limit must hold for any input."""
        self.cards[("ralph", "t_final")]["metadata"]["body"] = "\n\n".join(["y" * 3999] * 6)
        self.scan()
        self.assertFalse(any(any("final" in x for x in a) for a in self.sends()))

    def test_extra_metadata_keys_are_ignored(self):
        """t_8037487e added a `diagnosis` note and was refused on every scan."""
        self.cards[("queues", "t_intake")]["metadata"]["worker_session_id"] = "session-1"
        self.cards[("queues", "t_intake")]["metadata"]["diagnosis"] = "why"
        self.assertEqual(0, self.handle("queues", "t_intake"))

    def test_clarify_reply_writes_its_own_marker(self):
        del self.cards[("ralph", "t_final")]
        self.cards[("queues", "t_intake")]["metadata"] = {
            "decision": "reply", "reply_kind": "clarify", "body": "Which announcement do you mean?"}
        self.assertEqual(0, self.scan())
        self.assertTrue((self.root / self.op / ".clarify-sent").is_file())
        self.assertFalse((self.root / self.op / ".reply-sent").is_file())

    def test_decline_reply_writes_its_own_marker(self):
        del self.cards[("ralph", "t_final")]
        self.cards[("queues", "t_intake")]["metadata"] = {
            "decision": "reply", "reply_kind": "decline", "body": "I can't help with payroll questions."}
        self.assertEqual(0, self.scan())
        self.assertTrue((self.root / self.op / ".decline-sent").is_file())
        self.assertFalse((self.root / self.op / ".reply-sent").is_file())

    def test_reply_with_no_reply_kind_still_sends_and_writes_legacy_marker(self):
        del self.cards[("ralph", "t_final")]
        self.cards[("queues", "t_intake")]["metadata"] = {
            "decision": "reply", "body": "I can't help with payroll questions."}
        self.assertEqual(0, self.scan())
        self.assertTrue((self.root / self.op / ".reply-sent").is_file())

    def test_legacy_reply_sent_marker_satisfies_every_reply_kind(self):
        """A thread already answered before markers became per kind must not resend (PR 2 hazard)."""
        del self.cards[("ralph", "t_final")]
        (self.root / self.op / ".reply-sent").touch()
        for reply_kind in ("clarify", "decline", "answer"):
            with self.subTest(reply_kind=reply_kind):
                self.cards[("queues", "t_intake")]["metadata"] = {
                    "decision": "reply", "reply_kind": reply_kind, "body": "some reply text"}
                self.assertEqual(0, self.scan())
        self.assertEqual([], self.sends())

    def test_third_clarify_in_one_thread_refuses_and_blocks_card(self):
        """A17: at most 2 clarify per thread; counts span prior_operations directories."""
        del self.cards[("ralph", "t_final")]
        first = self.make_operation("spaces/example/messages/two")
        (self.root / first / ".clarify-sent").touch()
        second = self.make_operation("spaces/example/messages/three", prior=[first])
        (self.root / second / ".clarify-sent").touch()
        third = self.make_operation("spaces/example/messages/four", prior=[first, second])
        self.cards[("queues", "t_third")] = {
            "task": {"id": "t_third", "status": "done", "assignee": "ralph-chat-maintainer",
                     "body": f"operation_id: {third}\n",
                     "workspace_path": f"/workspaces/chat-inbox/{third}"},
            "metadata": {"decision": "reply", "reply_kind": "clarify", "body": "which one do you mean?"}}
        self.assertEqual(2, self.handle("queues", "t_third"))
        self.assertEqual([], self.sends())
        self.assertFalse((self.root / third / ".clarify-sent").is_file())
        self.assertEqual(1, len(self.blocks()))
        self.assertEqual("t_third", self.blocks()[0][self.blocks()[0].index("archive") + 1])

    def test_second_decline_in_one_thread_refuses_and_blocks_card(self):
        """A15/R7: at most 1 decline per thread; counts span prior_operations directories."""
        del self.cards[("ralph", "t_final")]
        first = self.make_operation("spaces/example/messages/two")
        (self.root / first / ".decline-sent").touch()
        second = self.make_operation("spaces/example/messages/three", prior=[first])
        self.cards[("queues", "t_second")] = {
            "task": {"id": "t_second", "status": "done", "assignee": "ralph-chat-maintainer",
                     "body": f"operation_id: {second}\n",
                     "workspace_path": f"/workspaces/chat-inbox/{second}"},
            "metadata": {"decision": "reply", "reply_kind": "decline", "body": "I can't help with that."}}
        self.assertEqual(2, self.handle("queues", "t_second"))
        self.assertEqual([], self.sends())
        self.assertEqual(1, len(self.blocks()))

    def test_held_or_absent_marker_does_not_count_toward_a_ceiling(self):
        other = self.make_operation("spaces/example/messages/two")
        item = {"prior_operations": []}
        self.assertEqual(0, self.reply.thread_count(self.root, other, item, "clarify"))
        (self.root / other / ".held-clarify").touch()
        self.assertEqual(0, self.reply.thread_count(self.root, other, item, "clarify"))
        (self.root / other / ".clarify-sent").touch()
        self.assertEqual(1, self.reply.thread_count(self.root, other, item, "clarify"))

    def test_two_operations_in_one_thread_serialize_on_the_thread_lock(self):
        order = []
        first_holds = threading.Event()
        let_first_release = threading.Event()

        def first():
            with self.reply.thread_lock(self.root, THREAD):
                order.append("first-acquired")
                first_holds.set()
                let_first_release.wait(timeout=5)
                order.append("first-released")

        worker = threading.Thread(target=first)
        worker.start()
        self.assertTrue(first_holds.wait(timeout=5))
        let_first_release.set()
        with self.reply.thread_lock(self.root, THREAD):
            order.append("second-acquired")
        worker.join(timeout=5)
        self.assertEqual(["first-acquired", "first-released", "second-acquired"], order)

    def test_refusal_blocks_once_and_alerts_once_across_scans(self):
        """A second scan over the same operation and kind must not alert again."""
        del self.cards[("ralph", "t_final")]
        other = self.make_operation("spaces/example/messages/two")
        (self.root / other / ".decline-sent").touch()
        third = self.make_operation("spaces/example/messages/three", prior=[other])
        self.cards[("queues", "t_third")] = {
            "task": {"id": "t_third", "status": "done", "assignee": "ralph-chat-maintainer",
                     "body": f"operation_id: {third}\n",
                     "workspace_path": f"/workspaces/chat-inbox/{third}"},
            "metadata": {"decision": "reply", "reply_kind": "decline", "body": "no."}}
        with patch.object(self.reply, "notify") as notify:
            self.assertEqual(2, self.handle("queues", "t_third"))
            self.assertEqual(2, self.handle("queues", "t_third"))
        self.assertEqual(1, notify.call_count)
        self.assertEqual(1, len(self.blocks()))

    def test_retryable_failure_alerts_once_and_does_not_block_so_it_retries(self):
        """A retryable failure must leave the card `done`: that listing is what
        the next scan retries from. A second scan alerts no second time, and
        once the send works the reply goes out."""
        (self.root / self.op / ".receipt-sent").touch()

        def failing_send(argv):
            if "send" in argv and "/ralph/bootstrap/chat-route" not in argv:
                return subprocess.CompletedProcess(argv, 1, "", "boom")
            return self.command(argv)

        with patch.object(self.reply, "command", side_effect=failing_send), \
                patch.object(self.reply, "notify") as notify:
            self.assertEqual(1, self.reply.handle("ralph-work-hermes-1", self.root,
                                                  {"spaces/example"}, "ralph", "t_final"))
            self.assertEqual(1, self.reply.handle("ralph-work-hermes-1", self.root,
                                                  {"spaces/example"}, "ralph", "t_final"))
        self.assertEqual(1, notify.call_count)
        self.assertEqual([], self.blocks())
        # The same card, still done, sends on the next scan once Chat works.
        self.assertEqual(0, self.handle("ralph", "t_final"))
        self.assertEqual(1, len(self.sends()))
        self.assertTrue((self.root / self.op / ".final-sent").is_file())

    def test_ack_ceiling_breach_is_held_not_blocked(self):
        """R6: an `ack` breach is held for the next scan. Blocking the card
        would take it out of the done listing and the hold could never lift."""
        other = self.make_operation("spaces/example/messages/two")
        (self.root / other / ".receipt-sent").touch()
        item = dict(self.item, prior_operations=[other])
        self.item = item
        self.write_item()
        with patch.object(self.reply, "notify") as notify:
            self.assertEqual(1, self.handle("queues", "t_intake"))
        self.assertEqual([], self.sends())
        self.assertEqual([], self.blocks())
        self.assertEqual(1, notify.call_count)

    def test_a_failed_archive_leaves_no_sentinel_so_the_next_scan_retries_it(self):
        del self.cards[("ralph", "t_final")]
        other = self.make_operation("spaces/example/messages/two")
        (self.root / other / ".decline-sent").touch()
        third = self.make_operation("spaces/example/messages/three", prior=[other])
        self.cards[("queues", "t_third")] = {
            "task": {"id": "t_third", "status": "done", "assignee": "ralph-chat-maintainer",
                     "body": f"operation_id: {third}\n",
                     "workspace_path": f"/workspaces/chat-inbox/{third}"},
            "metadata": {"decision": "reply", "reply_kind": "decline", "body": "no."}}
        state = {"fail": True}

        def command(argv):
            if "archive" in argv and state["fail"]:
                return subprocess.CompletedProcess(argv, 1, "", "docker down")
            return self.command(argv)

        with patch.object(self.reply, "command", side_effect=command), \
                patch.object(self.reply, "notify") as notify:
            self.assertEqual(2, self.reply.handle("ralph-work-hermes-1", self.root,
                                                  {"spaces/example"}, "queues", "t_third"))
            self.assertFalse((self.root / third / ".alerted-decline").exists())
            self.assertEqual(0, notify.call_count)
            state["fail"] = False
            self.assertEqual(2, self.reply.handle("ralph-work-hermes-1", self.root,
                                                  {"spaces/example"}, "queues", "t_third"))
        self.assertTrue((self.root / third / ".alerted-decline").exists())
        self.assertEqual(1, notify.call_count)
        self.assertEqual(1, len(self.blocks()))

    def test_refusal_on_a_done_card_archives_it_instead_of_blocking(self):
        """The kanban kernel only transitions running/ready -> blocked
        (kanban_db.py block_task); every card chat-reply refuses is already
        done, so `block` always fails there and the card would be
        rediscovered and re-refused forever. The real tool for taking a done
        card out of the retry listing is `archive`."""
        del self.cards[("ralph", "t_final")]
        other = self.make_operation("spaces/example/messages/two")
        (self.root / other / ".decline-sent").touch()
        third = self.make_operation("spaces/example/messages/three", prior=[other])
        self.cards[("queues", "t_third")] = {
            "task": {"id": "t_third", "status": "done", "assignee": "ralph-chat-maintainer",
                     "body": f"operation_id: {third}\n",
                     "workspace_path": f"/workspaces/chat-inbox/{third}"},
            "metadata": {"decision": "reply", "reply_kind": "decline", "body": "no."}}
        with patch.object(self.reply, "notify") as notify:
            self.assertEqual(2, self.handle("queues", "t_third"))
        self.assertTrue((self.root / third / ".alerted-decline").exists())
        self.assertEqual(1, notify.call_count)
        self.assertFalse(any("block" in a for a in self.calls))
        self.assertEqual(1, len(self.blocks()))
        self.assertEqual("t_third", self.blocks()[0][self.blocks()[0].index("archive") + 1])
        comments = [a for a in self.calls if "comment" in a]
        self.assertEqual(1, len(comments))
        self.assertIn("BLOCKED: decline ceiling reached for this thread", comments[0])

    def test_a_send_timeout_fails_that_operation_and_does_not_abort_the_scan(self):
        """`command()` turns a timeout into a failed result, so one hung send
        cannot stop the scan from reaching the next card."""
        timed_out = subprocess.TimeoutExpired(["x"], 60)
        with patch.object(self.reply.subprocess, "run", side_effect=timed_out):
            result = self.reply.command(["docker", "exec", "x"])
        self.assertEqual(124, result.returncode)

        def hanging_send(argv):
            if "send" in argv and "/ralph/bootstrap/chat-route" not in argv:
                return self.reply.subprocess.CompletedProcess(argv, 124, "", "timed out")
            return self.command(argv)

        with patch.object(self.reply, "command", side_effect=hanging_send), \
                patch.object(self.reply, "notify"):
            self.assertEqual(1, self.reply.scan(self.env))
        self.assertFalse((self.root / self.op / ".receipt-sent").is_file())

    def test_a_public_link_at_the_end_of_a_sentence_is_not_refused_for_its_punctuation(self):
        self.assertTrue(hasattr(self.reply, "refuse_unsendable"))
        for tail in (".", ",", ";", ":", "!", "?", ").", ".)"):
            with self.subTest(tail=tail), \
                    patch.object(self.reply, "github_repo_is_public", return_value=True) as read:
                body = f"The change is at https://github.com/example/project/pull/12{tail} Thanks"
                self.assertEqual(body, self.reply.refuse_unsendable(body, None))
                read.assert_called_once_with("example", "project")

    def test_a_private_link_is_still_refused_with_trailing_punctuation(self):
        with patch.object(self.reply, "github_repo_is_public", return_value=False):
            with self.assertRaises(self.reply.Refused):
                self.reply.refuse_unsendable(
                    "See https://github.com/example/private/pull/3.", None)

    def test_a_non_github_link_is_still_refused_with_trailing_punctuation(self):
        with self.assertRaises(self.reply.Refused):
            self.reply.refuse_unsendable("See https://example.com/doc.", None)

    def test_notify_with_no_webhook_prints_and_does_not_raise(self):
        self.reply.notify("a test alert")

    def test_tracker_host_reference_is_refused(self):
        """R1: the instance's tracker host, read from the instance env file."""
        with self.assertRaises(self.reply.Refused):
            self.reply.refuse_unsendable("see tracker.example/browse/x for the item",
                                         "tracker.example")

    def test_issues_path_is_refused(self):
        with self.assertRaises(self.reply.Refused):
            self.reply.refuse_unsendable("filed as /issues/12", None)

    def test_bare_issue_reference_is_refused(self):
        with self.assertRaises(self.reply.Refused):
            self.reply.refuse_unsendable("tracked under #12", None)

    def test_tracker_host_from_instance_env_reaches_the_refusal(self):
        """instance_config() reads RALPH_TRACKER_HOST from the instance env
        file (plan-tracker.md:703-708); scan() threads it through to the
        refusal, end to end."""
        self.env.write_text("RALPH_INSTANCE=ralph-work\nRALPH_WORKSPACES=./spaces\n"
                            "CHAT_ROUTE_SPACES=spaces/example\n"
                            "RALPH_TRACKER_HOST=tracker.example\n")
        del self.cards[("ralph", "t_final")]
        self.cards[("queues", "t_intake")]["metadata"]["ack"] = \
            "filed at tracker.example/browse/12"
        self.assertEqual(0, self.scan())
        self.assertEqual([], self.sends())
        self.assertEqual(1, len(self.blocks()))

    def test_url_outside_reachable_set_is_refused(self):
        with self.assertRaises(self.reply.Refused):
            self.reply.refuse_unsendable("see https://example.com/doc for the write-up", None)

    def test_public_github_pull_request_url_passes_with_one_stubbed_read(self):
        with patch.object(self.reply, "github_repo_is_public", return_value=True) as stub:
            body = self.reply.refuse_unsendable(
                "the change is at https://github.com/example/repo/pull/5", None)
        self.assertEqual(1, stub.call_count)
        self.assertEqual(("example", "repo"), stub.call_args.args)
        self.assertIn("pull/5", body)

    def test_public_github_repository_url_passes_with_one_stubbed_read(self):
        with patch.object(self.reply, "github_repo_is_public", return_value=True) as stub:
            self.reply.refuse_unsendable("see https://github.com/example/repo", None)
        self.assertEqual(1, stub.call_count)

    def test_private_github_pull_request_url_is_refused_and_blocks_card(self):
        del self.cards[("ralph", "t_final")]
        self.cards[("queues", "t_intake")]["metadata"] = {
            "decision": "reply",
            "body": "the change is at https://github.com/example/private-repo/pull/5"}
        with patch.object(self.reply, "github_repo_is_public", return_value=False):
            self.assertEqual(2, self.handle("queues", "t_intake"))
        self.assertEqual([], self.sends())
        self.assertEqual(1, len(self.blocks()))

    def test_non_200_reachability_response_refuses(self):
        """The reachability read is stubbed here too -- at urlopen, not at
        github_repo_is_public -- so this is the one test that exercises its
        non-200 handling without reaching the network."""
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.HTTPError("url", 404, "Not Found", {}, None)):
            self.assertFalse(self.reply.github_repo_is_public("example", "repo"))

    def test_credential_shaped_string_is_refused_on_every_send_path(self):
        credential = "ghp_" + "a" * 36
        self.cards[("queues", "t_intake")]["metadata"]["ack"] = f"use token {credential}"
        self.assertEqual(2, self.handle("queues", "t_intake"))
        self.assertEqual([], self.sends())

        for reply_kind in ("answer", "clarify", "decline"):
            with self.subTest(reply_kind=reply_kind):
                self.cards[("queues", "t_intake")]["metadata"] = {
                    "decision": "reply", "reply_kind": reply_kind,
                    "body": f"use token {credential}"}
                self.assertEqual(2, self.handle("queues", "t_intake"))
                self.assertEqual([], self.sends())

        (self.root / self.op / ".receipt-sent").touch()
        self.cards[("ralph", "t_final")]["metadata"]["body"] = f"use token {credential}"
        self.assertEqual(2, self.handle("ralph", "t_final"))
        self.assertEqual([], self.sends())
        self.assertGreaterEqual(len(self.blocks()), 1)

    # -- PR 5: the send switch, and the held marker that makes flipping it safe --

    def test_switch_off_holds_ack_runs_chat_route_but_does_not_post(self):
        self.set_send("0")
        self.assertEqual(0, self.scan())
        self.assertEqual([], self.sends())
        self.assertTrue(any("/ralph/bootstrap/chat-route" in a for a in self.calls))
        self.assertTrue((self.root / self.op / ".held-ack").is_file())
        self.assertFalse((self.root / self.op / ".receipt-sent").is_file())

    def test_switch_off_second_scan_skips_already_held_operation(self):
        self.set_send("0")
        self.assertEqual(0, self.scan())
        calls_after_first = len(self.calls)
        self.assertEqual(0, self.scan())
        # The held operation is skipped by scan()'s own pre-filter: handle()
        # is never called again for it, so no second `show` for its cards.
        self.assertFalse(any("show" in a for a in self.calls[calls_after_first:]))

    def test_switch_off_still_refuses_and_blocks_a_credential_body(self):
        """A reply that fails a PR 4 refusal check is still refused and still
        blocks, held or not."""
        credential = "ghp_" + "a" * 36
        self.cards[("queues", "t_intake")]["metadata"]["ack"] = f"use token {credential}"
        self.assertEqual(2, self.handle_with_send("queues", "t_intake", send_enabled=False))
        self.assertEqual([], self.sends())
        self.assertEqual(1, len(self.blocks()))
        self.assertFalse((self.root / self.op / ".held-ack").is_file())

    def test_switch_back_on_does_not_refuse_or_block_an_already_held_final(self):
        """Queue finding: `--task` on a held final, with the switch on and no
        acknowledgement marker, must be 'nothing to do', not a refusal."""
        self.assertEqual(0, self.handle_with_send("ralph", "t_final", send_enabled=False))
        self.assertTrue((self.root / self.op / ".held-final").is_file())
        self.assertEqual(0, self.handle_with_send("ralph", "t_final", send_enabled=True))
        self.assertEqual([], self.sends())
        self.assertEqual([], self.blocks())

    def test_switch_back_on_does_not_resend_an_already_sent_final_without_ack(self):
        (self.root / self.op / ".final-sent").touch()
        self.assertEqual(0, self.handle_with_send("ralph", "t_final", send_enabled=True))
        self.assertEqual([], self.sends())
        self.assertEqual([], self.blocks())

    def test_switch_off_holds_final_without_ack_marker_and_does_not_block(self):
        """The switch is read before the final-needs-acknowledgement check: a
        held final is held, not refused, with no acknowledgement marker at all."""
        self.assertFalse((self.root / self.op / ".receipt-sent").is_file())
        self.assertEqual(0, self.handle_with_send("ralph", "t_final", send_enabled=False))
        self.assertEqual([], self.sends())
        self.assertEqual([], self.blocks())
        self.assertTrue((self.root / self.op / ".held-final").is_file())

    def test_turning_switch_on_sends_nothing_for_an_already_held_operation(self):
        (self.root / self.op / ".held-ack").touch()
        self.assertEqual(0, self.handle_with_send("queues", "t_intake", send_enabled=True))
        self.assertEqual([], self.sends())
        self.assertFalse((self.root / self.op / ".receipt-sent").is_file())
        self.assertFalse(any("/ralph/bootstrap/chat-route" in a for a in self.calls))

    def test_held_reply_does_not_consume_a_ceiling(self):
        """A17: the clarify ceiling is 2 per thread. Three held clarifies in
        the same thread must all hold, never refuse -- held replies spend no
        ceiling because `mark()` never runs for them."""
        del self.cards[("ralph", "t_final")]
        ops = [self.op]
        for i in range(2):
            ops.append(self.make_operation(f"spaces/example/messages/extra{i}", prior=ops))
        for i, op in enumerate(ops):
            self.cards[("queues", f"t_clarify{i}")] = {
                "task": {"id": f"t_clarify{i}", "status": "done", "assignee": "ralph-chat-maintainer",
                         "body": f"operation_id: {op}\n",
                         "workspace_path": f"/workspaces/chat-inbox/{op}"},
                "metadata": {"decision": "reply", "reply_kind": "clarify", "body": "Which one?"}}
            self.assertEqual(
                0, self.handle_with_send("queues", f"t_clarify{i}", send_enabled=False))
            self.assertTrue((self.root / op / ".held-clarify").is_file())
        self.assertEqual([], self.sends())
        self.assertEqual([], self.blocks())

    def test_chat_send_value_parsing(self):
        cases = {
            None: True, "0": False, "false": False, "FALSE": False, "off": False, "OFF": False,
            "no": False, "NO": False, "1": True, "true": True, "TRUE": True, "on": True,
            "ON": True, "yes": True, "YES": True,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, self.reply.parse_send_switch(raw))

    def test_unrecognized_chat_send_value_is_treated_as_on_and_printed_once(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self.assertTrue(self.reply.parse_send_switch("maybe"))
        self.assertIn("CHAT_SEND", buf.getvalue())
        self.assertEqual(1, buf.getvalue().count("\n"))

    def test_instance_config_defaults_chat_send_on_when_key_absent(self):
        _, _, _, _, send_enabled = self.reply.instance_config(self.env)
        self.assertTrue(send_enabled)

    def test_instance_config_reads_chat_send_off(self):
        self.set_send("0")
        _, _, _, _, send_enabled = self.reply.instance_config(self.env)
        self.assertFalse(send_enabled)

    def test_switch_on_with_key_absent_shipped_tests_pass_unchanged(self):
        """The switch's default must not change today's behaviour."""
        self.assertEqual(0, self.scan())
        texts = [a[a.index("send") + 2] for a in self.sends()]
        self.assertEqual(["On it: drafting the announcement now.", "Here are both drafts: ..."],
                         texts)
        self.assertTrue((self.root / self.op / ".receipt-sent").is_file())
        self.assertTrue((self.root / self.op / ".final-sent").is_file())


    # -- PR 3: tracker-sync wiring, and a tracker failure you can see --

    def test_nonzero_record_sends_nothing_and_is_retryable(self):
        """E1, dd-tracker.md:428: the single most important test in this
        pull request. `post()` must never run after a failed `--record`."""
        self.record_rc = 1
        with patch.object(self.reply, "notify") as notify:
            self.assertEqual(1, self.handle("queues", "t_intake"))
        self.assertEqual([], self.sends())
        self.assertEqual([], self.blocks())
        self.assertEqual(0, notify.call_count)
        self.assertTrue((self.root / self.op / self.reply.TRACKER_RECORD_FAILURES).is_file())

    def test_record_runs_after_chat_route_confirms_and_before_post(self):
        self.assertEqual(0, self.handle("queues", "t_intake"))
        route_idx = next(i for i, a in enumerate(self.calls)
                         if "/ralph/bootstrap/chat-route" in a)
        record_idx = next(i for i, a in enumerate(self.calls)
                          if any(isinstance(x, str) and x.endswith("tracker-sync") for x in a))
        send_idx = next(i for i, a in enumerate(self.calls) if "send" in a)
        self.assertLess(route_idx, record_idx)
        self.assertLess(record_idx, send_idx)

    def test_decline_ignore_and_reply_never_call_tracker_sync(self):
        """E3, dd-tracker.md:430: only an `ack` records."""
        del self.cards[("ralph", "t_final")]
        scenarios = [
            ("ignore", {"decision": "ignore", "body": ""}),
            ("plain-reply", {"decision": "reply", "body": "no."}),
            ("clarify", {"decision": "reply", "reply_kind": "clarify", "body": "which one?"}),
            ("decline", {"decision": "reply", "reply_kind": "decline", "body": "no."}),
        ]
        for name, metadata in scenarios:
            with self.subTest(name=name):
                op = self.make_operation(f"spaces/example/messages/{name}")
                task_id = f"t_{name}"
                self.cards[("queues", task_id)] = {
                    "task": {"id": task_id, "status": "done", "assignee": "ralph-chat-maintainer",
                             "body": f"operation_id: {op}\n",
                             "workspace_path": f"/workspaces/chat-inbox/{op}"},
                    "metadata": metadata}
                self.assertEqual(0, self.handle("queues", task_id))
                self.assertEqual([], self.tracker_calls("--record"))

    def test_record_exit_zero_tracker_not_configured_is_inert(self):
        """The inertness claim, as a test: `--record` succeeding because no
        key file exists must leave the acknowledgement unchanged."""
        self.record_rc = 0
        self.assertEqual(0, self.scan())
        texts = [a[a.index("send") + 2] for a in self.sends()]
        self.assertEqual(["On it: drafting the announcement now.", "Here are both drafts: ..."],
                         texts)
        self.assertEqual([], self.blocks())
        self.assertFalse((self.root / self.op / self.reply.TRACKER_RECORD_FAILURES).exists())

    def test_settle_runs_once_per_scan_before_the_board_loop(self):
        self.assertEqual(0, self.scan())
        self.assertTrue(self.calls)
        first = self.calls[0]
        self.assertTrue(any(isinstance(a, str) and a.endswith("tracker-sync") for a in first))
        self.assertIn("--settle", first)
        self.assertEqual(1, len(self.tracker_calls("--settle")))

    def test_settle_failure_alerts_once_blocks_nothing_and_sends_still_go_out(self):
        self.settle_rc = 1
        self.settle_stderr = f"settle-failed: {self.op}\n"
        with patch.object(self.reply, "notify") as notify:
            self.assertEqual(1, self.scan())
            texts = [a[a.index("send") + 2] for a in self.sends()]
            self.assertEqual(["On it: drafting the announcement now.", "Here are both drafts: ..."],
                             texts)
            self.assertEqual([], self.blocks())
            self.assertEqual(1, notify.call_count)
            self.assertTrue((self.root / self.op / ".alerted-tracker-settle").is_file())
            # A second scan over the same still-failing operation alerts no second time.
            self.assertEqual(1, self.scan())
        self.assertEqual(1, notify.call_count)
        self.assertEqual([], self.blocks())

    def test_record_retry_blocks_nothing_alerts_nothing_and_retries_next_scan(self):
        """The test this pull request exists for: asserted on the card
        listing the second scan makes, not on the counter file."""
        # The final card is irrelevant to --record and would itself be
        # refused (no ack yet) and block the `ralph` board card otherwise.
        del self.cards[("ralph", "t_final")]
        self.record_rc = 1
        with patch.object(self.reply, "notify") as notify:
            self.assertEqual(1, self.scan())
        self.assertEqual([], self.sends())
        self.assertEqual([], self.blocks())
        self.assertEqual(0, notify.call_count)
        self.assertEqual(1, len(self.tracker_calls("--record")))
        self.calls.clear()
        with patch.object(self.reply, "notify") as notify:
            self.assertEqual(1, self.scan())
        self.assertTrue(any("list" in a for a in self.calls))
        self.assertEqual(1, len(self.tracker_calls("--record")))
        self.assertEqual(0, notify.call_count)

    def test_five_consecutive_record_failures_block_once_alert_once_sixth_no_second_alert(self):
        self.record_rc = 1
        with patch.object(self.reply, "notify") as notify:
            results = [self.handle("queues", "t_intake") for _ in range(5)]
            self.assertEqual([1, 1, 1, 1, 2], results)
            self.assertEqual(1, len(self.blocks()))
            self.assertEqual(1, notify.call_count)
            self.assertEqual(2, self.handle("queues", "t_intake"))
        self.assertEqual(1, len(self.blocks()))
        self.assertEqual(1, notify.call_count)

    def test_four_failures_then_success_clears_counter_and_a_later_failure_starts_at_one(self):
        failures_path = self.root / self.op / self.reply.TRACKER_RECORD_FAILURES
        with patch.object(self.reply, "command", side_effect=self.command):
            self.record_rc = 1
            for expected in (1, 2, 3, 4):
                self.assertEqual(1, self.reply.record_item(
                    "ralph-work-hermes-1", "queues", "t_intake", self.root, self.op))
                self.assertEqual(expected, int(failures_path.read_text()))
            self.record_rc = 0
            self.assertEqual(0, self.reply.record_item(
                "ralph-work-hermes-1", "queues", "t_intake", self.root, self.op))
            self.assertFalse(failures_path.exists())
            self.record_rc = 1
            self.assertEqual(1, self.reply.record_item(
                "ralph-work-hermes-1", "queues", "t_intake", self.root, self.op))
            self.assertEqual(1, int(failures_path.read_text()))

    def test_record_refusal_blocks_and_alerts_on_first_failure_with_no_budget_spent(self):
        self.record_rc = 2
        with patch.object(self.reply, "notify") as notify:
            self.assertEqual(2, self.handle("queues", "t_intake"))
        self.assertEqual([], self.sends())
        self.assertEqual(1, len(self.blocks()))
        self.assertEqual(1, notify.call_count)
        self.assertFalse((self.root / self.op / self.reply.TRACKER_RECORD_FAILURES).exists())

    def test_no_key_file_record_creates_no_counter_and_never_blocks(self):
        """The inertness claim on the failure path, as a test."""
        with patch.object(self.reply, "block_and_alert") as block_and_alert:
            self.assertEqual(0, self.scan())
        block_and_alert.assert_not_called()
        self.assertFalse((self.root / self.op / self.reply.TRACKER_RECORD_FAILURES).exists())

    def test_tracker_container_env_var_selects_docker_exec_or_local(self):
        seen = []

        def fake_command(argv):
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch.object(self.reply, "command", side_effect=fake_command):
            self.reply.tracker(self.root, "--record", "chat-abc")
        self.assertEqual(
            [sys.executable, str(self.reply.HERE / "tracker-sync"), "--record", "chat-abc",
             "--work-root", str(self.root)],
            seen[-1])

        with patch.dict(os.environ, {"RALPH_TRACKER_CONTAINER": "ralph-tracker-1"}), \
                patch.object(self.reply, "command", side_effect=fake_command):
            self.reply.tracker(self.root, "--settle")
        self.assertEqual(
            ["docker", "exec", "ralph-tracker-1", "python3", "/ralph/bootstrap/tracker-sync",
             "--settle", "--work-root", "/workspaces/chat-inbox"],
            seen[-1])

    def test_a_tracker_container_that_is_not_running_is_the_tracker_not_configured(self):
        """The sidecar names `<instance>-tracker-sync-1` by default, but that
        container only exists under the `tracker` profile. A failed docker exec
        into it must not hold the acknowledgement: it is the inert path."""
        for message in ("Error response from daemon: No such container: ralph-tracker-sync-1",
                        "Error response from daemon: container abc123 is not running"):
            with self.subTest(message=message):
                self.setUp()
                self.tracker_sync = lambda argv, message=message: subprocess.CompletedProcess(
                    argv, 1, "", message)
                with patch.dict(os.environ, {"RALPH_TRACKER_CONTAINER": "ralph-tracker-sync-1"}), \
                        patch.object(self.reply, "notify") as notify:
                    self.assertEqual(0, self.handle("queues", "t_intake"))
                self.assertEqual(1, len(self.sends()))
                self.assertEqual([], self.blocks())
                self.assertEqual(0, notify.call_count)
                self.assertFalse((self.root / self.op / self.reply.TRACKER_RECORD_FAILURES).exists())

    def test_a_tracker_container_that_is_down_does_not_fail_the_scan(self):
        self.tracker_sync = lambda argv: subprocess.CompletedProcess(
            argv, 1, "", "Error response from daemon: No such container: ralph-tracker-sync-1")
        with patch.dict(os.environ, {"RALPH_TRACKER_CONTAINER": "ralph-tracker-sync-1"}):
            self.assertEqual(0, self.scan())
        self.assertEqual(2, len(self.sends()))      # the acknowledgement and the final reply

    def test_a_real_record_failure_through_the_container_still_holds_the_acknowledgement(self):
        """Only docker's own 'container is down' message is the inert path. A
        tracker-sync that ran and failed is a failure."""
        self.tracker_sync = lambda argv: subprocess.CompletedProcess(
            argv, 1, "", "Traceback ...\ntracker.TrackerError: tracker returned 500")
        with patch.dict(os.environ, {"RALPH_TRACKER_CONTAINER": "ralph-tracker-sync-1"}):
            self.assertEqual(1, self.handle("queues", "t_intake"))
        self.assertEqual([], self.sends())

    def test_the_fifth_failure_says_the_card_is_blocked_not_that_it_will_retry(self):
        self.record_rc = 1
        printed = io.StringIO()
        with patch.object(self.reply, "notify") as notify, \
                patch("sys.stderr", printed):
            for _ in range(self.reply.TRACKER_RECORD_ATTEMPTS):
                self.handle("queues", "t_intake")
        alert = notify.call_args[0][0]
        self.assertIn("blocked", alert)
        self.assertNotIn("retrying next scan", alert)

    def test_held_ack_still_records_then_holds_but_held_reply_never_records(self):
        """A held ack (CHAT_SEND off) still records, then holds; a held
        reply never records."""
        self.set_send("0")
        self.assertEqual(0, self.scan())
        self.assertEqual(1, len(self.tracker_calls("--record")))
        self.assertTrue((self.root / self.op / ".held-ack").is_file())
        self.assertFalse((self.root / self.op / ".receipt-sent").is_file())

        del self.cards[("ralph", "t_final")]
        other = self.make_operation("spaces/example/messages/held-reply")
        self.cards[("queues", "t_held_reply")] = {
            "task": {"id": "t_held_reply", "status": "done", "assignee": "ralph-chat-maintainer",
                     "body": f"operation_id: {other}\n",
                     "workspace_path": f"/workspaces/chat-inbox/{other}"},
            "metadata": {"decision": "reply", "body": "I can't help with payroll questions."}}
        self.calls.clear()
        self.assertEqual(0, self.handle_with_send("queues", "t_held_reply", send_enabled=False))
        self.assertEqual([], self.tracker_calls("--record"))
        self.assertTrue((self.root / other / ".held-reply").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
