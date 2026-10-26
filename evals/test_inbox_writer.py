"""The inbox writer drafts and never sends.

No Gmail scope permits drafting without sending -- gmail.compose can send,
proven live -- so draft-only cannot be a property of the credential. It is a
property of bootstrap/inbox-writer's source instead, and these tests are what
keep it true: they fail the build if any send call ever appears there.

    python3 evals/test_inbox_writer.py
"""
import base64, email, importlib.machinery, importlib.util, pathlib, re, unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WRITER = ROOT / "bootstrap" / "inbox-writer"
PRODUCER = ROOT / "bootstrap" / "inbox-producer"

_loader = importlib.machinery.SourceFileLoader("inbox_writer", str(WRITER))
_spec = importlib.util.spec_from_loader("inbox_writer", _loader)
inbox_writer = importlib.util.module_from_spec(_spec)
_loader.exec_module(inbox_writer)


def code_only(path):
    """Source with comments and docstrings stripped, so explaining that this
    file never sends does not trip the check below."""
    import ast, io, tokenize
    src = path.read_text()
    tree = ast.parse(src)
    doc_lines = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            doc_lines.update(range(body[0].lineno, body[0].end_lineno + 1))
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT or tok.start[0] in doc_lines:
            continue
        out.append(tok.string)
    return " ".join(out)


class NeverSendsTests(unittest.TestCase):
    def test_writer_has_no_send_call(self):
        code = code_only(WRITER)
        self.assertIsNone(re.search(r"\.\s*send\s*\(", code), "inbox-writer must never call send")
        self.assertNotIn("messages ( ) . send", code)

    def test_producer_has_no_send_call(self):
        self.assertIsNone(re.search(r"\.\s*send\s*\(", code_only(PRODUCER)))

    def test_writer_only_creates_drafts(self):
        self.assertIn("drafts ( ) . create", code_only(WRITER))


class RecipientTests(unittest.TestCase):
    """Recipients come from the staged thread, never from agent output."""

    def _decode(self, draft):
        raw = base64.urlsafe_b64decode(draft["message"]["raw"])
        return email.message_from_bytes(raw)

    def test_reply_goes_to_the_threads_own_sender(self):
        staged = {"thread_id": "t1", "reply_to": "sam@example.com", "subject": "lunch?",
                  "reply_to_message_id": "<abc@x>"}
        m = self._decode(inbox_writer.build_draft(staged, "sure"))
        self.assertEqual("sam@example.com", m["To"])
        self.assertEqual("Re: lunch?", m["Subject"])
        self.assertEqual("<abc@x>", m["In-Reply-To"])

    def test_body_text_cannot_add_recipients(self):
        """A reply body containing header-looking lines stays body text."""
        staged = {"thread_id": "t1", "reply_to": "sam@example.com", "subject": "x",
                  "reply_to_message_id": ""}
        m = self._decode(inbox_writer.build_draft(staged, "Bcc: attacker@evil.com\n\nhi"))
        self.assertEqual("sam@example.com", m["To"])
        self.assertIsNone(m["Bcc"])
        self.assertIsNone(m["Cc"])

    def test_existing_re_prefix_is_not_doubled(self):
        staged = {"thread_id": "t1", "reply_to": "a@b.c", "subject": "Re: x",
                  "reply_to_message_id": ""}
        self.assertEqual("Re: x", self._decode(inbox_writer.build_draft(staged, "y"))["Subject"])


from unittest import mock


class FakeGmail:
    """Just the calls these scripts make, returning canned data."""

    def __init__(self, thread_labels=(), pages=()):
        self.thread_labels, self.pages, self.created = list(thread_labels), list(pages), []

    def users(self):
        return self

    def threads(self):
        return self

    def drafts(self):
        return self

    def get(self, userId, id, format, metadataHeaders=None):
        # thread_labels entries: (labels, in_reply_to or None)
        return mock.Mock(execute=lambda: {"messages": [
            {"labelIds": l, "payload": {"headers": [{"name": "In-Reply-To", "value": r}] if r else []}}
            for l, r in self.thread_labels]})

    def list(self, userId, q, maxResults, pageToken=None):
        page = self.pages.pop(0)
        return mock.Mock(execute=lambda: page)

    def create(self, userId, body):
        self.created.append(body)
        return mock.Mock(execute=lambda: {"id": "r-1"})


class DuplicateDraftTests(unittest.TestCase):
    def _run(self, fake):
        import json, sys, tempfile
        with tempfile.TemporaryDirectory() as d:
            ev = pathlib.Path(d) / "op1" / "evidence"
            ev.mkdir(parents=True)
            (ev / "thread.json").write_text(json.dumps(
                {"thread_id": "t1", "reply_to": "a@b.c", "subject": "x", "reply_to_message_id": "<m2@x>"}))
            show = {"task": {"status": "done", "body": "operation_id: op1\n"},
                    "runs": [{"outcome": "completed", "metadata": {"reply_body": "hi"}}]}
            with mock.patch.object(inbox_writer, "hermes",
                                   return_value=mock.Mock(returncode=0, stdout=json.dumps(show))), \
                 mock.patch.object(inbox_writer, "gmail", return_value=fake), \
                 mock.patch.object(sys, "argv", ["inbox-writer", "--task", "t_x", "--token", "/x",
                                                 "--work-root", d]):
                return inbox_writer.main()

    def test_creates_a_draft_when_the_thread_has_none(self):
        fake = FakeGmail(thread_labels=[(["INBOX", "UNREAD"], None)])
        self.assertEqual(0, self._run(fake))
        self.assertEqual(1, len(fake.created))

    def test_a_message_that_already_has_a_draft_gets_no_second(self):
        """Covers a rerun and a create whose response was lost."""
        fake = FakeGmail(thread_labels=[(["INBOX"], None), (["DRAFT"], "<m2@x>")])
        self.assertEqual(0, self._run(fake))
        self.assertEqual([], fake.created)

    def test_a_later_message_gets_a_draft_despite_an_older_one(self):
        """A draft answering an earlier message must not suppress a draft
        for the message that arrived after it."""
        fake = FakeGmail(thread_labels=[(["INBOX"], None), (["DRAFT"], "<m1@x>"), (["INBOX"], None)])
        self.assertEqual(0, self._run(fake))
        self.assertEqual(1, len(fake.created))


_ploader = importlib.machinery.SourceFileLoader("inbox_producer", str(PRODUCER))
_pspec = importlib.util.spec_from_loader("inbox_producer", _ploader)
inbox_producer = importlib.util.module_from_spec(_pspec)
_ploader.exec_module(inbox_producer)


class PaginationTests(unittest.TestCase):
    def test_every_page_of_matching_threads_is_read(self):
        import sys, tempfile
        fake = FakeGmail(pages=[{"threads": [{"id": "a"}], "nextPageToken": "p2"},
                                {"threads": [{"id": "b"}]}])
        fake.getProfile = lambda userId: mock.Mock(execute=lambda: {"emailAddress": "me@x"})
        seen = []
        fake.get = lambda userId, id, format, metadataHeaders=None: (seen.append(id), mock.Mock(execute=lambda: {
            "messages": [{"id": "m", "payload": {"headers": [{"name": "From", "value": "me@x"}]}}]}))[1]
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(inbox_producer, "gmail", return_value=fake), \
             mock.patch.object(sys, "argv", ["inbox-producer", "--token", "/x", "--work-root", d]):
            self.assertEqual(0, inbox_producer.main())
        self.assertEqual(["a", "b"], seen)


class StageOnceTests(unittest.TestCase):
    def test_writes_when_absent_and_never_overwrites(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "f.json"
            inbox_producer.stage_once(p, "first", 0o440)
            inbox_producer.stage_once(p, "second", 0o440)
            self.assertEqual("first", p.read_text())
            self.assertEqual(0o440, os.stat(p).st_mode & 0o777)
            self.assertEqual(1, os.stat(p).st_nlink)
            self.assertEqual(["f.json"], os.listdir(d))


if __name__ == "__main__":
    unittest.main(verbosity=2)
