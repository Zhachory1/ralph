"""The synthesis handoff tool accepts only its configured review board."""

import importlib.machinery
import importlib.util
import os
import pathlib
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bootstrap" / "lens-tools"
loader = importlib.machinery.SourceFileLoader("ralph_lens_tools", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
lens_tools = importlib.util.module_from_spec(spec)
loader.exec_module(lens_tools)


class ParentHandoffsBoardTests(unittest.TestCase):
    def assert_reaches_binding(self, board, env):
        aliases = {"profile": "ralph-review-synthesis", "board": board}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(lens_tools, "safe_binding", side_effect=RuntimeError("binding reached")):
            with self.assertRaisesRegex(RuntimeError, "binding reached"):
                lens_tools.parent_handoffs(aliases)

    def assert_refused(self, board, env, profile="ralph-review-synthesis", reason="restricted"):
        aliases = {"profile": profile, "board": board}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(lens_tools, "safe_binding") as binding:
            with self.assertRaisesRegex(ValueError, reason):
                lens_tools.parent_handoffs(aliases)
            binding.assert_not_called()

    def test_synthesis_on_the_expected_board_reaches_its_binding(self):
        self.assert_reaches_binding("lenses", {"LENS_EXPECTED_BOARD": "lenses"})

    def test_synthesis_on_the_feature_board_is_refused(self):
        self.assert_refused("ralph", {"LENS_EXPECTED_BOARD": "lenses"})

    def test_synthesis_on_the_maintenance_board_is_refused(self):
        self.assert_refused("maintain", {"LENS_EXPECTED_BOARD": "lenses"})

    def test_other_profiles_cannot_read_parent_handoffs(self):
        self.assert_refused("lenses", {"LENS_EXPECTED_BOARD": "lenses"},
                            profile="ralph-lens-generalist")

    def test_profile_setting_wins_over_process_setting(self):
        self.assert_reaches_binding("custom-lenses", {"LENS_EXPECTED_BOARD": "custom-lenses",
                                                      "RALPH_LENS_BOARD": "other-board"})

    def test_process_setting_is_used_when_profile_setting_is_absent(self):
        self.assert_reaches_binding("custom-lenses", {"RALPH_LENS_BOARD": "custom-lenses"})

    def test_built_in_default_is_used_when_both_settings_are_absent(self):
        self.assert_reaches_binding("lenses", {})

    def test_built_in_default_does_not_allow_queues(self):
        self.assert_refused("queues", {})

    def test_invalid_profile_setting_does_not_fall_back(self):
        self.assert_refused("lenses", {"LENS_EXPECTED_BOARD": "INVALID",
                                       "RALPH_LENS_BOARD": "lenses"}, reason="invalid")

    def test_invalid_process_setting_does_not_fall_back(self):
        self.assert_refused("lenses", {"RALPH_LENS_BOARD": "INVALID"}, reason="invalid")

    def test_empty_profile_setting_does_not_fall_back(self):
        self.assert_refused("lenses", {"LENS_EXPECTED_BOARD": "",
                                       "RALPH_LENS_BOARD": "lenses"}, reason="invalid")

    def test_other_lenses_do_not_require_the_expected_board_setting(self):
        self.assertNotIn("expected_board", lens_tools.LENS_ALIASES)


if __name__ == "__main__":
    unittest.main()
