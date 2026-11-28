"""Check maintenance CLI routing without credentials, services or subprocess writes."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bootstrap import paperclip_maintain as maintain
from evals.test_paperclip_maintain_workflow import Native


class MaintenanceCLITest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Path(self.directory.name) / "config.json"
        self.config.write_text(json.dumps(Native().config))

    def call(self, *arguments):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return maintain.main([*arguments, "--config", str(self.config)])

    def test_routine_renders_fixed_process_configuration_without_authority(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(maintain, "PaperclipClient") as client:
            self.assertEqual(self.call("routine"), 0)
        client.assert_not_called()
        body = maintain.routine_requests(Native().config, str(self.config))["checker"]["body"]
        self.assertEqual(body["adapterType"], "process")
        self.assertNotIn("aiConnection", body["runtimeConfig"])
        self.assertEqual(body["adapterConfig"]["args"][-1], str(self.config))
        self.assertIn("poll", body["adapterConfig"]["args"])

    def test_poll_requires_native_run_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertNotEqual(self.call("poll"), 0)

    def test_poll_uses_workflow_and_returns_partial_failure(self):
        env = {"PAPERCLIP_API_URL": "http://127.0.0.1:3100",
               "PAPERCLIP_API_KEY": "placeholder", "PAPERCLIP_RUN_ID": "run"}
        with patch.dict(os.environ, env, clear=True), patch.object(maintain, "PaperclipClient"), \
                patch.object(maintain, "GitHubCLI"), patch.object(maintain, "MaintenanceWorkflow") as workflow:
            workflow.return_value.poll.return_value = {"status": "partial"}
            self.assertNotEqual(self.call("poll"), 0)
            workflow.return_value.poll.assert_called_once_with()

    def test_record_result_routes_structured_file_and_workspace(self):
        result = Path(self.directory.name) / "result.json"
        result.write_text('{"outcome":"noop","handled":[]}')
        env = {"PAPERCLIP_API_URL": "http://127.0.0.1:3100",
               "PAPERCLIP_API_KEY": "placeholder", "PAPERCLIP_RUN_ID": "run"}
        with patch.dict(os.environ, env, clear=True), patch.object(maintain, "PaperclipClient"), \
                patch.object(maintain, "GitHubCLI"), patch.object(maintain, "MaintenanceWorkflow") as workflow:
            workflow.return_value.record_result.return_value = {"status": "recorded"}
            self.assertEqual(self.call("record-result", "--result", str(result), "--workspace", "/work"), 0)
            workflow.return_value.record_result.assert_called_once_with(
                {"outcome": "noop", "handled": []}, workspace="/work")

    def test_reply_routes_only_explicit_feedback_and_body(self):
        env = {"PAPERCLIP_API_URL": "http://127.0.0.1:3100",
               "PAPERCLIP_API_KEY": "placeholder", "PAPERCLIP_RUN_ID": "run"}
        with patch.dict(os.environ, env, clear=True), patch.object(maintain, "PaperclipClient"), \
                patch.object(maintain, "GitHubCLI"), patch.object(maintain, "MaintenanceWorkflow") as workflow:
            workflow.return_value.apply_effect.return_value = {"commentId": 1}
            self.assertEqual(self.call("reply", "--source", "version", "--body", "Fixed"), 0)
            workflow.return_value.apply_effect.assert_called_once_with(
                "reply", source="version", body="Fixed", workspace=None)
