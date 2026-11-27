"""Exercise the Paperclip API and gateway boundaries without network access."""

import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from bootstrap.paperclip_client import DocumentNotFound, PaperclipClient, ProtocolError


class RecordingTransport:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, method, path, headers, body):
        self.calls.append((method, path, headers, body))
        return self.replies.pop(0)


class PaperclipClientTest(unittest.TestCase):
    def client(self, transport):
        return PaperclipClient("https://example.invalid", "placeholder", "run-1", transport)

    def test_auth_denial_is_distinct_from_unknown_write_outcome(self):
        from bootstrap.paperclip_client import PermissionDenied
        client = self.client(RecordingTransport([(403, {})]))
        with self.assertRaises(PermissionDenied):
            client.create_issue("company", {"title": "Review"})

    def test_native_actor_and_issue_patch_use_run_context(self):
        transport = RecordingTransport([(200, {"id": "agent"}), (200, {"id": "task"})])
        client = self.client(transport)
        self.assertEqual(client.get_me(), {"id": "agent"})
        self.assertEqual(client.update_issue("task", {"blockedByIssueIds": ["review"]}),
                         {"id": "task"})
        self.assertEqual(transport.calls[0][:2], ("GET", "/api/agents/me"))
        self.assertEqual(transport.calls[1][:2], ("PATCH", "/api/issues/task"))

    def test_native_write_includes_bearer_and_run_headers(self):
        transport = RecordingTransport([(201, {"id": "issue-1"})])
        client = self.client(transport)
        client.create_issue("company-1", {"title": "Review"})
        method, path, headers, body = transport.calls[0]
        self.assertEqual((method, path), ("POST", "/api/companies/company-1/issues"))
        self.assertEqual(headers["Authorization"], "Bearer placeholder")
        self.assertEqual(headers["X-Paperclip-Run-Id"], "run-1")
        self.assertEqual(body, {"title": "Review"})

    def test_gateway_resolves_alias_by_connection_and_upstream_name(self):
        transport = RecordingTransport([
            (201, {"sessionId": "session-1", "token": "placeholder", "expiresAt": "later",
                   "toolsUrl": "/unused", "callUrl": "/unused"}),
            (200, [
                {"name": "wrong", "connectionId": "other", "upstreamToolName": "get_me"},
                {"name": "right", "connectionId": "connection-1", "upstreamToolName": "get_me"},
            ]),
            (200, {"invocationId": "inv-1", "status": "completed", "tool": "right",
                   "result": {"content": "{}", "data": {"structuredContent": {"id": 7},
                              "content": [], "isError": False, "transport": "mcp_http",
                              "spawnedLocalProcess": False}}}),
        ])
        client = self.client(transport)
        client.open_gateway("task-1", "project-1")
        result = client.call_tool("connection-1", "get_me", {}, "stable-key")
        self.assertEqual(result, ("inv-1", {"id": 7}))
        self.assertEqual(transport.calls[2][3],
                         {"tool": "right", "parameters": {}, "idempotencyKey": "stable-key"})
        self.assertEqual(transport.calls[2][2]["X-Paperclip-Tool-Gateway-Token"], "placeholder")

    def test_gateway_failed_reopen_discards_old_session(self):
        session = {"sessionId": "s", "token": "placeholder", "expiresAt": "later",
                   "toolsUrl": "/unused", "callUrl": "/unused"}
        catalog = [{"name": "right", "connectionId": "c", "upstreamToolName": "read"}]
        transport = RecordingTransport([
            (201, session), (200, catalog), (500, {}),
            (200, {"invocationId": "i", "status": "completed", "tool": "right",
                   "result": {"content": "{}", "data": {"structuredContent": {}, "content": [],
                              "isError": False, "transport": "mcp_http",
                              "spawnedLocalProcess": False}}}),
        ])
        client = self.client(transport)
        client.open_gateway("task", "project")
        with self.assertRaises(ProtocolError):
            client.open_gateway("other-task", "project")
        with self.assertRaises(ProtocolError):
            client.call_tool("c", "read", {}, "key")
        self.assertEqual(len(transport.calls), 3)

    def test_gateway_failed_catalog_reopen_discards_both_sessions(self):
        session = {"sessionId": "s", "token": "placeholder", "expiresAt": "later",
                   "toolsUrl": "/unused", "callUrl": "/unused"}
        new_session = dict(session, sessionId="new", token="other-placeholder")
        catalog = [{"name": "right", "connectionId": "c", "upstreamToolName": "read"}]
        transport = RecordingTransport([
            (201, session), (200, catalog), (201, new_session), (500, {}),
            (200, {"invocationId": "i", "status": "completed", "tool": "right",
                   "result": {"content": "{}", "data": {"structuredContent": {}, "content": [],
                              "isError": False, "transport": "mcp_http",
                              "spawnedLocalProcess": False}}}),
        ])
        client = self.client(transport)
        client.open_gateway("task", "project")
        with self.assertRaises(ProtocolError):
            client.open_gateway("other-task", "project")
        with self.assertRaises(ProtocolError):
            client.call_tool("c", "read", {}, "key")
        self.assertEqual(len(transport.calls), 4)

    def test_gateway_rejects_invalid_invocation_id_and_tool(self):
        for invocation_id, tool in ((None, "right"), (" ", "right"), (7, "right"),
                                    ("invocation", None), ("invocation", "wrong")):
            with self.subTest(invocation_id=invocation_id, tool=tool):
                transport = RecordingTransport([
                    (201, {"sessionId": "s", "token": "placeholder", "expiresAt": "later",
                           "toolsUrl": "/unused", "callUrl": "/unused"}),
                    (200, [{"name": "right", "connectionId": "c", "upstreamToolName": "read"}]),
                    (200, {"invocationId": invocation_id, "status": "completed", "tool": tool,
                           "result": {"content": "{}", "data": {
                               "structuredContent": {}, "content": [], "isError": False,
                               "transport": "mcp_http", "spawnedLocalProcess": False}}}),
                ])
                client = self.client(transport)
                client.open_gateway("task", "project")
                with self.assertRaises(ProtocolError):
                    client.call_tool("c", "read", {}, "key")

    def test_replayed_gateway_response_is_not_confirmation(self):
        transport = RecordingTransport([
            (201, {"sessionId": "s", "token": "placeholder", "expiresAt": "later",
                   "toolsUrl": "/unused", "callUrl": "/unused"}),
            (200, [{"name": "right", "connectionId": "c", "upstreamToolName": "write"}]),
            (200, {"invocationId": "i", "status": "replayed", "tool": "right", "result": {}}),
        ])
        client = self.client(transport)
        client.open_gateway("task", "project")
        with self.assertRaises(ProtocolError):
            client.call_tool("c", "write", {}, "key")
        self.assertEqual(len(transport.calls), 3)

    def test_gateway_rejects_non_text_fallback_content(self):
        transport = RecordingTransport([
            (201, {"sessionId": "s", "token": "placeholder", "expiresAt": "later",
                   "toolsUrl": "/unused", "callUrl": "/unused"}),
            (200, [{"name": "right", "connectionId": "c", "upstreamToolName": "read"}]),
            (200, {"invocationId": "i", "status": "completed", "tool": "right",
                   "result": {"content": "{}", "data": {"structuredContent": None,
                              "content": [{"type": "resource", "resource": {}}],
                              "isError": False, "transport": "mcp_http",
                              "spawnedLocalProcess": False}}}),
        ])
        client = self.client(transport)
        client.open_gateway("task", "project")
        with self.assertRaises(ProtocolError):
            client.call_tool("c", "read", {}, "key")

    def test_document_fork_or_missing_ack_is_rejected(self):
        body = json.dumps({"nonce": "fresh"})
        transport = RecordingTransport([
            (201, {"key": "intent-fork", "body": body, "latestRevisionId": "revision"}),
        ])
        with self.assertRaises(ProtocolError):
            self.client(transport).create_document("issue", "intent", body)

    def test_missing_document_is_distinct_from_unknown_failure(self):
        client = self.client(RecordingTransport([(404, None)]))
        with self.assertRaises(DocumentNotFound):
            client.get_document("issue", "key")
        client = self.client(RecordingTransport([(500, None)]))
        with self.assertRaises(ProtocolError) as caught:
            client.get_document("issue", "key")
        self.assertNotIsInstance(caught.exception, DocumentNotFound)

    def test_get_document_requires_matching_key_and_revision(self):
        transport = RecordingTransport([(200, {"key": "slot", "body": "free",
                                              "latestRevisionId": "rev-1"})])
        document = self.client(transport).get_document("issue", "slot")
        self.assertEqual(document["body"], "free")
        self.assertEqual(transport.calls[0][:2], ("GET", "/api/issues/issue/documents/slot"))
        transport = RecordingTransport([(200, {"key": "different", "body": "free",
                                              "latestRevisionId": "rev-1"})])
        with self.assertRaises(ProtocolError):
            self.client(transport).get_document("issue", "slot")

    def test_update_document_uses_revision_and_checks_ack(self):
        transport = RecordingTransport([(200, {"key": "slot", "body": "next",
                                              "latestRevisionId": "rev-2"})])
        self.client(transport).update_document("issue", "slot", "next", "rev-1")
        self.assertEqual(transport.calls[0][3], {
            "title": "slot", "format": "markdown", "body": "next", "baseRevisionId": "rev-1",
        })
        transport = RecordingTransport([(200, {"key": "slot-fork", "body": "next",
                                              "latestRevisionId": "rev-2"})])
        with self.assertRaises(ProtocolError):
            self.client(transport).update_document("issue", "slot", "next", "rev-1")
        self.assertEqual(len(transport.calls), 1)

    def test_document_update_conflict_is_not_retried(self):
        transport = RecordingTransport([(409, {})])
        with self.assertRaises(ProtocolError):
            self.client(transport).update_document("issue", "slot", "next", "rev-1")
        self.assertEqual(len(transport.calls), 1)

    def test_document_update_refuses_old_revision_ack(self):
        transport = RecordingTransport([(200, {"key": "slot", "body": "next",
                                              "latestRevisionId": "rev-1"})])
        with self.assertRaises(ProtocolError):
            self.client(transport).update_document("issue", "slot", "next", "rev-1")

    def test_http_normalizes_api_suffix_and_rejects_redirects(self):
        class Opener:
            def open(self, request, timeout):
                self.url = request.full_url
                raise HTTPError(self.url, 302, "redirect", {}, None)

        opener = Opener()
        with patch("bootstrap.paperclip_client.build_opener", return_value=opener) as build:
            client = PaperclipClient("https://example.invalid/api/", "placeholder", "run-1")
            with self.assertRaises(ProtocolError) as caught:
                client.get_issue("issue")
        self.assertEqual(opener.url, "https://example.invalid/api/issues/issue")
        self.assertNotIn("placeholder", str(caught.exception))
        handler = build.call_args.args[0]
        request = Request("https://example.invalid/api", headers={"Authorization": "Bearer placeholder"})
        self.assertIsNone(handler.redirect_request(request, None, 302, "", {}, "https://other.invalid/"))

    def test_http_404_document_is_safe_and_not_retried(self):
        class Opener:
            def open(self, request, timeout):
                raise HTTPError(request.full_url, 404, "private details", {}, None)

        with patch("bootstrap.paperclip_client.build_opener", return_value=Opener()):
            client = PaperclipClient("https://example.invalid", "placeholder", "run-1")
            with self.assertRaises(DocumentNotFound) as caught:
                client.get_document("issue", "key")
        self.assertNotIn("private", str(caught.exception))

    def test_api_hostname_is_not_removed_as_api_path(self):
        class Opener:
            def open(self, request, timeout):
                self.url = request.full_url
                raise HTTPError(self.url, 500, "error", {}, None)

        opener = Opener()
        with patch("bootstrap.paperclip_client.build_opener", return_value=opener):
            client = PaperclipClient("https://api", "placeholder", "run-1")
            with self.assertRaises(ProtocolError):
                client.get_issue("issue")
        self.assertEqual(opener.url, "https://api/api/issues/issue")

    def test_api_path_suffix_with_or_without_slash_is_normalized(self):
        class Opener:
            def open(self, request, timeout):
                self.url = request.full_url
                raise HTTPError(self.url, 500, "error", {}, None)

        for url in ("https://api/api", "https://api/api/"):
            with self.subTest(url=url):
                opener = Opener()
                with patch("bootstrap.paperclip_client.build_opener", return_value=opener):
                    client = PaperclipClient(url, "placeholder", "run-1")
                    with self.assertRaises(ProtocolError):
                        client.get_issue("issue")
                self.assertEqual(opener.url, "https://api/api/issues/issue")

    def test_plain_http_requires_literal_loopback_or_localhost(self):
        for url in ("http://example.invalid", "http://127.0.0.1.example.invalid", "http://192.0.2.1"):
            with self.subTest(url=url), self.assertRaises(ProtocolError):
                PaperclipClient(url, "placeholder", "run-1")

    def test_plain_http_allows_localhost_and_literal_loopback(self):
        for url in ("http://localhost", "http://127.0.0.1:8080", "http://[::1]:8080"):
            with self.subTest(url=url):
                PaperclipClient(url, "placeholder", "run-1")

    def test_loopback_http_disables_ambient_proxy_but_https_retains_it(self):
        class StoppedOpener:
            def open(self, request, timeout):
                raise HTTPError(request.full_url, 500, "stopped", {}, None)

        for url, scheme, should_use_proxy in (
                ("http://127.0.0.1:8080", "http", False),
                ("https://example.invalid", "https", True)):
            with self.subTest(url=url):
                proxies = []

                def capture_opener(*handlers):
                    opener = build_opener(*handlers)
                    proxies.extend(handler.proxies for handler in opener.handlers
                                   if isinstance(handler, ProxyHandler))
                    return StoppedOpener()

                env = {"HTTP_PROXY": "http://example.invalid:8080",
                       "HTTPS_PROXY": "http://example.invalid:8080"}
                with patch.dict(os.environ, env, clear=True):
                    with patch("bootstrap.paperclip_client.build_opener", side_effect=capture_opener):
                        client = PaperclipClient(url, "placeholder", "run-1")
                        with self.assertRaises(ProtocolError):
                            client.get_issue("issue")
                self.assertEqual(any(scheme in proxy for proxy in proxies), should_use_proxy)


if __name__ == "__main__":
    unittest.main()
