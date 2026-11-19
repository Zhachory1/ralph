"""Exercise the one-run relay over HTTP without a provider account."""

import http.client
import io
import json
import runpy
import threading
import unittest
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch

MODULE = runpy.run_path(str(Path(__file__).resolve().parents[1] / "pilot-model-relay.py"))
relay = MODULE["relay"]


class RelayTest(unittest.TestCase):
    def test_replacement_relay_uses_only_the_new_key_after_provider_rotation(self):
        valid_key = "dummy-old-key"
        seen = []

        class Upstream:
            def request(self, _method, _path, _body, headers):
                seen.append(headers["Authorization"])

            def getresponse(self):
                response = io.BytesIO(b"{}")
                response.status = 200 if seen[-1] == f"Bearer {valid_key}" else 401
                return response

            def close(self):
                pass

        def request_once(key):
            with HTTPServer(("127.0.0.1", 0), relay(
                key, "test/model", client_model="ralph-pilot", connect=Upstream,
            )) as server:
                server.timeout = 5
                worker = threading.Thread(target=server.handle_request)
                worker.start()
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                try:
                    conn.request("POST", "/v1/chat/completions",
                                 json.dumps({"model": "ralph-pilot", "messages": []}))
                    response = conn.getresponse()
                    self.assertNotIn(key.encode(), response.read())
                    return response.status
                finally:
                    conn.close()
                    worker.join()

        self.assertEqual(request_once("dummy-old-key"), 200)
        valid_key = "dummy-replacement-key"
        self.assertEqual(request_once("dummy-old-key"), 502)
        self.assertEqual(request_once("dummy-replacement-key"), 200)
        self.assertEqual(seen, ["Bearer dummy-old-key", "Bearer dummy-old-key", "Bearer dummy-replacement-key"])

    def test_upstream_key_is_not_exposed_and_calls_are_limited(self):
        key = "dummy-upstream-key"
        model = "test/model"
        calls = []

        class Response(io.BytesIO):
            status = 200

        class Upstream:
            def request(self, method, path, body, headers):
                calls.append((method, path, json.loads(body), headers))

            def getresponse(self):
                if len(calls) == 1:
                    return Response(b"data: [DONE]\n\n")
                response = Response(key.encode())
                response.status = 403
                return response

            def close(self):
                pass

        with patch.dict(relay.__globals__, MAX_CALLS=2):
            with HTTPServer(("127.0.0.1", 0),
                            relay(key, model, client_model="ralph-pilot", connect=Upstream)) as server:
                thread = threading.Thread(target=server.serve_forever)
                thread.start()

                def request(method, path, body=None):
                    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    try:
                        conn.request(method, path, None if body is None else json.dumps(body),
                                     {"Authorization": "Bearer client-placeholder"})
                        result = conn.getresponse()
                        data = result.read()
                        self.assertNotIn(key.encode(), data)
                        self.assertNotIn(key, str(result.headers))
                        return result.status, data
                    finally:
                        conn.close()

                try:
                    path = "/v1/chat/completions"
                    payload = {"model": "ralph-pilot", "messages": [], "stream": True,
                               "max_tokens": 99999, "provider": {"allow_fallbacks": True}}
                    status, data = request("GET", "/v1/models")
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(data)["data"][0]["id"], "ralph-pilot")
                    self.assertEqual(request("GET", "/api/v1/key")[0], 404)
                    self.assertEqual(request("POST", path, {**payload, "model": model})[0], 400)
                    self.assertEqual(calls, [])
                    self.assertEqual(request("POST", path, payload), (200, b"data: [DONE]\n\n"))
                    self.assertEqual(request("POST", path, payload)[0], 502)
                    self.assertEqual(request("POST", path, payload)[0], 429)
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(calls[0], ("POST", f"/api{path}", {
                        "model": model, "messages": [], "stream": True, "max_tokens": 2048,
                    }, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}))
                finally:
                    server.shutdown()
                    thread.join()


if __name__ == "__main__":
    unittest.main()
