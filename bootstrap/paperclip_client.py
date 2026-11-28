"""Paperclip run-scoped REST and tool gateway client."""

import hashlib
import ipaddress
import json
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


class ProtocolError(RuntimeError):
    """The native response did not establish authority or a known outcome."""


class PermissionDenied(ProtocolError):
    """The native API explicitly denied the actor."""


class DocumentNotFound(ProtocolError):
    """The document GET returned a definite not-found response."""


class DocumentConflict(ProtocolError):
    """The document PUT returned a definite conflict response."""


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
        data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode("utf-8")
        request = Request(self._base + path, data=data, method=method, headers=headers)
        handlers = [_NoRedirect()]
        if self._base.startswith("http://"):
            handlers.append(ProxyHandler({}))
        try:
            with build_opener(*handlers).open(request, timeout=30) as response:
                if path.endswith("/content?download=1"):
                    return response.status, response.read(MAX_ATTACHMENT_BYTES + 1)
                return response.status, json.loads(response.read())
        except HTTPError as error:
            if error.code in (401, 403):
                raise PermissionDenied("Paperclip authorization denied") from None
            if error.code in (404, 409):
                return error.code, None
            raise ProtocolError("Paperclip request failed or outcome unknown") from None
        except (URLError, TimeoutError, ValueError, OSError):
            raise ProtocolError("Paperclip request failed or outcome unknown") from None

    def _request(self, method, path, body=None, expected=200, gateway=False, document_get=False,
                 gateway_token=None, multipart_boundary=None, document_put=False):
        headers = dict(self._headers)
        headers["Content-Type"] = ("multipart/form-data; boundary=" + multipart_boundary
                                   if multipart_boundary else "application/json")
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
        if document_put and status == 409:
            raise DocumentConflict("Document conflict")
        if status != expected:
            raise ProtocolError("Paperclip request failed or outcome unknown")
        return result

    def get_issue(self, issue_id):
        return self._request("GET", "/api/issues/" + _segment(issue_id))

    def list_issues(self, company_id, project_id, after_id=None, limit=100):
        company = _segment(company_id)
        _segment(project_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ProtocolError("Invalid issue page limit")
        query = {"projectId": project_id, "sortField": "id", "sortDir": "asc", "limit": limit}
        if after_id is not None:
            _segment(after_id)
            query["afterId"] = after_id
        result = self._request("GET", "/api/companies/" + company + "/issues?" + urlencode(query))
        if not isinstance(result, list):
            raise ProtocolError("Invalid issues response")
        return result

    def get_me(self):
        return self._request("GET", "/api/agents/me")

    def update_issue(self, issue_id, body):
        return self._request("PATCH", "/api/issues/" + _segment(issue_id), body)

    def checkout_issue(self, issue_id, agent_id):
        result = self._request("POST", "/api/issues/" + _segment(issue_id) + "/checkout", {
            "agentId": agent_id, "expectedStatuses": ["todo", "in_progress"],
        })
        if (not isinstance(result, dict) or result.get("id") != issue_id
                or result.get("assigneeAgentId") != agent_id or result.get("status") != "in_progress"
                or result.get("checkoutRunId") != self._headers["X-Paperclip-Run-Id"]):
            raise ProtocolError("Native checkout was not established for this run")
        return result

    def list_interactions(self, issue_id):
        result = self._request("GET", "/api/issues/" + _segment(issue_id) + "/interactions")
        if not isinstance(result, list):
            raise ProtocolError("Invalid native interaction listing")
        return result

    def create_interaction(self, issue_id, body):
        return self._request("POST", "/api/issues/" + _segment(issue_id) + "/interactions", body, expected=201)

    def get_run(self, run_id):
        return self._request("GET", "/api/heartbeat-runs/" + _segment(run_id))

    def resolve_context(self, company_id, agent_id, task_id=None):
        _segment(company_id)
        _segment(agent_id)
        if task_id is not None:
            _segment(task_id)
        run_id = self._headers["X-Paperclip-Run-Id"]
        run = self.get_run(run_id)
        if not isinstance(run, dict):
            raise ProtocolError("Missing run provenance")
        context = run.get("contextSnapshot")
        if context is None:
            context = {}
        if not isinstance(context, dict):
            raise ProtocolError("Invalid run context")
        binding = next((item for item in (run.get("nativeIssueId"), run.get("issueId"),
                                          context.get("issueId"), context.get("taskId"))
                        if isinstance(item, str) and item.strip()), None)
        if (run.get("id") != run_id or run.get("companyId") != company_id
                or run.get("agentId") != agent_id or run.get("status") != "running"
                or binding is None or (task_id is not None and binding != task_id)):
            raise ProtocolError("Run provenance mismatch")
        return {"companyId": company_id, "agentId": agent_id, "runId": run_id, "taskId": binding}

    def live_runs(self, issue_id):
        result = self._request("GET", "/api/issues/" + _segment(issue_id) + "/live-runs")
        if not isinstance(result, list):
            raise ProtocolError("Invalid live runs response")
        return result

    def list_attachments(self, issue_id):
        result = self._request("GET", "/api/issues/" + _segment(issue_id) + "/attachments")
        if not isinstance(result, list):
            raise ProtocolError("Invalid attachments response")
        return result

    def upload_attachment(self, company_id, issue_id, filename, data):
        path = "/api/companies/{}/issues/{}/attachments".format(
            _segment(company_id), _segment(issue_id))
        if (not isinstance(filename, str) or not filename.strip()
                or any(ord(char) < 32 or ord(char) > 126 or char in '"/\\'
                       for char in filename)
                or not isinstance(data, bytes) or not 0 < len(data) <= MAX_ATTACHMENT_BYTES):
            raise ProtocolError("Invalid attachment")
        boundary = uuid.uuid4().hex
        while boundary.encode("ascii") in data:
            boundary = uuid.uuid4().hex
        body = (b"--" + boundary.encode("ascii") + b'\r\nContent-Disposition: form-data; '
                + b'name="file"; filename="' + filename.encode("ascii")
                + b'"\r\nContent-Type: application/octet-stream\r\n\r\n' + data
                + b"\r\n--" + boundary.encode("ascii") + b"--\r\n")
        result = self._request("POST", path, body, expected=201, multipart_boundary=boundary)
        if (not isinstance(result, dict) or not isinstance(result.get("id"), str)
                or not result["id"].strip() or result.get("companyId") != company_id
                or result.get("issueId") != issue_id
                or result.get("originatingRunId") != self._headers["X-Paperclip-Run-Id"]
                or result.get("sha256") != hashlib.sha256(data).hexdigest()
                or type(result.get("byteSize")) is not int or result["byteSize"] != len(data)):
            raise ProtocolError("Attachment acknowledgement is not authoritative")
        return result

    def download_attachment(self, attachment_id):
        path = "/api/attachments/" + _segment(attachment_id) + "/content?download=1"
        result = self._request("GET", path)
        if not isinstance(result, bytes) or len(result) > MAX_ATTACHMENT_BYTES:
            raise ProtocolError("Invalid attachment content")
        return result

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

    def list_documents(self, issue_id):
        result = self._request("GET", "/api/issues/" + _segment(issue_id) + "/documents")
        if not isinstance(result, list):
            raise ProtocolError("Invalid document listing")
        return result

    def create_document(self, issue_id, key, body):
        path = "/api/issues/{}/documents/{}".format(_segment(issue_id), _segment(key))
        result = self._request("PUT", path, {
            "title": key, "format": "markdown", "body": body, "baseRevisionId": None,
        }, expected=201, document_put=True)
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
        }, document_put=True)
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
