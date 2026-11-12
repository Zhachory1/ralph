"""bootstrap/tracker-work: open/update/close for an orchestrator-initiated
work item, against a stateful fake tracker. No test here opens a socket,
reads a credential, or reaches a real tracker.
"""

import importlib.machinery
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
import unittest.mock
import urllib.error
import urllib.parse

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "bootstrap" / "tracker-work"


def load_script():
    loader = importlib.machinery.SourceFileLoader("tracker_work", str(SCRIPT))
    spec = importlib.util.spec_from_loader("tracker_work", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


_LOADED = load_script()
tracker = _LOADED.tracker_client

STATUS_NAMES = ("New", "In Progress", "Resolved", "Feedback", "Closed", "Rejected")
THREAD_FIELD = 1
PROJECT = "work"


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self._body


class StatefulFakeTracker:
    """A minimal in-memory Redmine: enough of `/issues.json` and
    `/issues/<id>.json` for `bootstrap/tracker.py`'s real code to run
    against, so a create one call makes is visible to a later find another
    call makes."""

    def __init__(self):
        self.issues = {}
        self.next_id = 1

    def open(self, request, timeout=None):
        method = request.get_method()
        parsed = urllib.parse.urlsplit(request.full_url)
        path = parsed.path
        if path == "/issue_statuses.json" and method == "GET":
            body = {"issue_statuses": [{"id": i + 1, "name": name}
                                        for i, name in enumerate(STATUS_NAMES)]}
            return FakeResponse(json.dumps(body).encode())
        if path == "/issues.json" and method == "GET":
            params = dict(urllib.parse.parse_qsl(parsed.query))
            key = params.get("cf_%s" % THREAD_FIELD)
            matches = sorted((i for i in self.issues.values() if self._key_value(i) == key),
                             key=lambda i: i["id"])
            return FakeResponse(json.dumps({"issues": matches}).encode())
        if path == "/issues.json" and method == "POST":
            payload = json.loads(request.data)["issue"]
            issue_id = self.next_id
            self.next_id += 1
            payload["id"] = issue_id
            self._resolve_status(payload)
            self.issues[issue_id] = payload
            return FakeResponse(json.dumps({"issue": dict(payload)}).encode())
        if path.startswith("/issues/") and path.endswith(".json") and method == "PUT":
            issue_id = int(path[len("/issues/"):-len(".json")])
            payload = json.loads(request.data)["issue"]
            stored = self.issues[issue_id]
            for key, value in payload.items():
                if key == "custom_fields":
                    stored["custom_fields"] = value
                else:
                    stored[key] = value
            self._resolve_status(stored)
            return FakeResponse(b"{}")
        raise AssertionError("unhandled request: %s %s" % (method, request.full_url))

    def _resolve_status(self, payload):
        if "status_id" in payload:
            payload["status"] = {"id": payload["status_id"],
                                 "name": STATUS_NAMES[payload["status_id"] - 1]}

    def _key_value(self, issue):
        for field in issue.get("custom_fields", []):
            if field.get("id") == THREAD_FIELD:
                return field.get("value") or None
        return None


def make_config(statuses=None):
    return tracker.Config(url="https://tracker.example", key="secret", project=PROJECT,
                          thread_field=THREAD_FIELD, statuses=statuses or {})


class TrackerWorkTests(unittest.TestCase):
    def setUp(self):
        self.backend = StatefulFakeTracker()
        self.module = load_script()
        self.module.tracker_client = tracker

    def client(self):
        return tracker.Tracker(make_config(), opener=self.backend)

    def run_main(self, *args):
        with unittest.mock.patch.object(self.module, "load_client", return_value=self.client()):
            return self.module.main(list(args))

    def issue_for(self, op_id):
        matches = [i for i in self.backend.issues.values() if self._key_value(i) == op_id]
        self.assertEqual(1, len(matches))
        return matches[0]

    def _key_value(self, issue):
        return self.backend._key_value(issue)

    def test_open_creates_one_item_in_progress(self):
        self.assertEqual(0, self.run_main("--open", "work-abc", "--title", "Ship the thing"))
        issue = self.issue_for("work-abc")
        self.assertEqual("Ship the thing", issue["subject"])
        self.assertEqual("In Progress", issue["status"]["name"])

    def test_open_is_idempotent_on_retry(self):
        self.run_main("--open", "work-abc", "--title", "Ship the thing")
        self.run_main("--open", "work-abc", "--title", "Ship the thing")
        self.assertEqual(1, len(self.backend.issues))

    def test_open_without_title_is_refused(self):
        self.assertEqual(2, self.run_main("--open", "work-abc", "--title", ""))
        self.assertEqual(0, len(self.backend.issues))

    def test_update_sets_a_stock_status_any_caller_may_use(self):
        self.run_main("--open", "work-abc", "--title", "Ship the thing")
        self.assertEqual(0, self.run_main("--update", "work-abc", "--status", "Feedback"))
        self.assertEqual("Feedback", self.issue_for("work-abc")["status"]["name"])

    def test_update_with_no_open_item_is_refused(self):
        self.assertEqual(2, self.run_main("--update", "work-missing", "--status", "Feedback"))

    def test_update_with_unknown_status_fails(self):
        self.run_main("--open", "work-abc", "--title", "Ship the thing")
        self.assertEqual(1, self.run_main("--update", "work-abc", "--status", "Not A Status"))

    def test_close_sets_closed(self):
        self.run_main("--open", "work-abc", "--title", "Ship the thing")
        self.assertEqual(0, self.run_main("--close", "work-abc"))
        self.assertEqual("Closed", self.issue_for("work-abc")["status"]["name"])

    def test_close_with_no_open_item_is_refused(self):
        self.assertEqual(2, self.run_main("--close", "work-missing"))

    def test_two_different_operations_resolve_to_two_different_items(self):
        self.run_main("--open", "work-abc", "--title", "First")
        self.run_main("--open", "work-def", "--title", "Second")
        self.assertEqual(2, len(self.backend.issues))
        self.run_main("--update", "work-abc", "--status", "Feedback")
        self.assertEqual("Feedback", self.issue_for("work-abc")["status"]["name"])
        self.assertEqual("In Progress", self.issue_for("work-def")["status"]["name"])

    def test_no_credential_file_is_inert_on_every_flag(self):
        import os
        with unittest.mock.patch.dict(os.environ, {"RALPH_TRACKER_CREDS": "/no/such/file"}):
            self.assertEqual(0, self.module.main(["--open", "work-abc", "--title", "x"]))
            self.assertEqual(0, self.module.main(["--update", "work-abc", "--status", "x"]))
            self.assertEqual(0, self.module.main(["--close", "work-abc"]))
        self.assertEqual(0, len(self.backend.issues))


if __name__ == "__main__":
    unittest.main(verbosity=2)
