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


if __name__ == "__main__":
    unittest.main(verbosity=2)
