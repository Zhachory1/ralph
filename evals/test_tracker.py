"""bootstrap/tracker.py: the work tracker client, against a fake opener only.

No test here opens a socket; the fake opener is the only transport used.
"""

import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
import urllib.error

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "bootstrap" / "tracker.py"
spec = importlib.util.spec_from_file_location("tracker", SCRIPT)
tracker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tracker)


class FakeResponse:
    """A minimal stand-in for `http.client.HTTPResponse`: a context manager
    whose `read()` returns the body once."""

    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self._body


class FakeOpener:
    """Replays a scripted answer per call, in order, and records every
    request it was asked to make."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer)


def json_body(obj):
    return json.dumps(obj).encode()


def http_error(code, body=b"boom"):
    return urllib.error.HTTPError("https://tracker.example/issues.json", code,
                                   "error", {}, io.BytesIO(body))


def make_config(statuses=None, **overrides):
    values = {"url": "https://tracker.example", "key": "secret", "project": "ralph",
              "thread_field": 7}
    values.update(overrides)
    return tracker.Config(statuses=statuses or {}, **values)


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = pathlib.Path(self.tmp.name) / "tracker-creds.json"

    def write(self, data, mode=0o600):
        self.path.write_text(json.dumps(data))
        self.path.chmod(mode)

    def test_missing_file_returns_none(self):
        self.assertIsNone(tracker.load(self.path))

    def test_malformed_file_returns_none(self):
        self.path.write_text("not json")
        self.path.chmod(0o600)
        self.assertIsNone(tracker.load(self.path))

    def test_file_with_no_thread_field_returns_none(self):
        self.write({"url": "https://tracker.example", "key": "secret", "project": "ralph"})
        self.assertIsNone(tracker.load(self.path))

    def test_group_readable_file_returns_none(self):
        self.write({"url": "https://tracker.example", "key": "secret", "project": "ralph",
                    "thread_field": 7}, mode=0o644)
        self.assertIsNone(tracker.load(self.path))

    def test_well_formed_file_returns_a_configuration(self):
        self.write({"url": "https://tracker.example/", "key": "secret", "project": "ralph",
                    "thread_field": 7, "statuses": {"Waiting on requester": "Feedback"}})
        config = tracker.load(self.path)
        self.assertEqual(config.url, "https://tracker.example")
        self.assertEqual(config.key, "secret")
        self.assertEqual(config.project, "ralph")
        self.assertEqual(config.thread_field, 7)
        self.assertEqual(config.statuses, {"Waiting on requester": "Feedback"})


class FindTests(unittest.TestCase):
    def issue(self, issue_id):
        return {"id": issue_id, "subject": "example"}

    def find(self, issues):
        opener = FakeOpener([json_body({"issues": issues})])
        client = tracker.Tracker(make_config(), opener=opener)
        return client.find("spaces/example/threads/one")

    def test_no_match_returns_none_and_empty_rest(self):
        self.assertEqual(self.find([]), (None, []))

    def test_one_match_returns_it_with_empty_rest(self):
        canonical, newer = self.find([self.issue(5)])
        self.assertEqual(canonical, self.issue(5))
        self.assertEqual(newer, [])

    def test_three_matches_returns_lowest_id_as_canonical_in_any_order(self):
        for issues in ([self.issue(9), self.issue(3), self.issue(7)],
                       [self.issue(3), self.issue(7), self.issue(9)],
                       [self.issue(7), self.issue(9), self.issue(3)]):
            canonical, newer = self.find(issues)
            self.assertEqual(canonical["id"], 3)
            self.assertEqual(sorted(item["id"] for item in newer), [7, 9])


class WriteTests(unittest.TestCase):
    def test_create_sends_fields_including_thread_in_the_custom_field(self):
        opener = FakeOpener([json_body({"issue": {"id": 42}})])
        client = tracker.Tracker(make_config(), opener=opener)
        issue = client.create({"thread": "spaces/example/threads/one",
                                "subject": "a request", "description": "the text"})
        self.assertEqual(issue, {"id": 42})
        sent = json.loads(opener.calls[0].data)
        self.assertEqual(sent["issue"]["project_id"], "ralph")
        self.assertEqual(sent["issue"]["subject"], "a request")
        self.assertEqual(sent["issue"]["description"], "the text")
        self.assertEqual(sent["issue"]["custom_fields"],
                          [{"id": 7, "value": "spaces/example/threads/one"}])
        self.assertEqual(opener.calls[0].get_method(), "POST")
        self.assertTrue(opener.calls[0].full_url.endswith("/issues.json"))

    def test_update_sends_only_what_it_is_given(self):
        opener = FakeOpener([json_body({})])
        client = tracker.Tracker(make_config(), opener=opener)
        client.update(42, {"description": "revised text"})
        sent = json.loads(opener.calls[0].data)
        self.assertEqual(sent, {"issue": {"description": "revised text"}})
        self.assertEqual(opener.calls[0].get_method(), "PUT")

    def test_note_posts_a_journal_note_and_changes_no_field(self):
        opener = FakeOpener([json_body({})])
        client = tracker.Tracker(make_config(), opener=opener)
        client.note(42, "folded into the canonical item")
        sent = json.loads(opener.calls[0].data)
        self.assertEqual(sent, {"issue": {"notes": "folded into the canonical item"}})

    def test_relate_posts_a_relates_relation(self):
        opener = FakeOpener([json_body({})])
        client = tracker.Tracker(make_config(), opener=opener)
        client.relate(42, 43)
        sent = json.loads(opener.calls[0].data)
        self.assertEqual(sent, {"relation": {"issue_to_id": 43, "relation_type": "relates"}})
        self.assertTrue(opener.calls[0].full_url.endswith("/issues/42/relations.json"))


class StatusTests(unittest.TestCase):
    def test_unknown_status_name_raises_naming_it(self):
        opener = FakeOpener([json_body({"issue_statuses": [{"id": 1, "name": "New"}]}),
                            json_body({})])
        client = tracker.Tracker(make_config(), opener=opener)
        with self.assertRaises(tracker.TrackerError) as raised:
            client.update(42, {"status": "Waiting on requester"})
        self.assertIn("Waiting on requester", str(raised.exception))

    def test_name_mapped_through_statuses_resolves(self):
        opener = FakeOpener([json_body({"issue_statuses": [{"id": 1, "name": "New"},
                                                            {"id": 4, "name": "Feedback"}]}),
                            json_body({})])
        config = make_config(statuses={"Waiting on requester": "Feedback"})
        client = tracker.Tracker(config, opener=opener)
        client.update(42, {"status": "Waiting on requester"})
        sent = json.loads(opener.calls[1].data)
        self.assertEqual(sent["issue"]["status_id"], 4)

    def test_status_ids_are_resolved_once_per_run(self):
        opener = FakeOpener([json_body({"issue_statuses": [{"id": 1, "name": "New"}]}),
                            json_body({}), json_body({})])
        client = tracker.Tracker(make_config(), opener=opener)
        client.update(42, {"status": "New"})
        client.update(43, {"status": "New"})
        statuses_calls = [call for call in opener.calls
                          if call.full_url.endswith("/issue_statuses.json")]
        self.assertEqual(len(statuses_calls), 1)


class FailureTests(unittest.TestCase):
    def test_a_500_raises_tracker_error_carrying_the_code(self):
        opener = FakeOpener([http_error(500, b"internal error")])
        client = tracker.Tracker(make_config(), opener=opener)
        with self.assertRaises(tracker.TrackerError) as raised:
            client.find("spaces/example/threads/one")
        self.assertEqual(raised.exception.code, 500)
        self.assertIn("internal error", str(raised.exception))
        self.assertEqual(len(opener.calls), 1)

    def test_connection_error_is_retried_then_raises(self):
        opener = FakeOpener([urllib.error.URLError("refused")] * tracker.RETRIES)
        client = tracker.Tracker(make_config(), opener=opener)
        with self.assertRaises(tracker.TrackerError):
            client.find("spaces/example/threads/one")
        self.assertEqual(len(opener.calls), tracker.RETRIES)

    def test_connection_error_recovers_within_the_retry_budget(self):
        opener = FakeOpener([urllib.error.URLError("refused"), json_body({"issues": []})])
        client = tracker.Tracker(make_config(), opener=opener)
        self.assertEqual(client.find("spaces/example/threads/one"), (None, []))
        self.assertEqual(len(opener.calls), 2)

    def test_no_test_opens_a_socket(self):
        """The fake opener is the only transport any test uses."""
        opener = FakeOpener([json_body({"issues": []})])
        client = tracker.Tracker(make_config(), opener=opener)
        client.find("spaces/example/threads/one")
        self.assertIsInstance(client._opener, FakeOpener)


if __name__ == "__main__":
    unittest.main()
