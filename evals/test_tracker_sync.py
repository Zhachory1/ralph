"""bootstrap/tracker-sync: `--record` and the convergence guard, against a
stateful fake tracker and a temporary work root. No test here opens a
socket, reads a credential, or reaches a real tracker.
"""

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
import unittest.mock
import urllib.parse

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "bootstrap" / "tracker-sync"


def load_script():
    loader = importlib.machinery.SourceFileLoader("tracker_sync", str(SCRIPT))
    spec = importlib.util.spec_from_loader("tracker_sync", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


# `tracker-sync` itself imports `bootstrap/tracker.py` (as `tracker_client`) by
# inserting its own directory onto `sys.path`; reusing that same module object
# here, rather than loading a second copy, is what makes `isinstance` checks
# against `tracker.TrackerError` agree between the two (two separately loaded
# copies of one file are two different classes of the same name).
tracker = load_script().tracker_client

STATUS_NAMES = ("New", "In progress", "Waiting on requester", "Resolved", "Duplicate", "Closed",
                "Rejected")
THREAD_FIELD = 7
PROJECT = "ralph"


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
    """A minimal in-memory Redmine: enough of `/issues.json`,
    `/issues/<id>.json`, `/issues/<id>/relations.json` and
    `/issue_statuses.json` for `bootstrap/tracker.py`'s real code to run
    against, so a create a caller makes is visible to a later find the same
    or a different caller makes -- unlike a scripted-answer fake, which
    cannot show that round trip."""

    def __init__(self):
        self.issues = {}
        self.relations = []
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
            thread = params.get("cf_%s" % THREAD_FIELD)
            matches = [self._public(i) for i in self.issues.values()
                       if self._thread_value(i) == thread]
            return FakeResponse(json.dumps({"issues": matches}).encode())
        if path == "/issues.json" and method == "POST":
            payload = json.loads(request.data)["issue"]
            issue_id = self.next_id
            self.next_id += 1
            payload["id"] = issue_id
            self._resolve_status(payload)
            self.issues[issue_id] = payload
            return FakeResponse(json.dumps({"issue": self._public(payload)}).encode())
        if path.startswith("/issues/") and path.endswith(".json") and method == "PUT":
            issue_id = int(path[len("/issues/"):-len(".json")])
            payload = json.loads(request.data)["issue"]
            stored = self.issues[issue_id]
            if "notes" in payload:
                stored.setdefault("notes", []).append(payload["notes"])
            for key, value in payload.items():
                if key == "custom_fields":
                    stored["custom_fields"] = value
                elif key != "notes":
                    stored[key] = value
            self._resolve_status(stored)
            return FakeResponse(b"{}")
        if path.endswith("/relations.json") and method == "POST":
            issue_id = int(path[len("/issues/"):-len("/relations.json")])
            payload = json.loads(request.data)["relation"]
            self.relations.append((issue_id, payload["issue_to_id"]))
            return FakeResponse(b"{}")
        raise AssertionError("unhandled request: %s %s" % (method, request.full_url))

    def _resolve_status(self, payload):
        if "status_id" in payload:
            payload["status"] = STATUS_NAMES[payload["status_id"] - 1]

    def _thread_value(self, issue):
        for field in issue.get("custom_fields", []):
            if field.get("id") == THREAD_FIELD:
                return field.get("value") or None
        return None

    def _public(self, issue):
        return dict(issue)


def make_config():
    return tracker.Config(url="https://tracker.example", key="secret", project=PROJECT,
                          thread_field=THREAD_FIELD, statuses={})


def message_name(space, n=1):
    return "%s/messages/%d" % (space, n)


def op_id_for(message):
    return "chat-" + hashlib.sha256(message.encode()).hexdigest()


class TrackerSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name) / "chat-inbox"
        self.root.mkdir()
        self.backend = StatefulFakeTracker()
        self.module = load_script()
        self.cards = {}
        self.module.hermes = self._fake_hermes

    def client(self):
        return tracker.Tracker(make_config(), opener=self.backend)

    def _fake_hermes(self, *args):
        import subprocess
        if "list" in args:
            board = args[args.index("--board") + 1]
            tasks = [card["task"] for (b, _), card in self.cards.items() if b == board]
            return subprocess.CompletedProcess(args, 0, json.dumps({"tasks": tasks}), "")
        if "show" in args:
            board = args[args.index("--board") + 1]
            task_id = args[args.index("show") + 1]
            card = self.cards[(board, task_id)]
            return subprocess.CompletedProcess(args, 0, json.dumps({
                "task": card["task"],
                "runs": [{"outcome": "completed", "metadata": card["metadata"]}],
            }), "")
        raise AssertionError(args)

    def stage(self, space="spaces/example", thread_name="first", n=1, prior=(),
              sender_trusted=True, text="please help"):
        thread = "%s/threads/%s" % (space, thread_name)
        message = message_name(space, n)
        op = op_id_for(message)
        evidence = self.root / op / "evidence"
        evidence.mkdir(parents=True)
        item = {"kind": "inbound", "sender_trusted": sender_trusted, "space": space,
                "thread": thread, "sender": "a requester",
                "messages": [{"name": message, "text": text}], "prior_operations": list(prior)}
        path = evidence / "item.json"
        path.write_text(json.dumps(item))
        path.chmod(0o600)
        return op

    def intake_card(self, op, ack="On it.", kind="question", task_id=None):
        task_id = task_id or ("t_intake_" + op[-6:])
        self.cards[("queues", task_id)] = {
            "task": {"id": task_id, "status": "done", "assignee": "ralph-chat-maintainer",
                     "body": "operation_id: %s\n" % op},
            "metadata": {"decision": "route", "body": "do the thing", "ack": ack, "kind": kind},
        }

    def recorded_id(self, op):
        return (self.root / op / self.module.RECORDED).read_text()

    # --- not configured -----------------------------------------------------

    def test_record_not_configured_prints_and_exits_zero(self):
        creds = pathlib.Path(self.tmp.name) / "missing-creds.json"
        with unittest.mock.patch.dict(os.environ, {self.module.TRACKER_CREDS_ENV: str(creds)}):
            rc = self.module.main(["--record", "chat-" + "0" * 64,
                                   "--work-root", str(self.root)])
        self.assertEqual(rc, 0)

    # --- --record -------------------------------------------------------------

    def test_record_creates_one_item_with_requester_ask_kind_state_and_thread(self):
        op = self.stage(text="draft an announcement")
        self.intake_card(op, ack="On it: drafting now.", kind="question")
        client = self.client()
        rc = self.module.record(client, self.root, op)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.backend.issues), 1)
        issue = next(iter(self.backend.issues.values()))
        self.assertEqual(issue["requester"], "a requester")
        self.assertIn("draft an announcement", issue["description"])
        self.assertEqual(issue["kind"], "question")
        self.assertEqual(issue["status"], "In progress")
        self.assertEqual(self.backend._thread_value(issue), "spaces/example/threads/first")
        self.assertEqual(self.recorded_id(op), str(issue["id"]))

    def test_second_record_over_same_operation_creates_nothing(self):
        op = self.stage()
        self.intake_card(op)
        client = self.client()
        self.module.record(client, self.root, op)
        before = dict(self.backend.issues)
        marker_before = self.recorded_id(op)
        rc = self.module.record(client, self.root, op)
        self.assertEqual(rc, 0)
        self.assertEqual(self.backend.issues, before)
        self.assertEqual(self.recorded_id(op), marker_before)

    def test_thread_with_existing_item_is_found_not_recreated(self):
        op1 = self.stage(n=1)
        op2 = self.stage(n=2, prior=(op1,))
        self.intake_card(op1)
        self.intake_card(op2)
        client = self.client()
        self.module.record(client, self.root, op1)
        self.assertEqual(len(self.backend.issues), 1)
        self.module.record(client, self.root, op2)
        self.assertEqual(len(self.backend.issues), 1)
        self.assertEqual(self.recorded_id(op1), self.recorded_id(op2))

    def test_forced_double_create_converges_on_the_oldest(self):
        op1 = self.stage(n=1)
        op2 = self.stage(n=2, prior=(op1,))
        self.intake_card(op1)
        self.intake_card(op2)
        client = self.client()
        thread = "spaces/example/threads/first"
        # Force the race the per-thread lock would normally prevent: both
        # operations create before either reconciles.
        first = client.create({"thread": thread, "subject": "a", "description": "a"})
        second = client.create({"thread": thread, "subject": "b", "description": "b"})
        self.module.write_marker(self.root / op1 / self.module.RECORDED, str(first["id"]))
        self.module.write_marker(self.root / op2 / self.module.RECORDED, str(second["id"]))

        rc = self.module.record(client, self.root, op1)
        self.assertEqual(rc, 0)
        canonical_id = min(first["id"], second["id"])
        loser_id = max(first["id"], second["id"])
        self.assertEqual(self.recorded_id(op1), str(canonical_id))
        self.assertEqual(self.backend.issues[loser_id]["status"], "Duplicate")
        self.assertIsNone(self.backend._thread_value(self.backend.issues[loser_id]))
        self.assertIn((loser_id, canonical_id), self.backend.relations)
        self.assertIn("canonical item %s" % canonical_id,
                      self.backend.issues[loser_id]["notes"][-1])
        canonical, newer = client.find(thread)
        self.assertEqual(canonical["id"], canonical_id)
        self.assertEqual(newer, [])

    def test_losers_own_operation_settles_against_canonical_on_its_next_record(self):
        op1 = self.stage(n=1)
        op2 = self.stage(n=2, prior=(op1,))
        self.intake_card(op1)
        self.intake_card(op2)
        client = self.client()
        thread = "spaces/example/threads/first"
        first = client.create({"thread": thread, "subject": "a", "description": "a"})
        second = client.create({"thread": thread, "subject": "b", "description": "b"})
        self.module.write_marker(self.root / op1 / self.module.RECORDED, str(first["id"]))
        self.module.write_marker(self.root / op2 / self.module.RECORDED, str(second["id"]))
        canonical_id = min(first["id"], second["id"])
        loser_op = op1 if first["id"] != canonical_id else op2
        winner_op = op2 if loser_op == op1 else op1

        self.module.record(client, self.root, winner_op)
        self.assertEqual(self.recorded_id(loser_op), str(max(first["id"], second["id"])))

        rc = self.module.record(client, self.root, loser_op)
        self.assertEqual(rc, 0)
        self.assertEqual(self.recorded_id(loser_op), str(canonical_id))

    def test_write_a_dropped_connection_never_resends_within_one_call(self):
        """From the merged client (dd-tracker.md, bootstrap/tracker.py): a
        write that hits a dropped connection fails once, with no second
        write sent -- the exact behaviour `record()` relies on to let its
        own bounded retry run the whole step again from `find`."""
        import urllib.error

        class DroppedOpener:
            def __init__(self):
                self.calls = 0

            def open(self, request, timeout=None):
                self.calls += 1
                raise urllib.error.URLError("reset")

        opener = DroppedOpener()
        client = tracker.Tracker(make_config(), opener=opener)
        with self.assertRaises(tracker.TrackerError):
            client.create({"thread": "t", "subject": "x", "description": "x"})
        self.assertEqual(opener.calls, 1)


if __name__ == "__main__":
    unittest.main()
