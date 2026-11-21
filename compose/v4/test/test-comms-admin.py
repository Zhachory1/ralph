"""Test the local communications status page without starting a receiver."""
import importlib.util
import http.client
import threading
import unittest
from pathlib import Path
import yaml

path = Path(__file__).resolve().parents[1] / "comms-admin.py"
spec = importlib.util.spec_from_file_location("comms_admin", path)
admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admin)


class CommsAdminTest(unittest.TestCase):
    def test_status_page_shows_configuration_without_secrets_or_activation_controls(self):
        values = {
            "GMAIL_MAILBOX": "intake@example.com",
            "GMAIL_ALLOWLIST": "example.com",
            "PAPERCLIP_ROUTINE_ID": "routine-id",
            "RALPH_COMMS_CHAT_SPACES": "spaces/example",
            "CHAT_WEBHOOK_AUDIENCE": "https://example.invalid/chat",
            "PAPERCLIP_API_KEY": "never-render-this-token",
        }
        page = admin.render_page(admin.status_snapshot(values))
        self.assertIn("Gmail", page)
        self.assertIn("Chat", page)
        self.assertIn("read-only", page)
        self.assertIn("intake@example.com", page)
        self.assertIn("spaces/example", page)
        self.assertNotIn("never-render-this-token", page)
        self.assertNotIn("<form", page)

    def test_status_page_escapes_configuration_values(self):
        page = admin.render_page({
            "mailbox": "<script>alert(1)</script>",
        })
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_http_panel_is_read_only_and_has_a_health_route(self):
        server = admin.HTTPServer(("127.0.0.1", 0), admin.make_handler({}))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            connection.request("GET", "/healthz")
            self.assertEqual(connection.getresponse().status, 200)
            connection.request("POST", "/")
            response = connection.getresponse()
            self.assertEqual(response.status, 405)
            self.assertEqual(response.getheader("Allow"), "GET")
            connection.close()
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()

    def test_compose_keeps_receive_credentials_separate_from_chat_sender(self):
        compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
        services = yaml.safe_load(compose_path.read_text(encoding="utf-8"))["services"]
        chat_sender = services["chat-sender"]
        chat_intake = services["chat-intake"]
        gmail_intake = services["gmail-intake"]
        admin_service = services["comms-admin"]

        self.assertTrue(any(volume.startswith("chat-creds:") for volume in chat_sender["volumes"]))
        self.assertFalse(any("chat-creds:" in volume for volume in chat_intake["volumes"]))
        self.assertTrue(any("gmail-intake-creds:" in volume for volume in gmail_intake["volumes"]))
        self.assertFalse(any("chat-creds:" in volume for volume in gmail_intake["volumes"]))
        self.assertFalse(any("creds" in volume for volume in admin_service["volumes"]))
        self.assertEqual(chat_intake["profiles"], ["chat-receive"])
        self.assertEqual(gmail_intake["profiles"], ["gmail-receive"])
        self.assertTrue(services["chat-intake"]["ports"][0].startswith("127.0.0.1:"))


if __name__ == "__main__":
    unittest.main()
