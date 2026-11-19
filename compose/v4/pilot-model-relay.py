"""One-run OpenRouter relay. The upstream key stays in this process."""

import http.client
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

UPSTREAM = "openrouter.ai"
CHAT_PATH = "/v1/chat/completions"
MAX_REQUEST = 256 * 1024
MAX_RESPONSE = 2 * 1024 * 1024
MAX_CALLS = 12
MAX_OUTPUT_TOKENS = 2048
TIMEOUT = 45
FORWARDED_FIELDS = ("messages", "tools", "tool_choice", "stream", "stream_options")


def relay(key, model, *, client_model=None, connect=None):
    remaining = MAX_CALLS
    client_model = client_model or model

    class Handler(BaseHTTPRequestHandler):
        timeout = 10

        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == "/v1/models":
                self.reply(200, {"object": "list", "data": [{"id": client_model, "object": "model"}]})
            elif self.path == f"/v1/models/{client_model}":
                self.reply(200, {"id": client_model, "object": "model"})
            else:
                self.reply(404, {"error": "Route not available"})

        def reply(self, status, body):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_POST(self):
            nonlocal remaining
            self.connection.settimeout(5)
            if self.path != CHAT_PATH:
                self.reply(404, {"error": "Route not available"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_REQUEST or self.headers.get("Transfer-Encoding"):
                    raise ValueError
                request = json.loads(self.rfile.read(size))
                if (not isinstance(request, dict) or request.get("model") != client_model
                        or not isinstance(request.get("messages"), list)
                        or not isinstance(request.get("stream", False), bool)):
                    raise ValueError
            except (ValueError, OSError):
                self.reply(400, {"error": "Invalid relay request"})
                return
            if remaining <= 0:
                self.reply(429, {"error": "Pilot request limit reached"})
                return
            remaining -= 1
            payload = {field: request[field] for field in FORWARDED_FIELDS if field in request}
            payload.update(model=model, max_tokens=MAX_OUTPUT_TOKENS)
            upstream = connect() if connect else http.client.HTTPSConnection(UPSTREAM, timeout=TIMEOUT)
            try:
                upstream.request("POST", f"/api{CHAT_PATH}", json.dumps(payload), {
                    "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                })
                response = upstream.getresponse()
                if response.status != 200:
                    self.reply(502, {"error": f"Provider rejected the request ({response.status})"})
                    return
                streaming = request.get("stream") is True
                content_type = "text/event-stream" if streaming else "application/json"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.end_headers()
                total = 0
                while chunk := response.read1(8192):
                    total += len(chunk)
                    if total > MAX_RESPONSE:
                        raise OSError("Response limit reached")
                    self.wfile.write(chunk)
            except (http.client.HTTPException, OSError):
                # Never forward upstream headers, errors, or exception details.
                self.close_connection = True
            finally:
                upstream.close()

    return Handler


if __name__ == "__main__":
    key = sys.stdin.readline().strip()
    if not key:
        raise SystemExit("Provide the upstream key on stdin")
    model = os.environ["RALPH_PILOT_MODEL"]
    with HTTPServer(("0.0.0.0", 8765), relay(key, model, client_model="ralph-pilot")) as server:
        server.serve_forever()
