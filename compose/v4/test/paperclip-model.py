"""Scripted chat-completions provider for the offline Hermes and OpenCode canary."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

DISPOSITION = r'''
pc() {
    curl -fsS --max-time 5 -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
        -H "X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID" "$@"
}
issue="${PAPERCLIP_API_URL%/}/api/issues/$PAPERCLIP_TASK_ID"
pc --json "$(jq -n --arg agent "$PAPERCLIP_AGENT_ID" \
    '{agentId:$agent,expectedStatuses:["todo","in_review","in_progress"]}')" "$issue/checkout" >/dev/null
'''
REQUEST_REVIEW = r'''
review=$(pc --json '{"kind":"request_confirmation","idempotencyKey":"canary-review","resolverPolicy":"human_only","payload":{"version":1,"prompt":"Review the first canary result."}}' "$issue/interactions" | jq -er .id)
pc -X PATCH --json "$(jq -n --arg review "$review" \
    '{status:"in_review",reviewInteractionId:$review,comment:"First fixture check passed."}')" "$issue" >/dev/null
'''
FINISH = r'''
pc -X PATCH --json '{"status":"done","comment":"Follow-up fixture check passed."}' "$issue" >/dev/null
'''

QMD_QUERY_TOOL = "mcp__qmd__query"
QMD_FIXTURE_TERM = "coppermeadow"
QMD_FIXTURE_FILE = "approved/1-projects/fixture/rfc.md"


def qmd_retrieval_failure(content):
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return "invalid_json"
    if not isinstance(payload, dict):
        return "invalid_envelope"
    if "error" in payload:
        return "tool_error"
    structured = payload.get("structuredContent")
    if not isinstance(structured, dict):
        result = payload.get("result")
        return "text_result" if isinstance(result, str) else "object_result" if isinstance(result, dict) else "missing_structure"
    hits = structured.get("results")
    if not isinstance(hits, list) or len(hits) != 1:
        return "unexpected_hit_count"
    if not isinstance(hits[0], dict) or hits[0].get("file") != QMD_FIXTURE_FILE:
        return "unexpected_hit_file"
    snippet = hits[0].get("snippet")
    if not isinstance(snippet, str) or QMD_FIXTURE_TERM not in snippet:
        return "missing_hit_term"
    return None


def qmd_turn(names, tail):
    if "terminal" not in names or names.intersection({"tool_search", "tool_describe", "tool_call"}):
        return {"role": "assistant", "content": "QMD_FIXTURE_TOOL_SURFACE_ERROR"}, "stop"
    if QMD_QUERY_TOOL not in names:
        if any(name.startswith("mcp__qmd__") for name in names):
            return {"role": "assistant", "content": "QMD_FIXTURE_TOOL_SURFACE_ERROR"}, "stop"
        return {"role": "assistant", "content": "QMD_TOOL_NOT_ADVERTISED"}, "stop"
    for message in tail:
        if message["role"] == "tool" and message.get("tool_call_id") == "qmd_query":
            content = message.get("content")
            failure = qmd_retrieval_failure(content)
            if failure:
                return {"role": "assistant", "content": f"QMD_RETRIEVAL_FAILED:{failure}"}, "stop"
            return {"role": "assistant", "content": "QMD_RESULT: " + content}, "stop"
    query = {"searches": [{"type": "lex", "query": QMD_FIXTURE_TERM}],
             "collections": ["approved"], "rerank": False}
    return {"role": "assistant", "tool_calls": [{
        "index": 0, "id": "qmd_query", "type": "function",
        "function": {"name": QMD_QUERY_TOOL, "arguments": json.dumps(query)},
    }]}, "tool_calls"


class Model(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        names = {tool["function"]["name"] for tool in request.get("tools", [])}
        messages = request["messages"]
        last_user = next((i for i in reversed(range(len(messages))) if messages[i]["role"] == "user"), -1)
        tail = messages[last_user + 1:]
        prompt = str(messages[last_user]["content"])
        followup = "CANARY_FOLLOWUP" in prompt
        if "QMD_CANARY" in prompt:
            delta, finish = qmd_turn(names, tail)
        elif ("terminal" in names and "CANARY_DELEGATE" in prompt
                and not any(message["role"] == "tool" for message in tail)):
            args = json.dumps({"command": "python3 /paperclip/delegate.py"})
            delta = {"role": "assistant", "tool_calls": [{
                "index": 0, "id": "call_delegate", "type": "function",
                "function": {"name": "terminal", "arguments": args},
            }]}
            finish = "tool_calls"
        elif ("bash" in names and (followup or "CANARY_FIRST" in prompt)
                and not any(message["role"] == "tool" for message in tail)):
            command = (
                "test \"$(cat result.txt)\" = first && printf 'second\\n' > result.txt && sh check.sh second"
                if followup else
                "test ! -e result.txt && printf 'first\\n' > result.txt && sh check.sh first"
            )
            script = f"set -e\n{command}\n{DISPOSITION}\n{FINISH if followup else REQUEST_REVIEW}"
            args = json.dumps({"command": script, "description": "Run and record the fixture check"})
            delta = {"role": "assistant", "tool_calls": [{
                "index": 0, "id": f"call_fixture_{last_user}", "type": "function",
                "function": {"name": "bash", "arguments": args},
            }]}
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": "CANARY_RESULT" if tail else "hello"}
            finish = "stop"
        self.send_response(200)
        if not request.get("stream"):
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "id": "chatcmpl-fixture", "object": "chat.completion", "created": 1,
                "model": request["model"], "choices": [{"index": 0, "message": delta, "finish_reason": finish}],
            }).encode())
            return
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for content, reason in [(delta, None), ({}, finish)]:
            chunk = {
                "id": "chatcmpl-fixture", "object": "chat.completion.chunk", "created": 1,
                "model": request["model"], "choices": [{"index": 0, "delta": content, "finish_reason": reason}],
            }
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8765), Model).serve_forever()
