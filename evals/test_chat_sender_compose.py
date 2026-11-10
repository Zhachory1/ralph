"""The chat-sender sidecar's compose stanza, its entrypoint and the pinned
identity that must match bootstrap/chat-creds-copy (#149). No docker, no
network, no credential: the compose file is parsed as data, and the
entrypoint runs with a fake producer/sender command.

    python3 evals/test_chat_sender_compose.py
"""

import importlib.machinery
import importlib.util
import pathlib
import re
import subprocess
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "compose" / "docker-compose.yml").read_text())
DOCKERFILE = (ROOT / "compose" / "Dockerfile.notifier").read_text()
RUN_CHAT_SENDER = ROOT / "bootstrap" / "run-chat-sender"
CHAT_REPLY = ROOT / "bootstrap" / "chat-reply"
CHAT_CREDS_COPY = ROOT / "bootstrap" / "chat-creds-copy"


def load_module(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ComposeFileTests(unittest.TestCase):
    """compose/docker-compose.yml: the chat-sender and tracker-sync stanzas."""

    def test_compose_file_parses(self):
        self.assertIn("chat-sender", COMPOSE["services"])
        self.assertIn("tracker-sync", COMPOSE["services"])

    def test_chat_sender_and_tracker_sync_are_profile_gated(self):
        """A plain `docker compose up` must start neither."""
        self.assertEqual(["chat"], COMPOSE["services"]["chat-sender"]["profiles"])
        self.assertEqual(["tracker"], COMPOSE["services"]["tracker-sync"]["profiles"])
        example = (ROOT / "compose" / ".env.example").read_text()
        default_line = next(line for line in example.splitlines()
                             if line.startswith("COMPOSE_PROFILES="))
        self.assertEqual("COMPOSE_PROFILES=knowledge", default_line)

    def test_chat_sender_mounts_neither_hermes_home_nor_tracker_creds(self):
        volumes = " ".join(COMPOSE["services"]["chat-sender"]["volumes"])
        self.assertNotIn("hermes-home", volumes)
        self.assertNotIn("tracker-creds", volumes)
        self.assertIn("chat-creds:/root/.config/gws-cli", volumes)

    def test_tracker_sync_mounts_neither_hermes_home_nor_chat_creds(self):
        volumes = " ".join(COMPOSE["services"]["tracker-sync"]["volumes"])
        self.assertNotIn("hermes-home", volumes)
        self.assertNotIn("chat-creds", volumes)
        self.assertIn("tracker-creds:/var/lib/ralph-tracker", volumes)

    def test_both_named_volumes_are_declared(self):
        self.assertIn("chat-creds", COMPOSE["volumes"])
        self.assertIn("tracker-creds", COMPOSE["volumes"])

    def test_chat_sender_carries_the_tracker_container_name(self):
        """Tracker plan assumption 7: RALPH_TRACKER_CONTAINER on the sender."""
        env = COMPOSE["services"]["chat-sender"]["environment"]
        self.assertIn("RALPH_TRACKER_CONTAINER", env)

    def test_tracker_sync_carries_its_own_creds_path(self):
        """Tracker plan assumption 7: RALPH_TRACKER_CREDS on the tracker service."""
        env = COMPOSE["services"]["tracker-sync"]["environment"]
        self.assertIn("RALPH_TRACKER_CREDS", env)


class PinnedIdentityTests(unittest.TestCase):
    """The Dockerfile's /etc/machine-id and user must equal
    chat-creds-copy's own defaults, or the copied token will not decrypt."""

    def test_dockerfile_machine_id_matches_chat_creds_copy_default(self):
        creds_copy = load_module("chat_creds_copy", CHAT_CREDS_COPY)
        match = re.search(r'echo -n "([^"]+)" > /etc/machine-id', DOCKERFILE)
        self.assertIsNotNone(match, "Dockerfile.notifier must pin /etc/machine-id")
        self.assertEqual(creds_copy.DEFAULT_MACHINE_ID, match.group(1))

    def test_dockerfile_runs_as_the_same_user_chat_creds_copy_defaults_to(self):
        creds_copy = load_module("chat_creds_copy", CHAT_CREDS_COPY)
        self.assertEqual("root", creds_copy.DEFAULT_USER)
        # No USER directive: the image's own default user is root.
        self.assertNotIn("\nUSER ", "\n" + DOCKERFILE)


class RunChatSenderTests(unittest.TestCase):
    """bootstrap/run-chat-sender: one failing pass must not stop the next."""

    def run_sender(self, producer_cmd, sender_cmd, iterations, env_extra=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        instance_env = pathlib.Path(tmp.name) / "instance.env"
        env = {
            "PATH": "/usr/bin:/bin",
            "RALPH_CHAT_INSTANCE_ENV": str(instance_env),
            "RALPH_CHAT_PRODUCER_CMD": producer_cmd,
            "RALPH_CHAT_SENDER_CMD": sender_cmd,
            "RALPH_CHAT_MAX_ITERATIONS": str(iterations),
            "RALPH_CHAT_POLL_SECONDS": "0",
            "CHAT_ROUTE_SPACES": "spaces/example",
        }
        env.update(env_extra or {})
        result = subprocess.run(["bash", str(RUN_CHAT_SENDER)], env=env,
                                 capture_output=True, text=True, timeout=30)
        return result, instance_env

    def test_a_failing_producer_pass_does_not_stop_the_loop(self):
        marker = tempfile.NamedTemporaryFile(delete=False)
        marker.close()
        self.addCleanup(lambda: pathlib.Path(marker.name).unlink(missing_ok=True))
        result, _ = self.run_sender("false", f"echo ran >> {marker.name}", 3)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["ran"] * 3, pathlib.Path(marker.name).read_text().split())

    def test_a_failing_sender_pass_does_not_stop_the_loop(self):
        marker = tempfile.NamedTemporaryFile(delete=False)
        marker.close()
        self.addCleanup(lambda: pathlib.Path(marker.name).unlink(missing_ok=True))
        result, _ = self.run_sender(f"echo ran >> {marker.name}", "false", 3)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["ran"] * 3, pathlib.Path(marker.name).read_text().split())

    def test_iteration_count_is_bounded(self):
        marker = tempfile.NamedTemporaryFile(delete=False)
        marker.close()
        self.addCleanup(lambda: pathlib.Path(marker.name).unlink(missing_ok=True))
        result, _ = self.run_sender("true", f"echo ran >> {marker.name}", 5)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(5, len(pathlib.Path(marker.name).read_text().split()))

    def test_chat_send_from_the_instance_env_reaches_the_sender(self):
        """CHAT_SEND, rendered by the entrypoint, is the same key
        chat-reply's own instance_config() reads from --instance-env."""
        _, instance_env = self.run_sender("true", "true", 1, {"CHAT_SEND": "off"})
        chat_reply = load_module("chat_reply", CHAT_REPLY)
        _, _, _, _, send_enabled = chat_reply.instance_config(instance_env)
        self.assertFalse(send_enabled)


if __name__ == "__main__":
    unittest.main()
