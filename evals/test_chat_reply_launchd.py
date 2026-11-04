"""The host reply job starts only the scoped scan with no credential values."""

import importlib.machinery
import importlib.util
import pathlib
import plistlib
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "bootstrap" / "install-chat-reply-launchd"
loader = importlib.machinery.SourceFileLoader("chat_reply_launchd", str(SCRIPT))
spec = importlib.util.spec_from_loader("chat_reply_launchd", loader)
installer = importlib.util.module_from_spec(spec)
loader.exec_module(installer)


class LaunchdTests(unittest.TestCase):
    def test_scoped_recurring_job_contains_no_credential_or_room(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            env = root / ".env.work"
            env.write_text("RALPH_INSTANCE=ralph-work\nCHAT_ROUTE_SPACES=spaces/private-example\n"
                           "RALPH_MAINTAIN_TOKEN=test-secret\n")
            plist = installer.build_plist(env, root / "python3", root / "chat-reply",
                                          root / "reply.log")
            self.assertEqual("dev.ralph.chat-reply.ralph-work", plist["Label"])
            self.assertEqual(60, plist["StartInterval"])
            self.assertTrue(plist["RunAtLoad"])
            self.assertEqual(0o077, plist["Umask"])
            self.assertIn("/opt/homebrew/bin", plist["EnvironmentVariables"]["PATH"])
            self.assertIn("/usr/local/bin", plist["EnvironmentVariables"]["PATH"])
            self.assertEqual([str(root / "python3"), str(root / "chat-reply"), "--scan",
                              "--instance-env", str(env)], plist["ProgramArguments"])
            text = plistlib.dumps(plist).decode()
            self.assertNotIn("test-secret", text)
            self.assertNotIn("private-example", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
