"""The one-time host->sidecar Chat token copy (chat-creds-copy, D8): a round
trip through gchat.py's own `_load_creds` path, never a real credential."""

import importlib.machinery
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "bootstrap" / "chat-creds-copy"
GCHAT = pathlib.Path(__file__).resolve().parent.parent / "skills/ops/google-chat/gchat.py"
COMPOSE = pathlib.Path(__file__).resolve().parent.parent / "compose" / "docker-compose.yml"
RUN_CHAT_SENDER = pathlib.Path(__file__).resolve().parent.parent / "bootstrap" / "run-chat-sender"

HOST_MACHINE_ID = "fake-host-machine"
HOST_USER = "fake-host-user"
CONTAINER_MACHINE_ID = "fake-container-machine"
CONTAINER_USER = "fake-container-user"
SALT = "fake-salt-not-a-secret"
# A recognizable stand-in for a real OAuth refresh token. Asserted absent
# from every byte of output this test captures.
FAKE_TOKEN_MARKER = "FAKE-REFRESH-TOKEN-NEVER-REAL-xyz123"
FAKE_INFO = {
    "token": "fake-access-token", "refresh_token": FAKE_TOKEN_MARKER,
    "client_id": "fake-client-id", "client_secret": "fake-client-secret",
    "scopes": ["https://www.googleapis.com/auth/chat.messages.create"],
}


def load_script():
    loader = importlib.machinery.SourceFileLoader("chat_creds_copy", str(SCRIPT))
    spec = importlib.util.spec_from_loader("chat_creds_copy", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def load_gchat():
    loader = importlib.machinery.SourceFileLoader("gchat", str(GCHAT))
    spec = importlib.util.spec_from_loader("gchat", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ChatCredsCopyTests(unittest.TestCase):
    def setUp(self):
        self.script = load_script()
        self.gchat = load_gchat()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.host_config = pathlib.Path(self.tmp.name) / "host"
        self.host_config.mkdir()
        (self.host_config / "gws_config.json").write_text(
            json.dumps({"encryption_salt": SALT}))

    def seed_host_token(self, machine_id=HOST_MACHINE_ID, user=HOST_USER, info=None):
        """Write a host token exactly as gchat.py's own `_save_creds` would,
        under a fake machine id and user -- standing in for "this host" and
        never a real credential."""
        key = self._key_with(machine_id, user)
        encrypted = self.gchat.Fernet(key).encrypt(json.dumps(info or FAKE_INFO).encode())
        (self.host_config / "chat_token.json.enc").write_bytes(encrypted)

    def _key_with(self, machine_id, user):
        with patch.object(self.gchat, "_machine_id", return_value=machine_id), \
                patch("getpass.getuser", return_value=user), \
                patch.object(self.gchat, "CONFIG", self.host_config / "gws_config.json"):
            return self.gchat._key()

    def load_creds_as(self, cfg_dir, machine_id, user):
        """Exercise the real `gchat._load_creds` path against a given
        machine id and user, pointed at a temporary CFG_DIR."""
        with patch.object(self.gchat, "_machine_id", return_value=machine_id), \
                patch("getpass.getuser", return_value=user), \
                patch.object(self.gchat, "CFG_DIR", cfg_dir), \
                patch.object(self.gchat, "CONFIG", cfg_dir / "gws_config.json"), \
                patch.object(self.gchat, "TOKEN_ENC", cfg_dir / "chat_token.json.enc"):
            return self.gchat._load_creds()

    def reencrypt(self, machine_id, user, host_machine_id=HOST_MACHINE_ID, host_user=HOST_USER):
        """`gchat._key()` always reads the live machine id, the live user and
        the module's own hardcoded CONFIG path (gchat.py:58,106-114); in
        production those are this real host's own, but the test stands in
        with fakes for all three so the "host" side of the round trip is
        reproducible and never touches a real machine id or user."""
        with patch.object(self.gchat, "_machine_id", return_value=host_machine_id), \
                patch("getpass.getuser", return_value=host_user), \
                patch.object(self.gchat, "CONFIG", self.host_config / "gws_config.json"):
            return self.script.reencrypt(self.gchat, self.host_config, machine_id, user)

    def test_round_trip_decrypts_through_load_creds(self):
        self.seed_host_token()
        config_bytes, token_bytes = self.reencrypt(CONTAINER_MACHINE_ID, CONTAINER_USER)

        dest = pathlib.Path(self.tmp.name) / "container"
        dest.mkdir()
        (dest / "gws_config.json").write_bytes(config_bytes)
        (dest / "chat_token.json.enc").write_bytes(token_bytes)

        creds = self.load_creds_as(dest, CONTAINER_MACHINE_ID, CONTAINER_USER)
        self.assertIsNotNone(creds)
        self.assertEqual(FAKE_TOKEN_MARKER, creds.refresh_token)

    def test_wrong_machine_id_does_not_decrypt(self):
        self.seed_host_token()
        config_bytes, token_bytes = self.reencrypt(CONTAINER_MACHINE_ID, CONTAINER_USER)

        dest = pathlib.Path(self.tmp.name) / "container-wrong"
        dest.mkdir()
        (dest / "gws_config.json").write_bytes(config_bytes)
        (dest / "chat_token.json.enc").write_bytes(token_bytes)

        creds = self.load_creds_as(dest, "a-different-machine", CONTAINER_USER)
        self.assertIsNone(creds)

    def test_wrong_user_does_not_decrypt(self):
        self.seed_host_token()
        config_bytes, token_bytes = self.reencrypt(CONTAINER_MACHINE_ID, CONTAINER_USER)

        dest = pathlib.Path(self.tmp.name) / "container-wrong-user"
        dest.mkdir()
        (dest / "gws_config.json").write_bytes(config_bytes)
        (dest / "chat_token.json.enc").write_bytes(token_bytes)

        creds = self.load_creds_as(dest, CONTAINER_MACHINE_ID, "a-different-user")
        self.assertIsNone(creds)

    def test_source_file_is_left_unmodified(self):
        self.seed_host_token()
        before = (self.host_config / "chat_token.json.enc").read_bytes()
        before_config = (self.host_config / "gws_config.json").read_bytes()
        self.reencrypt(CONTAINER_MACHINE_ID, CONTAINER_USER)
        self.assertEqual(before, (self.host_config / "chat_token.json.enc").read_bytes())
        self.assertEqual(before_config, (self.host_config / "gws_config.json").read_bytes())

    def test_no_token_on_host_refuses_without_a_traceback(self):
        with self.assertRaises(ValueError):
            self.reencrypt(CONTAINER_MACHINE_ID, CONTAINER_USER)

    def test_never_prints_the_token(self):
        import contextlib
        import io
        self.seed_host_token()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            config_bytes, token_bytes = self.reencrypt(CONTAINER_MACHINE_ID, CONTAINER_USER)
        self.assertNotIn(FAKE_TOKEN_MARKER, out.getvalue())
        self.assertNotIn(FAKE_TOKEN_MARKER, err.getvalue())
        # The re-encrypted bytes are ciphertext: confirm the marker is not
        # merely unencrypted-but-silent in the returned bytes either.
        self.assertNotIn(FAKE_TOKEN_MARKER.encode(), token_bytes)
        self.assertNotIn(FAKE_TOKEN_MARKER.encode(), config_bytes)


class ComposeRenderTests(unittest.TestCase):
    """compose/docker-compose.yml renders, and the two new services' mounts
    stay on their own side of the credential boundary (dd-chat.md Sec4.1,
    dd-tracker.md Sec2). Uses the real `docker compose config` as the parser,
    not a bundled YAML library this repo's CI does not already install."""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("docker"):
            raise unittest.SkipTest("docker not installed in this environment")

    def rendered(self):
        env = dict(os.environ, HERMES_VERSION="v0.0.0-test",
                   HERMES_WEBUI_VERSION="0.0.0-test",
                   RALPH_SANDBOX_ROOT="/tmp/ralph-compose-test-sandboxes",
                   RALPH_DASHBOARD_PASSWORD="test-password-not-a-secret")
        result = subprocess.run(["docker", "compose", "-f", str(COMPOSE), "config"],
                                capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout

    def service_block(self, rendered_text, service_name):
        """This file's own two-space indents, under `services:` -- not a
        general YAML reader, just enough to isolate one service's lines."""
        lines = rendered_text.splitlines()
        start = None
        for i, line in enumerate(lines):
            if line == f"  {service_name}:":
                start = i
                break
        self.assertIsNotNone(start, f"service {service_name} not found in rendered compose")
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if lines[j].strip() and not lines[j].startswith("    "):
                end = j
                break
        return "\n".join(lines[start:end])

    def test_compose_file_parses_with_both_new_services(self):
        rendered = self.rendered()
        self.assertIn("  chat-sender:", rendered)
        self.assertIn("  tracker-sync:", rendered)

    def test_chat_sender_mounts_neither_hermes_home_nor_tracker_creds(self):
        block = self.service_block(self.rendered(), "chat-sender")
        self.assertNotIn("hermes-home", block)
        self.assertNotIn("tracker-creds", block)

    def test_tracker_sync_mounts_neither_hermes_home_nor_chat_creds(self):
        block = self.service_block(self.rendered(), "tracker-sync")
        self.assertNotIn("hermes-home", block)
        self.assertNotIn("chat-creds", block)


class RunChatSenderTests(unittest.TestCase):
    """bootstrap/run-chat-sender survives one failing pass and runs the
    next, driven by a fake command and a bounded iteration count
    (RALPH_CHAT_MAX_ITERATIONS, RALPH_CHAT_PRODUCER_CMD/_SENDER_CMD -- unset
    in the real container, which loops forever)."""

    def test_survives_one_failing_pass_and_runs_the_next(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance_env = pathlib.Path(tmp) / "chat-instance.env"
            marker = pathlib.Path(tmp) / "sender-ran"
            env = dict(os.environ,
                       RALPH_CHAT_INSTANCE_ENV=str(instance_env),
                       RALPH_CHAT_POLL_SECONDS="0",
                       RALPH_CHAT_MAX_ITERATIONS="2",
                       RALPH_CHAT_PRODUCER_CMD="false",
                       RALPH_CHAT_SENDER_CMD=f"touch {marker}")
            result = subprocess.run(["bash", str(RUN_CHAT_SENDER)], capture_output=True,
                                    text=True, env=env, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("producer pass failed, will retry", result.stderr)
            self.assertTrue(marker.is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
