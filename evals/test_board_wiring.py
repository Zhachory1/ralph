"""Board creation and configuration for the staged workflow split."""

import os
import pathlib
import subprocess
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALL = (ROOT / "bootstrap" / "install").read_text()
COMPOSE = yaml.safe_load((ROOT / "compose" / "docker-compose.yml").read_text())
EXAMPLE = (ROOT / "compose" / ".env.example").read_text()


class BoardInstallTests(unittest.TestCase):
    def run_ensure_board(self, existing):
        function = "ensure_board() {" + INSTALL.split("ensure_board() {", 1)[1].split("\n}", 1)[0] + "\n}"
        with tempfile.TemporaryDirectory() as tmp:
            calls = pathlib.Path(tmp) / "calls"
            script = function + """
EXISTS=$1
hermes() {
    if [ "$3" = list ]; then
        [ "$EXISTS" = yes ] && printf '●   queues                  Custom name\\n'
    elif [ "$3" = create ]; then
        printf '%s\\n' "$*" >> "$CALLS"
        EXISTS=yes
    fi
}
ensure_board queues Intake 'Chat and email intake'
ensure_board queues Intake 'Chat and email intake'
"""
            result = subprocess.run(["bash", "-c", script, "bash", "yes" if existing else "no"],
                                    env={**os.environ, "CALLS": str(calls)},
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(0, result.returncode, result.stderr)
            return result.stdout, calls.read_text().splitlines() if calls.exists() else []

    def test_a_new_board_is_created_once_with_its_name_and_description(self):
        output, calls = self.run_ensure_board(existing=False)
        self.assertEqual(["kanban boards create queues --name Intake --description "
                          "Chat and email intake --default-workdir /workspaces"], calls)
        self.assertIn("created board queues", output)
        self.assertIn("board queues already exists", output)

    def test_an_existing_board_keeps_its_display_name(self):
        output, calls = self.run_ensure_board(existing=True)
        self.assertEqual([], calls)
        self.assertEqual(2, output.count("board queues already exists"))

    def test_install_ensures_the_four_workflow_boards(self):
        for slug in ("BOARD", "QUEUES_BOARD", "LENS_BOARD", "MAINTAIN_BOARD"):
            with self.subTest(slug=slug):
                self.assertIn(f'ensure_board "${slug}"', INSTALL)
        self.assertEqual(1, INSTALL.count("hermes kanban boards create"))

    def test_synthesis_board_setting_is_written_after_tokens(self):
        target = INSTALL.index("profiles/ralph-review-synthesis/.env")
        self.assertLess(INSTALL.index("# CI logs for the profiles"), target)
        self.assertLess(INSTALL.index("# A dispatched worker sees"), target)
        self.assertIn('grep -v "^RALPH_LENS_BOARD="', INSTALL)
        self.assertIn('profiles/ralph-review-synthesis/.env', INSTALL)


class BoardComposeTests(unittest.TestCase):
    def test_install_and_review_loop_receive_all_board_names(self):
        for service in ("init", "review-loop"):
            with self.subTest(service=service):
                env = COMPOSE["services"][service]["environment"]
                for name in ("RALPH_LENS_BOARD", "RALPH_MAINTAIN_BOARD", "RALPH_QUEUES_BOARD"):
                    self.assertIn(name, env)

    def test_hermes_receives_the_synthesis_board_name(self):
        self.assertIn("RALPH_LENS_BOARD", COMPOSE["services"]["hermes"]["environment"])

    def test_notifier_keeps_its_old_board_setting_until_cutover(self):
        self.assertIn("RALPH_REVIEW_BOARD", COMPOSE["services"]["notifier"]["environment"])

    def test_example_lists_all_four_board_defaults(self):
        for setting in ("RALPH_BOARD=ralph", "RALPH_QUEUES_BOARD=queues",
                        "RALPH_LENS_BOARD=lenses", "RALPH_MAINTAIN_BOARD=maintain"):
            with self.subTest(setting=setting):
                self.assertEqual(1, EXAMPLE.splitlines().count(setting))


if __name__ == "__main__":
    unittest.main()
