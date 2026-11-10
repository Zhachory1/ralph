"""Every call to the work tracker (a self-hosted Redmine) goes through here.

One module: `load` reads the credential file or reports "not configured" by
returning `None`; `Tracker.find` resolves a thread key to exactly one item by
the oldest-wins rule (dd-tracker.md:291-296, Section 3.3), and `create`,
`update`, `note` and `relate` are the only writes. No business logic builds
its own HTTP client, and no call happens at import time: the first network
access is the caller's, after it holds a `Config` from `load`.
"""

import json
import pathlib
import stat
import urllib.error
import urllib.parse
import urllib.request

# Q1's required keys in the credential file (projects/ralph-redmine/plan-tracker.md).
REQUIRED = ("url", "key", "project", "thread_field")
TIMEOUT = 10
# A connection failure (no response at all) is retried this many times before
# giving up, for a READ only. A definite non-2xx response is never retried: the
# server already answered, and retrying it would not change the answer. A WRITE
# is never retried here: a connection that drops after the request left may
# have been applied, and sending a create, a journal note or a relation again
# would duplicate it. The caller's own bounded retry runs the whole step again
# from `find`, which sees what landed.
RETRIES = 3
SAFE_METHODS = frozenset({"GET"})


class TrackerError(Exception):
    """A non-2xx tracker response, or a status name that does not resolve.

    `code` is the HTTP status for a response, and `None` for everything else
    (a status name that does not resolve, or a connection that never got one).
    """

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class Config:
    """Q1's single credential file: the base URL, the API key, the tracker
    project and the id of the thread-key custom field D1 creates by hand, plus
    an optional name-to-name override for Q2's two non-stock statuses."""

    def __init__(self, url, key, project, thread_field, statuses):
        self.url = url.rstrip("/")
        self.key = key
        self.project = project
        self.thread_field = thread_field
        self.statuses = statuses


def load(path):
    """Read Q1's credential file. Returns `None` -- "not configured" to every
    caller (dd-tracker.md:262-268) -- for a missing file, an unreadable or
    malformed one, one missing a required key, or one that is group- or
    world-readable: the same refusal staged evidence already gets
    (bootstrap/chat-reply:71-74)."""
    path = pathlib.Path(path)
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            return None
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or any(key not in data for key in REQUIRED):
        return None
    statuses = data.get("statuses", {})
    if not isinstance(statuses, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in statuses.items()):
        return None
    # A value of the wrong type is "not configured", never an exception: the
    # callers treat None as a no-op, and a raise here would break a send path.
    for name in ("url", "key"):
        if not isinstance(data[name], str) or not data[name].strip():
            return None
    for name in ("project", "thread_field"):
        value = data[name]
        if isinstance(value, bool) or not isinstance(value, (str, int)) \
                or (isinstance(value, str) and not value.strip()):
            return None
    return Config(data["url"], data["key"], data["project"], data["thread_field"], statuses)


class Tracker:
    """Holds a `Config` and an injected opener. `opener` defaults to
    `urllib.request.build_opener()`; every test here injects a fake instead,
    so none of them reaches a socket."""

    def __init__(self, config, opener=None):
        self.config = config
        self._opener = opener or urllib.request.build_opener()
        self._status_ids = None

    def find(self, thread):
        """Thread-keyed lookup. Returns `(canonical, newer)`: the oldest
        matching item and every other match, oldest decided here rather than
        trusted from the tracker's own ordering -- the one place oldest-wins is
        implemented, so every thread-keyed lookup in this half reaches the
        same answer (dd-tracker.md:291-296)."""
        data = self._call("GET", "/issues.json", params={
            "cf_%s" % self.config.thread_field: thread,
            "project_id": self.config.project,
            "status_id": "*",
        })
        issues = sorted(data.get("issues", []), key=lambda issue: issue["id"])
        if not issues:
            return None, []
        return issues[0], issues[1:]

    def create(self, fields):
        """Creates an item in the configured project. `fields["thread"]`
        becomes the thread-key custom field (E12's "reads cold" row,
        dd-tracker.md:434); everything else is a Redmine issue attribute,
        passed through as given."""
        payload = self._issue_payload(fields)
        payload["project_id"] = self.config.project
        body = self._call("POST", "/issues.json", payload={"issue": payload})
        return body["issue"]

    def update(self, issue_id, fields):
        """Sends only what it is given: no field `create` adds on its own is
        implied here."""
        self._call("PUT", "/issues/%s.json" % issue_id,
                   payload={"issue": self._issue_payload(fields)})

    def note(self, issue_id, text):
        """Posts a journal note. Changes no field (dd-tracker.md:230-231)."""
        self._call("PUT", "/issues/%s.json" % issue_id, payload={"issue": {"notes": text}})

    def relate(self, issue_id, other_id):
        """Posts a `relates` relation. Redmine creates relations through their
        own endpoint rather than as an issue field, so `update` cannot carry
        this (dd-tracker.md:300-301,217-221)."""
        self._call("POST", "/issues/%s/relations.json" % issue_id,
                   payload={"relation": {"issue_to_id": other_id, "relation_type": "relates"}})

    def _issue_payload(self, fields):
        payload = {}
        for key, value in fields.items():
            if key == "thread":
                payload.setdefault("custom_fields", []).append(
                    {"id": self.config.thread_field, "value": value})
            elif key == "status":
                payload["status_id"] = self._status_id(value)
            else:
                payload[key] = value
        return payload

    def _status_id(self, name):
        """Resolves a status name to its id, once per run, through Q2's
        optional override. An unresolved name raises, naming the status that
        did not resolve -- the message that makes a missing one a two-minute
        fix."""
        if self._status_ids is None:
            data = self._call("GET", "/issue_statuses.json")
            self._status_ids = {status["name"]: status["id"]
                                 for status in data.get("issue_statuses", [])}
        mapped = self.config.statuses.get(name, name)
        try:
            return self._status_ids[mapped]
        except KeyError:
            raise TrackerError("unknown tracker status: %s" % mapped) from None

    def _call(self, method, path, params=None, payload=None):
        url = self.config.url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"X-Redmine-API-Key": self.config.key}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        attempt = 0
        while True:
            attempt += 1
            try:
                with self._opener.open(request, timeout=TIMEOUT) as response:
                    body = response.read()
                    return json.loads(body) if body else {}
            except urllib.error.HTTPError as error:
                body = error.read()[:200].decode("utf-8", "replace")
                raise TrackerError("tracker returned %d: %s" % (error.code, body),
                                    code=error.code) from None
            except urllib.error.URLError:
                if method not in SAFE_METHODS:
                    raise TrackerError(
                        "tracker unreachable on a write; not retried here "
                        "(it may have been applied)") from None
                if attempt >= RETRIES:
                    raise TrackerError(
                        "tracker unreachable after %d attempts" % RETRIES) from None
