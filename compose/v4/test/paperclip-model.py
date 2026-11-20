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
            replies = {message.get("tool_call_id"): message.get("content", "")
                       for message in tail if message["role"] == "tool"}
            query = {"searches": [{"type": "lex", "query": "coppermeadow"}],
                     "collections": ["approved"], "rerank": False}
            if "qmd_query" in replies:
                delta = {"role": "assistant", "content": "QMD_RESULT: " + str(replies["qmd_query"])}
                finish = "stop"
            elif "qmd_search" in replies:
                try:
                    matches = json.loads(replies["qmd_search"])["results"][0]["matches"]
                    if not isinstance(matches, list):
                        raise ValueError("invalid tool search matches")
                except (KeyError, IndexError, TypeError, ValueError):
                    delta = {"role": "assistant", "content": "QMD_SEARCH_ERROR"}
                    finish = "stop"
                else:
                    if "mcp__qmd__query" not in matches:
                        delta = {"role": "assistant", "content": "QMD_SEARCH_NO_MATCH"}
                        finish = "stop"
                    else:
                        delta = {"role": "assistant", "tool_calls": [{
                            "index": 0, "id": "qmd_query", "type": "function",
                            "function": {"name": "tool_call", "arguments": json.dumps({
                                "calls": [{"name": "mcp__qmd__query", "arguments": query}],
                            })},
                        }]}
                        finish = "tool_calls"
            elif "mcp__qmd__query" in names or "tool_search" in names:
                direct = "mcp__qmd__query" in names
                delta = {"role": "assistant", "tool_calls": [{
                    "index": 0, "id": "qmd_query" if direct else "qmd_search", "type": "function",
                    "function": {"name": "mcp__qmd__query" if direct else "tool_search",
                                 "arguments": json.dumps(query if direct else {"queries": ["qmd query"]})},
                }]}
                finish = "tool_calls"
            else:
                delta = {"role": "assistant", "content": "QMD_TOOL_UNAVAILABLE"}
                finish = "stop"
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


HTTPServer(("127.0.0.1", 8765), Model).serve_forever()
