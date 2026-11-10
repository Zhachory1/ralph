"""The public Chat producer's read and eligibility (PR 6): no network, no
credential, nothing staged."""

import datetime
import hashlib
import importlib.machinery
import importlib.util
import pathlib
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "bootstrap" / "chat-producer"
NOW = datetime.datetime(2026, 11, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)
CUTOFF = NOW - datetime.timedelta(seconds=300)


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
        page = self.pages.pop(0)
        return FakeExec(page)


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
        # One call attempts the create_time filter and is rejected; the
        # fallback's first page then stops at the first old message, so the
        # second page is never read.
        self.assertEqual(2, len(fake.calls))

    def test_filtered_request_uses_the_documented_field_and_succeeds(self):
        """The routine path: the API accepts the filter, so one call returns
        the window and the unfiltered fallback never runs. The filter must
        name `create_time`, the field the Chat v1 discovery document
        documents; `createTime` is rejected as INVALID_ARGUMENT."""
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
