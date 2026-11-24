"""Offline regression checks for qmd fixture tool visibility and stdio timeouts."""

import json
import runpy
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
MODEL = runpy.run_path(str(HERE / "paperclip-model.py"))
GATEWAY = runpy.run_path(str(HERE / "paperclip-qmd-gateway-canary.py"))


class QmdCanaryTests(unittest.TestCase):
    def test_same_direct_tool_check_for_both_seats(self):
        qmd_turn = MODEL["qmd_turn"]
        delta, finish = qmd_turn({"terminal"}, [])
        self.assertEqual((delta["content"], finish), ("QMD_TOOL_NOT_ADVERTISED", "stop"))

        delta, finish = qmd_turn({"terminal", "mcp__qmd__query"}, [])
        call, = delta["tool_calls"]
        self.assertEqual(finish, "tool_calls")
        self.assertEqual(call["function"]["name"], "mcp__qmd__query")
        self.assertEqual(json.loads(call["function"]["arguments"])["collections"], ["approved"])
        content = json.dumps({"result": "fixture summary", "structuredContent": {"results": [
            {"file": "approved/1-projects/fixture/rfc.md", "snippet": "3: coppermeadow"},
        ]}})
        reply = {"role": "tool", "tool_call_id": call["id"], "content": content}
        delta, finish = qmd_turn({"terminal", "mcp__qmd__query"}, [reply])
        self.assertEqual((delta["content"], finish), ("QMD_RESULT: " + content, "stop"))

    def test_error_with_expected_term_and_path_is_not_a_retrieval_pass(self):
        content = json.dumps({"error": "Could not query coppermeadow in approved/1-projects/fixture/rfc.md"})
        reply = {"role": "tool", "tool_call_id": "qmd_query", "content": content}
        delta, finish = MODEL["qmd_turn"]({"terminal", "mcp__qmd__query"}, [reply])
        self.assertEqual((delta["content"], finish), ("QMD_RETRIEVAL_FAILED:tool_error", "stop"))

    def test_retrieval_diagnostics_report_shape_without_echoing_payload(self):
        for payload, reason in (
            ({"result": "coppermeadow approved/1-projects/fixture/rfc.md"}, "text_result"),
            ({"result": {"results": []}}, "object_result"),
            ({"structuredContent": {"results": []}}, "unexpected_hit_count"),
            ({"structuredContent": {"results": [{"file": "unapproved.md"}]}}, "unexpected_hit_file"),
            ({"structuredContent": {"results": [
                {"file": "approved/1-projects/fixture/rfc.md", "snippet": "different term"},
            ]}}, "missing_hit_term"),
        ):
            with self.subTest(reason=reason):
                reply = {"role": "tool", "tool_call_id": "qmd_query", "content": json.dumps(payload)}
                delta, finish = MODEL["qmd_turn"]({"terminal", "mcp__qmd__query"}, [reply])
                self.assertEqual((delta["content"], finish), (f"QMD_RETRIEVAL_FAILED:{reason}", "stop"))

    def test_unexpected_tool_surface_is_not_a_negative_pass(self):
        for names in (set(), {"terminal", "tool_search"}, {"terminal", "mcp__qmd__get"}):
            with self.subTest(names=names):
                delta, finish = MODEL["qmd_turn"](names, [])
                self.assertEqual((delta["content"], finish), ("QMD_FIXTURE_TOOL_SURFACE_ERROR", "stop"))

    def test_stdio_query_waits_for_initialization_and_handles_partial_reply(self):
        program = """
import json, sys
def read():
    return json.loads(sys.stdin.readline())
assert read()["method"] == "initialize"
print('{"jsonrpc":"2.0","id":1,"result":{}}', flush=True)
assert read()["method"] == "notifications/initialized"
query = read()
assert query["method"] == "tools/call"
sys.stdout.write('{"jsonrpc":"2.0","id":2,')
sys.stdout.flush()
print('"result":{"structuredContent":{"results":["fixture hit"]}}}', flush=True)
assert sys.stdin.read() == ""
"""
        self.assertEqual(GATEWAY["run_mcp_query"]([sys.executable, "-c", program], "fixture"), ["fixture hit"])

    def test_partial_stdio_line_times_out_and_child_is_reaped(self):
        program = "import sys, time; sys.stdin.readline(); print('{', end='', flush=True); time.sleep(60)"
        argv = [sys.executable, "-c", program]
        children = []
        real_popen = subprocess.Popen

        def capture(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child

        with patch.object(subprocess, "Popen", side_effect=capture):
            with self.assertRaises(TimeoutError):
                GATEWAY["run_mcp_query"](argv, "fixture", timeout=0.5)
        child, = children
        self.assertIsNotNone(child.returncode)


if __name__ == "__main__":
    unittest.main()
