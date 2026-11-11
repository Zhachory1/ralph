"""The one-time host->sidecar Chat token copy (chat-creds-copy, D8): a round
trip through gchat.py's own `_load_creds` path, never a real credential."""

import base64
import importlib.machinery
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "bootstrap" / "chat-creds-copy"
GCHAT = pathlib.Path(__file__).resolve().parent.parent / "skills/ops/google-chat/gchat.py"

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


def pinned_key_for_test(salt: str, machine_id: str, user: str) -> bytes:
    """Mirrors gchat.py's `_key()` formula for a test-chosen salt, machine id
    and user -- never this host's real ones."""
    fingerprint = f"{machine_id}:{user}:gws-cli".encode()
    derived = HKDF(algorithm=SHA256(), length=32, salt=salt.encode(),
                   info=b"token-encryption").derive(fingerprint)
    return base64.urlsafe_b64encode(derived)


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


class MainUsesHostConfigsOwnSaltTests(unittest.TestCase):
    """`main()`'s own `--host-config` must be where `gchat._key()` reads its
    salt from, not wherever CFG_DIR/CONFIG already points in this process --
    a second identity's directory has its own gws_config.json with its own
    encryption_salt, different from the default dir's. A token encrypted
    under that salt must round-trip through `main()` with only --host-config
    set, the same as a real second-identity run (GWS_CFG_DIR unset)."""

    def setUp(self):
        self.script = load_script()
        self.gchat = load_gchat()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.host_config = pathlib.Path(self.tmp.name) / "personal-host"
        self.host_config.mkdir()
        self.other_salt = "a-different-salt-than-the-default-dir-has"
        (self.host_config / "gws_config.json").write_text(
            json.dumps({"encryption_salt": self.other_salt}))
        self.machine_id, self.user = "fake-host-machine", "fake-host-user"
        key = pinned_key_for_test(self.other_salt, self.machine_id, self.user)
        encrypted = self.gchat.Fernet(key).encrypt(json.dumps(FAKE_INFO).encode())
        (self.host_config / "chat_token.json.enc").write_bytes(encrypted)

    def test_main_reads_the_salt_from_host_config_not_the_default_cfg_dir(self):
        """load_gchat() is wrapped, not replaced: main()'s real call still
        runs (so the GWS_CFG_DIR it sets is what the freshly-loaded module's
        CONFIG resolves from), but the loaded module's machine id and user
        are pinned to this test's fakes once it comes back, matching how the
        token above was encrypted."""
        real_load_gchat = self.script.load_gchat

        def load_and_pin():
            module = real_load_gchat()
            module._machine_id = lambda: self.machine_id
            return module

        with patch("getpass.getuser", return_value=self.user), \
                patch.object(self.script, "load_gchat", side_effect=load_and_pin), \
                patch.object(self.script, "instance_name", return_value="fake"), \
                patch.object(self.script, "write_into_container") as write, \
                patch("sys.argv", ["chat-creds-copy", "--instance-env", "/dev/null",
                                   "--host-config", str(self.host_config)]):
            rc = self.script.main()
        self.assertEqual(0, rc)
        write.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
