import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AdapterBootstrapTests(unittest.TestCase):
    def test_bootstrap_leaves_existing_oauth_state_in_both_homes_alone(self):
        script = Path(__file__).resolve().parents[1] / "compose/v4/bootstrap-hermes.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / "docker"
            docker.write_text(f'''#!{sys.executable}
import os, pathlib, subprocess, sys
args = sys.argv[1:]
with open(os.environ["CALLS"], "a") as calls:
    calls.write("\\n".join(args) + "\\n")
mount = pathlib.Path(os.environ["HERMES_HOME_VOLUME"])
if args[-5:-3] == ["sh", "-c"] and args[-2] == "sh" and "umask 077; mkdir -p " in args[-3]:
    path = mount / pathlib.Path(args[-1]).relative_to("/opt/data")
    subprocess.run([*args[-5:-1], str(path)], check=True)
''')
            docker.chmod(0o755)
            env = {**os.environ, "PATH": f"{root}{os.pathsep}{os.environ['PATH']}",
                   "HERMES_IMAGE": "fixture:1", "HERMES_HOME_VOLUME": directory,
                   "CALLS": str(root / "calls")}
            env.pop("MEWRITE_AUTH_SOURCE", None)  # Ignore an ambient setting from the old bootstrap.
            legacy = root / "mewrite-home/.mewrite/agent/fixture"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("legacy state")
            current = root / "mewrite-adapter-home/.mewrite/agent/fixture"
            for attempt in range(2):
                result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(legacy.read_text(), "legacy state")
                self.assertTrue(current.parent.is_dir())
                if attempt == 0:
                    self.assertFalse(current.exists())
                    self.assertEqual(current.parent.stat().st_mode & 0o777, 0o700)
                    current.write_text("dedicated state")
            self.assertEqual(current.read_text(), "dedicated state")
            calls = (root / "calls").read_text()
            self.assertIn("env.HOME\n/opt/data/mewrite-adapter-home", calls)
            self.assertNotIn("/opt/data/mewrite-home", calls)
            self.assertNotIn("auth.json", calls)
