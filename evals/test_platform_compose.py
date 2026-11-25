"""Verify the v4 Paperclip database Compose configuration."""

import json
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose" / "compose.yaml"
IMAGE = (
    "postgres:17.12@sha256:"
    "87c8b42f423b45d5247e6aa8fdb2e7762fec164c68a50fe3eb07e7bfcaf4dbcf"
)


def compose_config(password=None, port=None):
    env = os.environ.copy()
    for name in ("PAPERCLIP_DB_PASSWORD", "PAPERCLIP_DB_PORT", "COMPOSE_PROJECT_NAME"):
        env.pop(name, None)
    if password is not None:
        env["PAPERCLIP_DB_PASSWORD"] = password
    if port is not None:
        env["PAPERCLIP_DB_PORT"] = port
    return subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "-f",
            str(COMPOSE),
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


class PlatformComposeTest(unittest.TestCase):
    def test_password_is_required(self):
        result = compose_config()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PAPERCLIP_DB_PASSWORD", result.stderr)

    def test_database_is_pinned_private_and_persistent(self):
        result = compose_config(password="not-a-real-password")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        postgres = config["services"]["postgres"]
        self.assertEqual(config["name"], "ralph-v4")
        self.assertEqual(postgres["image"], IMAGE)
        self.assertEqual(len(postgres["ports"]), 1)
        self.assertEqual(postgres["ports"][0]["host_ip"], "127.0.0.1")
        self.assertEqual(postgres["ports"][0]["published"], "5432")
        self.assertEqual(postgres["ports"][0]["target"], 5432)
        self.assertEqual(len(postgres["volumes"]), 1)
        self.assertEqual(postgres["volumes"][0]["type"], "volume")
        self.assertEqual(postgres["volumes"][0]["source"], "paperclip-db")
        self.assertEqual(postgres["volumes"][0]["target"], "/var/lib/postgresql/data")
        self.assertEqual(postgres["restart"], "unless-stopped")
        self.assertEqual(
            postgres["healthcheck"]["test"],
            ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U paperclip -d paperclip"],
        )

    def test_database_port_can_be_changed(self):
        result = compose_config(password="not-a-real-password", port="15432")
        self.assertEqual(result.returncode, 0, result.stderr)
        postgres = json.loads(result.stdout)["services"]["postgres"]
        self.assertEqual(postgres["ports"][0]["published"], "15432")


if __name__ == "__main__":
    unittest.main()
