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
        granted = og.directives(og.load(path), "researcher", {})
        self.assertIn("set mcp_servers.team-memory.url u", granted)
        self.assertIn("set mcp_servers.team-memory.enabled true", granted)
        self.assertIn('set mcp_servers.team-memory.tools.include ["recall"]', granted)
        self.assertIn("set mcp_servers.team-memory._overlay true", granted)
        self.assertNotIn("set mcp_servers.team-memory.url u",
                         og.directives(og.load(path), "builder", {}))

    def test_a_currently_granted_server_is_unset_before_being_reset(self):
        """A changed config/tools value replaces the old one rather than
        merging with it: unset comes before set for every granted name."""
        path = write({"a": {"profiles": ["researcher"], "config": {}, "tools": ["x"]}})
        out = og.directives(og.load(path), "researcher", {})
        self.assertEqual("unset mcp_servers.a", out[0])
        self.assertLess(out.index("unset mcp_servers.a"), out.index("set mcp_servers.a.enabled true"))

    def test_a_server_no_longer_granted_to_this_profile_is_unset(self):
        """The overlay still grants it to someone else, just not this
        profile: this profile's own marked copy must still come down."""
        path = write({"a": {"profiles": ["builder"], "config": {}, "tools": ["x"]}})
        current = {"a": {"url": "old", "_overlay": True}}
        out = og.directives(og.load(path), "researcher", current)
        self.assertIn("unset mcp_servers.a", out)
        self.assertNotIn("set mcp_servers.a.enabled true", out)

    def test_a_server_deleted_from_mcp_json_entirely_is_still_unset(self):
        """#134 review: a server removed from mcp.json -- not merely dropped
        from one profile's list, but gone from the file -- never appears in
        `servers.items()`, so nothing about it would be emitted without
        checking the profile's own currently-marked servers too."""
        path = write({})  # mcp.json now has nothing in it
        current = {"a": {"url": "old", "_overlay": True}}
        out = og.directives(og.load(path), "researcher", current)
        self.assertEqual(["unset mcp_servers.a"], out)

    def test_the_overlay_being_removed_entirely_still_cleans_up(self):
        """#134 review: install must not skip the cleanup pass just because
        there is no mcp.json at all right now -- load('') is the same as an
        empty grant, not 'nothing to do'."""
        current = {"a": {"url": "old", "_overlay": True}, "mine": {"url": "owner"}}
        out = og.directives(og.load(""), "researcher", current)
        self.assertEqual(["unset mcp_servers.a"], out)

    def test_a_missing_file_path_behaves_like_an_empty_grant(self):
        out = og.directives(og.load("/no/such/path.json"), "researcher", {})
        self.assertEqual([], out)

    def test_a_hand_added_server_is_never_unset(self):
        """Only a server this script itself marked (`_overlay: true`) is ever
        a candidate for removal; anything else is the owner's."""
        path = write({})
        current = {"mine": {"url": "http://owner"}}  # no _overlay key
        out = og.directives(og.load(path), "researcher", current)
        self.assertEqual([], out)

    def test_config_values_round_trip_through_json(self):
        """Non-string config values (numbers, booleans) stay typed; install's
        `hermes config set` receives valid JSON either way."""
        path = write({"a": {"profiles": ["researcher"], "config": {"port": 8181, "secure": True},
                            "tools": ["x"]}})
        out = og.directives(og.load(path), "researcher", {})
        self.assertIn("set mcp_servers.a.port 8181", out)
        self.assertIn("set mcp_servers.a.secure true", out)

    def test_string_config_values_are_not_double_quoted(self):
        """Found live: `hermes config set` stores a string argument literally
        rather than decoding it as JSON first, so json.dumps-ing a URL wrapped
        it in quote characters that then became part of the stored string."""
        path = write({"a": {"profiles": ["researcher"],
                            "config": {"url": "https://example.com/mcp/team/"},
                            "tools": ["x"]}})
        out = og.directives(og.load(path), "researcher", {})
        self.assertIn("set mcp_servers.a.url https://example.com/mcp/team/", out)
        self.assertNotIn('set mcp_servers.a.url "https://example.com/mcp/team/"', out)


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

    def test_rejects_newlines_in_config_values_and_tool_names(self):
        """#134 review: output is one directive per line, read with bash
        `read -r`; a newline embedded in a value would print as extra lines
        and be read back as unintended additional directives."""
        self.check_rejects({"a": {"profiles": ["researcher"],
                                  "config": {"note": "line one\nset mcp_servers.evil.url x"},
                                  "tools": ["x"]}})
        self.check_rejects({"a": {"profiles": ["researcher"], "config": {},
                                  "tools": ["x\nunset mcp_servers.knowledge"]}})

    def test_accepts_a_well_formed_file(self):
        path = write({"team-memory": {"profiles": ["researcher", "default"],
                                       "config": {"url": "https://memory.example/mcp/team/"},
                                       "tools": ["recall", "reflect"]}})
        og.load(path)  # does not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
