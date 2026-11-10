"""bootstrap/overlay-grants.py (#133, #134): reads and validates a private
overlay's mcp.json and prints `config set`/`unset` directives for install to
apply directly with `hermes config`. No YAML is produced or parsed here --
install runs each directive against the real Hermes config writer, so none of
the indentation, quoting, or merge bugs a hand-rolled YAML editor can have are
possible in this design.

    python3 evals/test_overlay_grants.py
"""

import importlib.machinery
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bootstrap" / "overlay-grants.py"
loader = importlib.machinery.SourceFileLoader("overlay_grants", str(SCRIPT))
spec = importlib.util.spec_from_loader("overlay_grants", loader)
og = importlib.util.module_from_spec(spec)
loader.exec_module(og)


def write(servers):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(servers, tmp)
    tmp.close()
    return tmp.name


class DirectivesTests(unittest.TestCase):
    def test_grants_only_the_named_profile(self):
        path = write({"team-memory": {"profiles": ["researcher"], "config": {"url": "u"},
                                       "tools": ["recall"]}})
        granted = og.directives(og.load(path), "researcher")
        self.assertIn("set mcp_servers.team-memory.url \"u\"", granted)
        self.assertIn("set mcp_servers.team-memory.enabled true", granted)
        self.assertIn('set mcp_servers.team-memory.tools.include ["recall"]', granted)
        self.assertNotIn("set mcp_servers.team-memory.url \"u\"",
                         og.directives(og.load(path), "builder"))

    def test_every_grant_is_unset_before_any_is_set(self):
        """A profile the overlay doesn't currently grant this server to must
        still have it unset, so a revoked grant actually disappears."""
        path = write({"a": {"profiles": ["researcher"], "config": {}, "tools": ["x"]},
                      "b": {"profiles": ["builder"], "config": {}, "tools": ["y"]}})
        out = og.directives(og.load(path), "researcher")
        self.assertEqual(["unset mcp_servers.a", "unset mcp_servers.b"], out[:2])
        self.assertNotIn("set mcp_servers.b.enabled true", out)

    def test_config_values_round_trip_through_json(self):
        """Non-string config values (numbers, booleans) stay typed; install's
        `hermes config set` receives valid JSON either way."""
        path = write({"a": {"profiles": ["researcher"], "config": {"port": 8181, "secure": True},
                            "tools": ["x"]}})
        out = og.directives(og.load(path), "researcher")
        self.assertIn("set mcp_servers.a.port 8181", out)
        self.assertIn("set mcp_servers.a.secure true", out)


class ValidationTests(unittest.TestCase):
    def check_rejects(self, servers):
        path = write(servers)
        with self.assertRaises(SystemExit):
            og.load(path)

    def test_rejects_reserved_names(self):
        self.check_rejects({"knowledge": {"profiles": ["researcher"], "config": {}, "tools": ["x"]}})
        self.check_rejects({"lens-tools": {"profiles": ["researcher"], "config": {}, "tools": ["x"]}})

    def test_rejects_names_that_are_not_plain_lowercase_identifiers(self):
        for bad in ("True", "1abc", "a b", "a,b", "UPPER"):
            with self.subTest(bad=bad):
                self.check_rejects({bad: {"profiles": ["researcher"], "config": {}, "tools": ["x"]}})

    def test_rejects_empty_or_non_list_profiles(self):
        self.check_rejects({"a": {"profiles": [], "config": {}, "tools": ["x"]}})
        self.check_rejects({"a": {"profiles": "researcher", "config": {}, "tools": ["x"]}})
        self.check_rejects({"a": {"profiles": [1], "config": {}, "tools": ["x"]}})

    def test_rejects_empty_or_missing_tools(self):
        self.check_rejects({"a": {"profiles": ["researcher"], "config": {}, "tools": []}})
        self.check_rejects({"a": {"profiles": ["researcher"], "config": {}}})

    def test_rejects_non_object_config(self):
        self.check_rejects({"a": {"profiles": ["researcher"], "config": "not an object", "tools": ["x"]}})

    def test_accepts_a_well_formed_file(self):
        path = write({"team-memory": {"profiles": ["researcher", "default"],
                                       "config": {"url": "https://memory.example/mcp/team/"},
                                       "tools": ["recall", "reflect"]}})
        og.load(path)  # does not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
