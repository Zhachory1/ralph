"""The public Chat producer's read/eligibility (PR 6) and staging/card
creation (PR 7): the item record, prior_operations, the cutover guard."""

import datetime
import hashlib
import importlib.machinery
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "bootstrap" / "chat-producer"
BOOTSTRAP = pathlib.Path(__file__).resolve().parent.parent / "bootstrap"
sys.path.insert(0, str(BOOTSTRAP))
import chat_evidence

NOW = datetime.datetime(2026, 11, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)
CUTOFF = NOW - datetime.timedelta(seconds=300)
THREAD = "spaces/example/threads/first"


def load_script():
    loader = importlib.machinery.SourceFileLoader("chat_producer", str(SCRIPT))
    spec = importlib.util.spec_from_loader("chat_producer", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def message(name="spaces/example/messages/one", created=None, sender_type="HUMAN",
            client_id=None):
    msg = {"name": name, "createTime": (created or NOW).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "sender": {"type": sender_type}}
    if client_id is not None:
        msg["clientAssignedMessageId"] = client_id
    return msg


def thread_message(name, text="hi", from_ralph_id=None, thread=THREAD):
    """A message resource shaped for list_thread/stage."""
    msg = {"name": name, "text": text, "thread": {"name": thread},
           "createTime": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}
    if from_ralph_id:
        msg["clientAssignedMessageId"] = from_ralph_id
    return msg


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.producer = load_script()
        self.allowed = {"spaces/example"}

    def eligible(self, msg, space="spaces/example"):
        return self.producer.eligible(msg, space, self.allowed, CUTOFF)

    def test_unlisted_space_is_not_eligible(self):
        self.assertFalse(self.eligible(message(), space="spaces/other"))

    def test_message_older_than_window_is_not_eligible(self):
        old = CUTOFF - datetime.timedelta(seconds=1)
        self.assertFalse(self.eligible(message(created=old)))

    def test_bot_sender_is_not_eligible(self):
        self.assertFalse(self.eligible(message(sender_type="BOT")))

    def test_ralph_client_id_is_not_eligible(self):
        self.assertFalse(self.eligible(
            message(client_id="client-ralph-ack-deadbeef")))

    def test_human_message_in_listed_space_is_eligible(self):
        self.assertTrue(self.eligible(message()))


class ReadSpacesTests(unittest.TestCase):
    def setUp(self):
        self.producer = load_script()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = pathlib.Path(self.tmp.name) / "instance.env"

    def write(self, instance, spaces):
        self.path.write_text(f"RALPH_INSTANCE={instance}\n"
                              f"CHAT_ROUTE_SPACES={spaces}\n")

    def test_valid_config_is_accepted(self):
        self.write("ralph-work", "spaces/example spaces/other")
        self.assertEqual({"spaces/example", "spaces/other"},
                         self.producer.read_spaces(self.path))

    def test_nine_spaces_is_refused(self):
        spaces = " ".join(f"spaces/room{n}" for n in range(9))
        self.write("ralph-work", spaces)
        with self.assertRaises(ValueError):
            self.producer.read_spaces(self.path)

    def test_malformed_space_is_refused(self):
        self.write("ralph-work", "not-a-space")
        with self.assertRaises(ValueError):
            self.producer.read_spaces(self.path)

    def test_empty_space_list_is_refused(self):
        self.write("ralph-work", "")
        with self.assertRaises(ValueError):
            self.producer.read_spaces(self.path)


class OperationIdTests(unittest.TestCase):
    def test_operation_id_is_chat_plus_sha256_of_message_name(self):
        producer = load_script()
        name = "spaces/example/messages/one"
        expected = "chat-" + hashlib.sha256(name.encode()).hexdigest()
        self.assertEqual(expected, producer.operation_id(name))


class FakeMessages:
    """A fake spaces().messages().list() chain. `pages` is a list of
    responses returned in order to plain (unfiltered) calls; a call that
    passes `filter` raises, simulating an API that rejects it."""

    def __init__(self, pages, reject_filter=True):
        self.pages = list(pages)
        self.reject_filter = reject_filter
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        if self.reject_filter and "filter" in kwargs:
            raise ValueError("create_time filter not supported")
        return FakeExec(self.pages.pop(0))


class FakeExec:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeSpaces:
    def __init__(self, messages):
        self.messages_obj = messages

    def messages(self):
        return self.messages_obj


class FakeService:
    def __init__(self, messages):
        self.spaces_obj = FakeSpaces(messages)

    def spaces(self):
        return self.spaces_obj


class ListRecentFallbackTests(unittest.TestCase):
    def test_fallback_pages_and_stops_at_first_old_message(self):
        producer = load_script()
        in_window = message(name="spaces/example/messages/new", created=NOW)
        at_edge = message(name="spaces/example/messages/edge",
                           created=CUTOFF + datetime.timedelta(seconds=1))
        too_old = message(name="spaces/example/messages/old",
                           created=CUTOFF - datetime.timedelta(seconds=1))
        never_seen = message(name="spaces/example/messages/ancient",
                             created=CUTOFF - datetime.timedelta(seconds=600))
        page_one = {"messages": [in_window, at_edge, too_old], "nextPageToken": "p2"}
        page_two = {"messages": [never_seen]}
        fake = FakeMessages([page_one, page_two], reject_filter=True)
        svc = FakeService(fake)
        result = producer.list_recent(svc, "spaces/example", CUTOFF)
        self.assertEqual([in_window["name"], at_edge["name"]],
                         [m["name"] for m in result])
        # The filter is rejected once; the fallback then stops at the first
        # old message, so page two is unread.
        self.assertEqual(2, len(fake.calls))

    def test_filtered_request_uses_the_documented_field_and_succeeds(self):
        """The API accepts the filter, so one call returns the window and
        the fallback never runs; the filter must name `create_time`, not
        `createTime`, which the Chat v1 API rejects."""
        producer = load_script()
        in_window = message(name="spaces/example/messages/fresh",
                            created=CUTOFF + datetime.timedelta(seconds=30))
        fake = FakeMessages([{"messages": [in_window]}], reject_filter=False)
        svc = FakeService(fake)
        result = producer.list_recent(svc, "spaces/example", CUTOFF)
        self.assertEqual([in_window["name"]], [m["name"] for m in result])
        self.assertEqual(1, len(fake.calls))
        sent = fake.calls[0]["filter"]
        self.assertTrue(sent.startswith('create_time > "'), sent)
        self.assertNotIn("createTime", sent)
        self.assertTrue(sent.endswith('+00:00"'), sent)


class FakeChatService:
    """Serves both list_recent's createTime filter and list_thread's
    thread.name filter: one canned page, keyed on the filter passed."""

    def __init__(self, window_messages, thread_messages=None):
        self.window_messages = window_messages
        self.thread_messages = thread_messages if thread_messages is not None \
            else window_messages
        self.calls = []

    def spaces(self):
        return self

    def messages(self):
        return self

    def list(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("filter", "").startswith("thread.name"):
            return FakeExec({"messages": self.thread_messages})
        return FakeExec({"messages": self.window_messages})


class StageTestCase(unittest.TestCase):
    """A fresh chat-inbox root, for tests that drive stage() directly."""

    def setUp(self):
        self.producer = load_script()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name) / "chat-inbox"
        self.root.mkdir(parents=True)

    def stage(self, messages, triggering, allow_unseen_history=False):
        return self.producer.stage(FakeChatService(messages), self.root,
                                   "spaces/example", triggering, allow_unseen_history)


class SchemaConformanceTests(StageTestCase):
    def test_staged_record_passes_chat_evidence_read_item(self):
        """Schema conformance proved by the consumer, not a second copy."""
        msg = thread_message("spaces/example/messages/one")
        op_id, staged = self.stage([msg], msg)
        self.assertTrue(staged)
        item = chat_evidence.read_item(self.root, op_id)
        self.assertEqual("inbound", item["kind"])
        self.assertTrue(item["sender_trusted"])


class WholeThreadStagedTests(StageTestCase):
    def test_later_message_carries_earlier_ones_as_context(self):
        first = thread_message("spaces/example/messages/one", text="first question")
        second = thread_message("spaces/example/messages/two", text="follow-up")
        op_id, staged = self.stage([first, second], second)
        self.assertTrue(staged)
        item = chat_evidence.read_item(self.root, op_id)
        self.assertEqual([first["name"], second["name"]],
                         [m["name"] for m in item["messages"]])
        self.assertEqual(["first question"], item["messages"][0]["text"])


class SenderAndFromRalphTests(StageTestCase):
    def test_sender_present_and_from_ralph_true_for_exactly_ralphs_messages(self):
        ralph_msg = thread_message("spaces/example/messages/ralph-one",
                                   from_ralph_id="client-ralph-ack-deadbeef")
        human_msg = thread_message("spaces/example/messages/two")
        human_msg["sender"] = {"name": "users/123", "displayName": "Alex"}
        # Steady state: Ralph's reply already has a staged directory.
        (self.root / self.producer.operation_id(ralph_msg["name"])).mkdir()
        op_id, staged = self.stage([ralph_msg, human_msg], human_msg)
        self.assertTrue(staged)
        item = chat_evidence.read_item(self.root, op_id)
        self.assertEqual({"name": "users/123", "displayName": "Alex"}, item["sender"])
        flags = {m["name"]: m["from_ralph"] for m in item["messages"]}
        self.assertEqual({ralph_msg["name"]: True, human_msg["name"]: False}, flags)


class CutoverGuardTests(StageTestCase):
    def setUp(self):
        super().setUp()
        self.ralph_msg = thread_message("spaces/example/messages/ralph-one",
                                        from_ralph_id="client-ralph-ack-deadbeef")
        self.human_msg = thread_message("spaces/example/messages/two")

    def guarded_stage(self, allow_unseen_history=False):
        return self.stage([self.ralph_msg, self.human_msg], self.human_msg,
                          allow_unseen_history)

    def test_ralph_reply_with_no_visible_prior_directory_stages_nothing(self):
        op_id, staged = self.guarded_stage()
        self.assertFalse(staged)
        self.assertFalse((self.root / op_id).exists())

    def test_same_thread_with_one_prior_directory_stages_normally(self):
        (self.root / self.producer.operation_id(self.ralph_msg["name"])).mkdir()
        op_id, staged = self.guarded_stage()
        self.assertTrue(staged)
        self.assertTrue((self.root / op_id / "evidence" / "item.json").is_file())

    def test_allow_unseen_history_stages_the_guarded_thread(self):
        op_id, staged = self.guarded_stage(allow_unseen_history=True)
        self.assertTrue(staged)
        self.assertTrue((self.root / op_id / "evidence" / "item.json").is_file())


class PriorOperationsTests(StageTestCase):
    def staged(self, count):
        """`count` earlier messages, oldest first, each already staged."""
        names = [f"spaces/example/messages/m{n}" for n in range(count)]
        ids = []
        for name in names:
            op_id = self.producer.operation_id(name)
            (self.root / op_id).mkdir()
            ids.append(op_id)
        return [thread_message(name) for name in names], ids

    def test_four_earlier_staged_operations_yield_four_newest_first(self):
        msgs, ids = self.staged(4)
        current = thread_message("spaces/example/messages/current")
        result = self.producer.prior_operations(
            msgs + [current], self.producer.operation_id(current["name"]), self.root)
        self.assertEqual(list(reversed(ids)), result)

    def test_twenty_five_earlier_yields_twenty(self):
        msgs, ids = self.staged(25)
        current = thread_message("spaces/example/messages/current")
        result = self.producer.prior_operations(
            msgs + [current], self.producer.operation_id(current["name"]), self.root)
        self.assertEqual(list(reversed(ids))[:20], result)

    def test_this_operation_never_appears_in_its_own_list(self):
        msgs, ids = self.staged(2)
        op_id = self.producer.operation_id(msgs[-1]["name"])
        self.assertNotIn(op_id, self.producer.prior_operations(msgs, op_id, self.root))


class OnceIdempotencyTests(unittest.TestCase):
    """E2: a rerun over the same message creates no second card."""

    def setUp(self):
        self.producer = load_script()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name) / "chat-inbox"
        self.message_name = "spaces/example/messages/one"
        self.svc = FakeChatService([thread_message(self.message_name)])
        self.calls = []

    def fake_command(self, argv):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, '{"id": "t_x"}', "")

    def run_once(self):
        with patch.object(self.producer, "command", side_effect=self.fake_command):
            return self.producer.once(self.svc, {"spaces/example"},
                                      "ralph-work-hermes-1", self.root, False, now=NOW)

    def test_rerun_over_same_message_creates_no_second_directory_or_card(self):
        self.assertEqual(0, self.run_once())
        op_id = self.producer.operation_id(self.message_name)
        self.assertTrue((self.root / op_id / "evidence" / "item.json").is_file())
        self.assertEqual(0, self.run_once())
        self.assertEqual(1, len(self.calls))


if __name__ == "__main__":
    unittest.main(verbosity=2)
