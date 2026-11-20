"""Exercise native pause recovery on an offline, disposable Paperclip board."""

import runpy
import secrets
import subprocess
import time
from pathlib import Path

fixture = runpy.run_path(str(Path(__file__).with_name("paperclip-rfc-canary.py")))
request = fixture["request"]

SCRIPT = r'''#!/bin/sh
set -e
if [ "${1:-}" != chat ]; then
  printf 'fixture hermes\n'
  exit 0
fi
pc() { curl -fsS --max-time 10 -H "Authorization: Bearer $PAPERCLIP_API_KEY" -H "X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID" "$@"; }
run=$(pc "$PAPERCLIP_API_URL/api/heartbeat-runs/$PAPERCLIP_RUN_ID")
issue=$(printf '%s' "$run" | jq -r '.issueId')
reason=$(printf '%s' "$run" | jq -r '.contextSnapshot.wakeReason // empty')
if [ "$reason" = issue_disposition_repair ]; then
  marker="/tmp/paused-repair-$FIXTURE_ADAPTER"
  if [ ! -f "$marker" ]; then touch "$marker"; sleep 90; fi
  pc --json '{"status":"done","comment":"The resumed owner completed its task."}' -X PATCH "$PAPERCLIP_API_URL/api/issues/$issue" >/dev/null
else
  pc --json '{"comment":"The original owner needs to verify the receipt."}' -X PATCH "$PAPERCLIP_API_URL/api/issues/$issue" >/dev/null
  printf 'Unable to verify the receipt; task remains incomplete.\nsession_id: fixture-session\n'
fi
'''


def wait_for(description, read, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = read()
        if result:
            return result
        time.sleep(0.5)
    raise AssertionError(f"Timed out waiting for {description}")


def check_adapter(board, root, secret, adapter):
    env = {"FIXTURE_ADAPTER": adapter}
    if adapter == "hermes":
        env.update({
            "HERMES_HOME": "/paperclip/hermes-resume-fixture",
            "OPENAI_API_KEY": {"type": "secret_ref", "secretId": secret["id"]},
        })
        adapter_type = "hermes_local"
        config = {"hermesCommand": "/tmp/pause-recovery-fixture", "model": "gpt-6-luna",
                  "provider": "openai-codex", "quiet": True, "persistSession": False,
                  "cwd": "/paperclip", "env": env}
    else:
        adapter_type = "process"
        config = {"command": "/tmp/pause-recovery-fixture", "args": ["chat"],
                  "cwd": "/paperclip", "env": env}
    agent = board(f"{root}/agents", {"name": f"{adapter} owner", "adapterType": adapter_type,
                                     "adapterConfig": config})
    agent_path = f"agents/{agent['id']}"
    try:
        wait_for("agent ready", lambda: board(agent_path).get("lifecycleState") == "ready")
    except AssertionError:
        current = board(agent_path)
        raise AssertionError(f"{adapter} agent not ready: {current['status']}, "
                             f"{current.get('lifecycleState')}, {current.get('lifecycleError')}") from None
    issue = board(f"{root}/issues", {"title": f"{adapter} recovery fixture", "status": "todo",
                                     "assigneeAgentId": agent["id"]})
    path = f"issues/{issue['id']}"

    def running_repair():
        for row in board(f"{path}/runs"):
            if row["agentId"] == agent["id"] and row["status"] == "running":
                detail = board(f"heartbeat-runs/{row['runId']}")
                if (detail.get("contextSnapshot") or {}).get("wakeReason") == "issue_disposition_repair":
                    return detail
        return None

    repair = wait_for(f"{adapter} native disposition repair", running_repair)
    action = wait_for("scheduled recovery action", lambda: (
        candidate if (candidate := board(path).get("activeRecoveryAction"))
        and (candidate.get("wakePolicy") or {}).get("scheduledRunId") == repair["id"] else None
    ))
    assert action["kind"] == "deliberate_wait_without_target"
    assert action["ownerType"] == "agent" and action["ownerAgentId"] == agent["id"]
    context = repair["contextSnapshot"]
    episode = context["legacyDispositionEpisode"]
    source_id = context["dispositionRepairSourceRunId"]
    source = board(f"heartbeat-runs/{source_id}")
    assert source["status"] == "succeeded" and source["agentId"] == agent["id"]
    assert episode["id"] == source_id
    assert episode["attempt"] == action["attemptCount"] == 1
    assert (action.get("wakePolicy") or {}).get("maxAttempts") == episode["maxAttempts"]
    assert action["maxAttempts"] >= episode["maxAttempts"] > episode["attempt"], (
        episode, action["attemptCount"], action["maxAttempts"]
    )

    before = {row["runId"] for row in board(f"{path}/runs")}
    board(f"{agent_path}/pause", {})

    def paused():
        current_agent = board(agent_path)
        current_run = board(f"heartbeat-runs/{repair['id']}")
        if (current_agent["status"] == "paused" and current_agent.get("lifecycleState") == "paused"
                and current_run["status"] == "cancelled"):
            return current_run
        return None

    cancelled = wait_for("pause and cancellation", paused, 30)
    assert cancelled["errorCode"] == "agent_paused"
    result = cancelled.get("resultJson") or {}
    assert (result.get("executionCancellation") or {}).get("state") == "acknowledged"
    if adapter == "hermes":
        assert result.get("conversationContinuation") == "continue_conversation_v1"
        paused_issue = board(path)
        assert paused_issue["status"] == "in_progress" and paused_issue["assigneeAgentId"] == agent["id"]
        assert (paused_issue.get("activeRecoveryAction") or {}).get("id") == action["id"]
    else:
        assert result.get("conversationContinuation") != "continue_conversation_v1"

    assert {row["runId"] for row in board(f"{path}/runs")} == before, "Dispatch occurred during pause"
    time.sleep(10)
    assert {row["runId"] for row in board(f"{path}/runs")} == before, "Dispatch occurred while paused"
    board(f"{agent_path}/resume", {})
    wait_for("agent resumed", lambda: board(agent_path).get("lifecycleState") == "ready", 30)

    if adapter == "process":
        # Watch beyond the one-minute second-attempt backoff and jitter.
        time.sleep(75)
        rows = board(f"{path}/runs")
        state = board(path)
        assert {row["runId"] for row in rows} == before, "Unknown process action was automatically retried"
        actions = board(f"{path}/recovery-actions")["actions"]
        held = [item for item in actions if item["kind"] == "active_run_watchdog"
                and item["ownerType"] == "board" and item["outcome"] == "blocked"
                and (item.get("evidence") or {}).get("runId") == repair["id"]]
        assert state["status"] == "blocked" and len(held) == 1, "Unknown process action was not board-held"
        assert held[0]["evidence"]["automaticRecovery"]["actionOutcome"] == "unknown"
        assert held[0]["evidence"]["automaticRecovery"]["replay"] == "blocked"
        print("PASS: unknown process outcome stayed board-held without automatic replay", flush=True)
        return

    def continuation():
        rows = [row for row in board(f"{path}/runs") if row["runId"] not in before]
        return rows if rows else None

    rows = wait_for("native same-owner continuation after resume", continuation, 45)
    assert len(rows) == 1 and rows[0]["agentId"] == agent["id"], rows
    resumed = board(f"heartbeat-runs/{rows[0]['runId']}")
    resumed_context = resumed["contextSnapshot"]
    assert resumed["invocationSource"] == "automation" and resumed["triggerDetail"] == "system"
    assert resumed_context["wakeReason"] == "issue_disposition_repair"
    assert resumed_context["recoveryActionId"] == action["id"]
    assert resumed_context["dispositionRepairSourceRunId"] == source_id
    assert resumed_context["dispositionRepairFingerprint"] == context["dispositionRepairFingerprint"]
    resumed_episode = resumed_context["legacyDispositionEpisode"]
    assert resumed_episode["id"] == episode["id"]
    assert resumed_episode["maxAttempts"] == episode["maxAttempts"]
    assert resumed_episode["attempt"] == episode["attempt"] + 1 <= episode["maxAttempts"]
    same_action = wait_for("same action reserved for the continuation", lambda: (
        candidate if (candidate := board(path).get("activeRecoveryAction"))
        and candidate["id"] == action["id"]
        and (candidate.get("wakePolicy") or {}).get("scheduledRunId") == resumed["id"] else None
    ))
    assert same_action["ownerAgentId"] == agent["id"]
    assert same_action["fingerprint"] == action["fingerprint"]
    assert same_action["attemptCount"] == resumed_episode["attempt"]
    assert same_action["maxAttempts"] == episode["maxAttempts"] <= action["maxAttempts"]
    # Attempt two has a one-minute native backoff plus jitter.
    wait_for("owner completion", lambda: board(path)["status"] == "done", 120)
    time.sleep(10)
    assert {row["runId"] for row in board(f"{path}/runs")} == before | {resumed["id"]}
    assert not board(path).get("activeRecoveryAction"), "Completed issue retained a recovery action"
    comments = board(f"{path}/comments")
    assert any(comment.get("body") == "The resumed owner completed its task."
               and comment.get("authorAgentId") == agent["id"]
               and comment.get("createdByRunId") == resumed["id"] for comment in comments), comments
    print("PASS: native resume continued exactly once under the same owner, source, episode and budget", flush=True)


def main(container):
    def board(path, body=None, method="POST"):
        return request(container, "director", path, body, method)[1]

    board("auth/sign-up/email", {"name": "director", "email": "director@fixture.invalid",
                                 "password": secrets.token_hex(16)})
    board("bootstrap/claim", {})
    company = board("companies", {"name": "Pause recovery fixture"})
    root = f"companies/{company['id']}"
    secret = board(f"{root}/secrets", {"name": "fixture-key", "value": "not-a-real-key"})
    subprocess.run(["docker", "exec", "-i", "-u", "node", container, "sh", "-ec",
                    "cat > /tmp/pause-recovery-fixture; chmod 700 /tmp/pause-recovery-fixture"],
                   input=SCRIPT, text=True, check=True, timeout=10)
    check_adapter(board, root, secret, "process")
    check_adapter(board, root, secret, "hermes")


if __name__ == "__main__":
    with fixture["authenticated_fixture"]("pause-recovery") as container:
        main(container)
