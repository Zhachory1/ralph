"""Paperclip run-scoped REST and tool gateway client."""

import ipaddress
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class ProtocolError(RuntimeError):
    """The native response did not establish authority or a known outcome."""


class PermissionDenied(ProtocolError):
    """The native API explicitly denied the actor."""


class DocumentNotFound(ProtocolError):
    """The document GET returned a definite not-found response."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _segment(value):
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError("Missing API path identifier")
    return quote(value, safe="")


class PaperclipClient:
    def __init__(self, api_url, api_key, run_id, transport=None):
        parsed = urlsplit(api_url)
        if (parsed.scheme not in ("http", "https") or not parsed.netloc
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ProtocolError("Invalid Paperclip API URL")
        if parsed.scheme == "http" and parsed.hostname != "localhost":
            try:
                is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                is_loopback = False
            if not is_loopback:
                raise ProtocolError("Paperclip API requires HTTPS outside loopback")
        if not isinstance(api_key, str) or not api_key or not isinstance(run_id, str) or not run_id:
            raise ProtocolError("Missing run credentials")
        path = parsed.path.rstrip("/")
        if path.endswith("/api"):
            path = path[:-4]
        self._base = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
        self._headers = {"Authorization": "Bearer " + api_key, "X-Paperclip-Run-Id": run_id}
        self._transport = transport or self._http
        self._gateway_token = None
        self._tools = None

    def _http(self, method, path, headers, body):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(self._base + path, data=data, method=method, headers=headers)
        handlers = [_NoRedirect()]
        if self._base.startswith("http://"):
            handlers.append(ProxyHandler({}))
        try:
            with build_opener(*handlers).open(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            if error.code in (401, 403):
                raise PermissionDenied("Paperclip authorization denied") from None
            if error.code == 404:
                return 404, None
            raise ProtocolError("Paperclip request failed or outcome unknown") from None
        except (URLError, TimeoutError, ValueError, OSError):
            raise ProtocolError("Paperclip request failed or outcome unknown") from None

    def _request(self, method, path, body=None, expected=200, gateway=False, document_get=False,
                 gateway_token=None):
        headers = dict(self._headers)
        headers["Content-Type"] = "application/json"
        if gateway:
            token = gateway_token if gateway_token is not None else self._gateway_token
            if not token:
                raise ProtocolError("Gateway session not open")
            headers["X-Paperclip-Tool-Gateway-Token"] = token
        status, result = self._transport(method, path, headers, body)
        if status in (401, 403):
            raise PermissionDenied("Paperclip authorization denied")
        if document_get and status == 404:
            raise DocumentNotFound("Document not found")
        if status != expected:
            raise ProtocolError("Paperclip request failed or outcome unknown")
        return result

    def get_issue(self, issue_id):
        return self._request("GET", "/api/issues/" + _segment(issue_id))

    def get_me(self):
        return self._request("GET", "/api/agents/me")

    def update_issue(self, issue_id, body):
        return self._request("PATCH", "/api/issues/" + _segment(issue_id), body)

    def get_run(self, run_id):
        return self._request("GET", "/api/heartbeat-runs/" + _segment(run_id))

    def get_revision(self, issue_id, key, revision_id):
        path = "/api/issues/{}/documents/{}/revisions/{}".format(
            _segment(issue_id), _segment(key), _segment(revision_id))
        return self._request("GET", path)

    def get_decisions(self, company_id, run_id):
        path = "/api/companies/{}/tools/runs/{}/decisions".format(
            _segment(company_id), _segment(run_id))
        return self._request("GET", path)

    def create_issue(self, company_id, body):
        return self._request("POST", "/api/companies/" + _segment(company_id) + "/issues",
                             body, expected=201)

    def get_document(self, issue_id, key):
        path = "/api/issues/{}/documents/{}".format(_segment(issue_id), _segment(key))
        result = self._request("GET", path, document_get=True)
        if (not isinstance(result, dict) or result.get("key") != key
                or not isinstance(result.get("body"), str)
                or not isinstance(result.get("latestRevisionId"), str)
                or not result["latestRevisionId"]):
            raise ProtocolError("Document response is incomplete")
        return result

    def create_document(self, issue_id, key, body):
        path = "/api/issues/{}/documents/{}".format(_segment(issue_id), _segment(key))
        result = self._request("PUT", path, {
            "title": key, "format": "markdown", "body": body, "baseRevisionId": None,
        }, expected=201)
        if (not isinstance(result, dict) or result.get("key") != key or
                result.get("body") != body or not isinstance(result.get("latestRevisionId"), str)
                or not result["latestRevisionId"]):
            raise ProtocolError("Document creation acknowledgement is not authoritative")
        return result

    def update_document(self, issue_id, key, body, base_revision_id):
        if not isinstance(base_revision_id, str) or not base_revision_id.strip():
            raise ProtocolError("Missing document base revision")
        path = "/api/issues/{}/documents/{}".format(_segment(issue_id), _segment(key))
        result = self._request("PUT", path, {
            "title": key, "format": "markdown", "body": body, "baseRevisionId": base_revision_id,
        })
        if (not isinstance(result, dict) or result.get("key") != key or result.get("body") != body
                or not isinstance(result.get("latestRevisionId"), str)
                or not result["latestRevisionId"] or result["latestRevisionId"] == base_revision_id):
            raise ProtocolError("Document update acknowledgement is not authoritative")
        return result

    def open_gateway(self, issue_id, project_id):
        self._gateway_token, self._tools = None, None
        result = self._request("POST", "/api/tool-gateway/sessions",
                               {"issueId": issue_id, "projectId": project_id}, expected=201)
        if not isinstance(result, dict) or not all(
                isinstance(result.get(key), str) and result[key].strip()
                for key in ("sessionId", "token", "expiresAt", "toolsUrl", "callUrl")):
            raise ProtocolError("Invalid gateway session")
        tools = self._request("GET", "/api/tool-gateway/tools", gateway=True,
                              gateway_token=result["token"])
        if not isinstance(tools, list):
            raise ProtocolError("Invalid gateway catalog")
        self._gateway_token, self._tools = result["token"], tools

    def tool_name(self, connection_id, upstream_name):
        if not isinstance(self._tools, list):
            raise ProtocolError("Gateway catalog unavailable")
        matches = [item for item in self._tools if isinstance(item, dict)
                   and item.get("connectionId") == connection_id
                   and item.get("upstreamToolName") == upstream_name]
        if len(matches) != 1 or not isinstance(matches[0].get("name"), str):
            raise ProtocolError("Gateway tool not uniquely configured")
        return matches[0]["name"]

    def call_tool(self, connection_id, upstream_name, parameters, idempotency_key):
        alias = self.tool_name(connection_id, upstream_name)
        if not isinstance(parameters, dict) or not isinstance(idempotency_key, str) or not idempotency_key:
            raise ProtocolError("Invalid gateway invocation")
        result = self._request("POST", "/api/tool-gateway/tools/call", {
            "tool": alias, "parameters": parameters, "idempotencyKey": idempotency_key,
        }, gateway=True)
        if (not isinstance(result, dict) or result.get("status") != "completed"
                or result.get("tool") != alias or not isinstance(result.get("invocationId"), str)
                or not result["invocationId"].strip()):
            raise ProtocolError("Gateway result is unresolved")
        envelope = result.get("result")
        if not isinstance(envelope, dict) or envelope.get("error"):
            raise ProtocolError("Gateway tool returned an error")
        data = envelope.get("data")
        if (not isinstance(data, dict) or data.get("isError") is not False
                or data.get("transport") != "mcp_http" or data.get("spawnedLocalProcess") is not False):
            raise ProtocolError("Invalid gateway result envelope")
        blocks = data.get("content")
        if (not isinstance(blocks, list) or any(
                not isinstance(block, dict) or block.get("type") != "text"
                or not isinstance(block.get("text"), str) for block in blocks)):
            raise ProtocolError("Unsupported gateway content")
        content = data.get("structuredContent")
        if not isinstance(content, (dict, list)):
            if content is not None or len(blocks) != 1 or blocks[0]["text"] != envelope.get("content"):
                raise ProtocolError("Invalid gateway result content")
            try:
                content = json.loads(envelope["content"])
            except (KeyError, TypeError, ValueError):
                raise ProtocolError("Invalid gateway result content") from None
        if not isinstance(content, (dict, list)):
            raise ProtocolError("Invalid gateway result content")
        return result["invocationId"], content
