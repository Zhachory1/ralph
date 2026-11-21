"""Serve a loopback-only status page for the separate communications receivers."""
import argparse
import html
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer


def status_snapshot(environment=None):
    values = os.environ if environment is None else environment
    gmail_configured = all(values.get(key) for key in (
        "GMAIL_MAILBOX", "GMAIL_ALLOWLIST", "PAPERCLIP_ROUTINE_ID",
    ))
    chat_configured = all(values.get(key) for key in (
        "RALPH_COMMS_CHAT_SPACES", "CHAT_WEBHOOK_AUDIENCE", "PAPERCLIP_ROUTINE_ID",
    ))
    return {
        "Gmail receive": "configured; activation remains separate" if gmail_configured else "not configured",
        "Chat receive": "configured; activation remains separate" if chat_configured else "not configured",
        "Gmail mailbox": values.get("GMAIL_MAILBOX") or "not configured",
        "Gmail sender domains": values.get("GMAIL_ALLOWLIST") or "not configured",
        "Chat spaces": values.get("RALPH_COMMS_CHAT_SPACES") or "not configured",
        "Paperclip Routine": values.get("PAPERCLIP_ROUTINE_ID") or "not configured",
        "Sending identity": "separate from both receive paths",
        "Actions": "read-only; no account connection, webhook activation, or send action",
    }


def render_page(snapshot):
    rows = "".join(
        f"<tr><th scope=\"row\">{html.escape(key)}</th><td>{html.escape(value)}</td></tr>"
        for key, value in snapshot.items()
    )
    return (
        "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Ralph communications admin</title>"
        "<style>body{font:16px system-ui;max-width:52rem;margin:4rem auto;padding:0 1rem;color:#17212b}"
        "h1{font-size:1.8rem}table{border-collapse:collapse;width:100%}th,td{text-align:left;"
        "padding:.8rem;border-bottom:1px solid #ccd4dc}th{width:35%}small{color:#52616d}</style>"
        "<body><h1>Communications admin</h1><p>Receive-path configuration and status.</p>"
        f"<table><tbody>{rows}</tbody></table>"
        "<p><small>Configure receive routes through the approved host setup. This page cannot connect accounts,"
        " activate webhooks, change routing, or send messages.</small></p></body></html>"
    )


def make_handler(environment=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/healthz":
                body = json.dumps({"status": "ok"}).encode()
                content_type = "application/json"
            elif self.path == "/":
                body = render_page(status_snapshot(environment)).encode()
                content_type = "text/html; charset=utf-8"
            else:
                body = b"{}"
                content_type = "application/json"
                self.send_response(404)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.send_response(405)
            self.send_header("Allow", "GET")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, _format, *_args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8789)
    arguments = parser.parse_args()
    if not 1 <= arguments.port <= 65535:
        parser.error("Use a valid local port")
    HTTPServer(("0.0.0.0", arguments.port), make_handler()).serve_forever()


if __name__ == "__main__":
    main()
