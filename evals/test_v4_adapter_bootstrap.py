import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class AdapterBootstrapTests(unittest.TestCase):
    def test_bootstrap_does_not_require_or_copy_oauth_state(self):
        script = Path(__file__).resolve().parents[1] / "compose/v4/bootstrap-hermes.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / "docker"
            docker.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$CALLS"\n')
            docker.chmod(0o755)
            env = {**os.environ, "PATH": f"{root}{os.pathsep}{os.environ['PATH']}",
                   "HERMES_IMAGE": "fixture:1", "HERMES_HOME_VOLUME": directory,
                   "CALLS": str(root / "calls")}
            env.pop("MEWRITE_AUTH_SOURCE", None)  # Ignore an ambient setting from the old bootstrap.
            result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = (root / "calls").read_text()
            self.assertIn("env.HOME\n/opt/data/mewrite-adapter-home", calls)
            self.assertNotIn("/opt/data/mewrite-home", calls)
            self.assertNotIn("auth.json", calls)
