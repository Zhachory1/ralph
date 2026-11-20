"""Exercise native Council dependencies and natural-language handoffs on a disposable board.

The seats are scripted process fixtures with a preselected reaction: this verifies Paperclip
routing and attribution, not an LLM's interpretation of the comments. The dissent and
clarification cases choose the fixture reaction explicitly rather than parsing the comment.

check_review_recovery_wait covers a distinct native path through the same seats: a Manager
that opens its own human_only wake_assignee_on_accept confirmation directly off the native
review-stage wake (not a Council dependency), and the window where native recovery must not
mistake that completed, confirmation-bearing run for a stranded assignment. The Director's
own acceptance is a separate decision from the reviewer's conclusion above; it is resolved
through the board API only, never manufactured on the Manager's behalf.
"""
import json
import runpy
import secrets
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RFC = runpy.run_path(str(HERE / "paperclip-rfc-canary.py"))
STAGE = runpy.run_path(str(HERE / "paperclip-rfc-review-stage-canary.py"))
WAKEUP = runpy.run_path(str(HERE / "paperclip-wakeup-canary.py"))
request = RFC["request"]
fixture = RFC["authenticated_fixture"]

JUDGE = r"""
set -e
pc() {
  curl -fsS --max-time 10 -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
    -H "X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID" "$@"
}
issue=$(pc "$PAPERCLIP_API_URL/api/heartbeat-runs/$PAPERCLIP_RUN_ID" | jq -r '.issueId')
sleep 3
comment=COMMENT_LITERAL
title=$(pc "$PAPERCLIP_API_URL/api/issues/$issue" | jq -r '.title')
case "$title" in
  "Clarification fixture "*) comment="I can support the current revision after clarification." ;;
esac
payload=$(jq -nc --arg comment "$comment" '{status:"done",comment:$comment}')
pc --json "$payload" -X PATCH "$PAPERCLIP_API_URL/api/issues/$issue" >/dev/null
"""

MANAGER = r"""
set -e
pc() {
  curl -fsS --max-time 10 -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
    -H "X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID" "$@"
}
issue=$(pc "$PAPERCLIP_API_URL/api/heartbeat-runs/$PAPERCLIP_RUN_ID" | jq -r '.issueId')
marker="/tmp/council-deps-$issue"
if [ ! -f "$marker" ]; then
  revision=$(pc "$PAPERCLIP_API_URL/api/issues/$issue/documents/rfc" | jq -r '.latestRevisionId')
  for judge in JUDGES; do
    payload=$(jq -nc --arg issue "$issue" --arg judge "$judge" --arg revision "$revision" \
      '{title:("Council fixture "+$judge),
        description:("Review RFC "+$issue+" revision "+$revision+" and explain your conclusion."),
        status:"backlog",parentId:$issue,assigneeAgentId:$judge,
        idempotencyKey:("council:"+$issue+":"+$judge)}')
    created=$(pc --json "$payload" "$PAPERCLIP_API_URL/api/companies/$PAPERCLIP_COMPANY_ID/issues")
    printf '%s\n' "$created" | jq -r '.id' >> "$marker"
  done
  blockers=$(jq -R . < "$marker" | jq -s .)
  payload=$(jq -nc --argjson blockers "$blockers" '{blockedByIssueIds:$blockers}')
  updated=$(pc --json "$payload" -X PATCH "$PAPERCLIP_API_URL/api/issues/$issue")
  printf '%s' "$updated" | jq -e '.status == "in_review" and (.blockedByIssueIds|length)==3' >/dev/null
  pc --json '{"body":"Council reviews are requested; I am waiting for the linked tasks."}' \
    "$PAPERCLIP_API_URL/api/issues/$issue/comments" >/dev/null
  while IFS= read -r judge_issue; do
    pc --json '{"status":"todo"}' -X PATCH "$PAPERCLIP_API_URL/api/issues/$judge_issue" >/dev/null
  done < "$marker"
else
  while IFS= read -r judge_issue; do
    judge=$(pc "$PAPERCLIP_API_URL/api/issues/$judge_issue")
    printf '%s' "$judge" | jq -e '.status=="done"' >/dev/null
    author=$(printf '%s' "$judge" | jq -r '.assigneeAgentId')
    pc "$PAPERCLIP_API_URL/api/issues/$judge_issue/comments" | jq -e --arg author "$author" \
      'any(.[]; .authorAgentId==$author and (.body|length)>0)' >/dev/null
  done < "$marker"
  if [ DECISION_LITERAL = "clarify" ] && [ ! -f "$marker-clarification" ]; then
    pc "$PAPERCLIP_API_URL/api/issues/$issue/interactions" | jq -e 'length == 0' >/dev/null
    judge_issue=$(sed -n '2p' "$marker")
    judge=$(pc "$PAPERCLIP_API_URL/api/issues/$judge_issue" | jq -r '.assigneeAgentId')
    revision=$(pc "$PAPERCLIP_API_URL/api/issues/$issue/documents/rfc" | jq -r '.latestRevisionId')
    payload=$(jq -nc --arg issue "$issue" --arg judge "$judge" --arg revision "$revision" \
      '{title:("Clarification fixture "+$judge),
        description:("Please clarify your conclusion on RFC revision "+$revision+"."),
        status:"backlog",parentId:$issue,assigneeAgentId:$judge,
        idempotencyKey:("clarify:"+$issue+":"+$judge)}')
    clarification=$(pc --json "$payload" "$PAPERCLIP_API_URL/api/companies/$PAPERCLIP_COMPANY_ID/issues" | jq -r '.id')
    printf '%s\n' "$clarification" > "$marker-clarification"
    blockers=$(cat "$marker" "$marker-clarification" | jq -R . | jq -s .)
    payload=$(jq -nc --argjson blockers "$blockers" '{blockedByIssueIds:$blockers}')
    pc --json "$payload" -X PATCH "$PAPERCLIP_API_URL/api/issues/$issue" |
      jq -e '.status == "in_review" and (.blockedByIssueIds|length)==4' >/dev/null
    pc --json '{"body":"I requested clarification and will wait for the judge."}' \
      "$PAPERCLIP_API_URL/api/issues/$issue/comments" >/dev/null
    pc --json '{"status":"todo"}' -X PATCH "$PAPERCLIP_API_URL/api/issues/$clarification" >/dev/null
  elif [ DECISION_LITERAL = "revise" ]; then
    pc --json '{"status":"todo","comment":"Please address the security concern from Council."}' \
      -X PATCH "$PAPERCLIP_API_URL/api/issues/$issue" >/dev/null
  else
    if [ -f "$marker-clarification" ]; then
      clarification=$(cat "$marker-clarification")
      task=$(pc "$PAPERCLIP_API_URL/api/issues/$clarification")
      printf '%s' "$task" | jq -e '.status=="done"' >/dev/null
      author=$(printf '%s' "$task" | jq -r '.assigneeAgentId')
      pc "$PAPERCLIP_API_URL/api/issues/$clarification/comments" | jq -e --arg author "$author" \
        'any(.[]; .authorAgentId==$author and (.body|length)>0)' >/dev/null
    fi
    doc=$(pc "$PAPERCLIP_API_URL/api/issues/$issue/documents/rfc")
    pc --json '{"body":"Fixture decision brief: Council supports this revision."}' \
      "$PAPERCLIP_API_URL/api/issues/$issue/comments" >/dev/null
    payload=$(printf '%s' "$doc" | jq -c --arg issue "$issue" --arg human DIRECTOR \
      '{kind:"request_confirmation",resolverPolicy:"human_only",addresseeUserId:$human,
        continuationPolicy:"wake_assignee_on_accept",idempotencyKey:("approval:"+.latestRevisionId),
        payload:{version:1,prompt:"Approve this fixture RFC?",
          target:{type:"issue_document",issueId:$issue,key:"rfc",documentId:.id,
            revisionId:.latestRevisionId,revisionNumber:.latestRevisionNumber}}}')
    pc --json "$payload" "$PAPERCLIP_API_URL/api/issues/$issue/interactions" >/dev/null
  fi
fi
"""


RECOVERY_MANAGER = r"""
set -e
pc() {
  curl -fsS --max-time 10 -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
    -H "X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID" "$@"
}
run=$(pc "$PAPERCLIP_API_URL/api/heartbeat-runs/$PAPERCLIP_RUN_ID")
issue=$(printf '%s' "$run" | jq -r '.issueId')
kind=$(printf '%s' "$run" | jq -r '.contextSnapshot.interactionKind // empty')
accepted=$(printf '%s' "$run" | jq -r '.contextSnapshot.interactionStatus // empty')
confirmation=$(printf '%s' "$run" | jq -r '.contextSnapshot.interactionId // empty')
if [ "$kind" = "request_confirmation" ] && [ "$accepted" = "accepted" ] && [ -n "$confirmation" ]; then
  payload=$(jq -nc --arg parent "$issue" --arg conf "$confirmation" \
    --arg key "recovery-wait-ack:$confirmation" \
    '{title: ("Director approved " + $conf), status: "done", parentId: $parent, idempotencyKey: $key}')
  pc --json "$payload" "$PAPERCLIP_API_URL/api/companies/$PAPERCLIP_COMPANY_ID/issues" >/dev/null
else
  marker="/tmp/recovery-wait-$issue"
  if [ ! -f "$marker" ]; then
    doc=$(pc "$PAPERCLIP_API_URL/api/issues/$issue/documents/rfc")
    pc --json '{"body":"Fixture decision brief: the proposal is ready for a Director decision."}' \
      "$PAPERCLIP_API_URL/api/issues/$issue/comments" >/dev/null
    payload=$(printf '%s' "$doc" | jq -c --arg issue "$issue" --arg human DIRECTOR \
      '{kind:"request_confirmation",resolverPolicy:"human_only",addresseeUserId:$human,
        continuationPolicy:"wake_assignee_on_accept",idempotencyKey:("recovery-wait:"+.latestRevisionId),
        payload:{version:1,prompt:"Approve this exact RFC revision?",
          target:{type:"issue_document",issueId:$issue,key:"rfc",documentId:.id,
            revisionId:.latestRevisionId,revisionNumber:.latestRevisionNumber}}}')
    pc --json "$payload" "$PAPERCLIP_API_URL/api/issues/$issue/interactions" >/dev/null
    touch "$marker"
  fi
fi
"""


def check_council_comments(container, *, dissent=False, unclear=False):
    assert not (dissent and unclear)
    actual = subprocess.check_output(
        ["docker", "inspect", container, "--format", "{{.Config.Image}}"], text=True
    ).strip()
    assert actual == fixture.__wrapped__.__globals__["IMAGE"]

    def board(path, body=None, method="POST"):
        return request(container, "director", path, body, method)[1]

    board("auth/sign-up/email", {
        "name": "director", "email": "director@fixture.invalid", "password": secrets.token_hex(16),
    })
    board("bootstrap/claim", {})
    director = board("auth/get-session")["user"]["id"]
    company = board("companies", {"name": "Council dependency fixture"})
    root = f"companies/{company['id']}"
    opinions = (
        "The scope fits; I'm happy to proceed.",
        ("I see tradeoffs, but I cannot yet tell whether this is ready." if unclear
         else "Architecture looks sound. I approve the current revision."),
        ("I cannot support this yet: the authorization gap needs a fix." if dissent
         else "I found no security objection; proceed to the Director."),
    )
    judges = [
        board(f"{root}/agents", {
            "name": f"Judge {index}", "adapterType": "process",
            "adapterConfig": {
                "command": "/bin/sh", "args": ["-ec", JUDGE.replace("COMMENT_LITERAL", json.dumps(opinion))],
            },
        })
        for index, opinion in enumerate(opinions)
    ]
    script = MANAGER.replace("JUDGES", " ".join(judge["id"] for judge in judges)).replace(
        "DIRECTOR", json.dumps(director)
    ).replace("DECISION_LITERAL", json.dumps("revise" if dissent else "clarify" if unclear else "approve"))
    manager = board(f"{root}/agents", {
        "name": "Manager", "adapterType": "process",
        "adapterConfig": {"command": "/bin/sh", "args": ["-ec", script]},
        "runtimeConfig": {"heartbeat": {"maxConcurrentRuns": 1}},
    })
    author = board(f"{root}/agents", {
        "name": "Author", "adapterType": "process",
        "adapterConfig": {"command": "/bin/sh", "args": ["-ec", STAGE["AUTHOR_SCRIPT"]]},
    })
    for seat in [*judges, manager, author]:
        STAGE["wait_ready"](board, seat["id"])
    board(f"agents/{manager['id']}/permissions", {
        "canCreateAgents": False, "canCreateSkills": False, "canAssignTasks": True,
    }, "PATCH")
    parent = board(f"{root}/issues", {"title": "Root", "status": "backlog"})
    stage = {"type": "review", "participants": [{"type": "agent", "agentId": manager["id"]}]}
    issue = board(f"{root}/issues", {
        "title": "Fixture RFC", "status": "todo", "parentId": parent["id"],
        "assigneeAgentId": author["id"], "executionPolicy": {"stages": [stage]},
    })
    path = f"issues/{issue['id']}"
    deadline = time.monotonic() + 110
    while time.monotonic() < deadline:
        issue = board(path)
        cards = board(f"{path}/interactions")
        state = issue.get("executionState") or {}
        changed = dissent and state.get("lastDecisionOutcome") == "changes_requested"
        if cards or issue["status"] == "blocked" or changed:
            break
        time.sleep(1)
    if not cards and not (dissent and state.get("lastDecisionOutcome") == "changes_requested"):
        for run in board(f"{path}/runs")[:6]:
            detail = board(f"heartbeat-runs/{run['runId']}")
            print("Run:", detail["status"], detail.get("errorCode"), (detail.get("stdoutExcerpt") or "")[-1000:])
        raise AssertionError(f"No native confirmation; source status {issue['status']}")

    panel = [item for item in board(f"{root}/issues") if item["title"].startswith("Council fixture ")]
    clarifications = [item for item in board(f"{root}/issues") if item["title"].startswith("Clarification fixture ")]
    assert len(panel) == 3 and all(
        item["status"] == "done" and item["createdByAgentId"] == manager["id"] for item in panel
    )
    assert all(item["parentId"] == parent["id"] and item["privacyParentIssueId"] == issue["id"] for item in panel)
    assert len(clarifications) == (1 if unclear else 0), "An unclear conclusion needs its judge's clarification"
    assert {item["id"] for item in issue["blockedBy"]} == {item["id"] for item in [*panel, *clarifications]}
    document = board(f"{path}/documents/rfc")
    for task in panel:
        assert document["latestRevisionId"] in task["description"]
        comments = board(f"issues/{task['id']}/comments")
        assert any(comment.get("authorAgentId") == task["assigneeAgentId"] and comment["body"] in opinions
                   for comment in comments)
        assert board(f"issues/{task['id']}/documents") == []
    if dissent:
        assert not cards and issue["status"] == "in_progress" and issue["assigneeAgentId"] == author["id"]
        assert any(comment.get("authorAgentId") == manager["id"] and "security concern" in comment["body"]
                   for comment in board(f"{path}/comments"))
    else:
        assert issue["status"] == "in_review" and issue["assigneeAgentId"] == manager["id"]
        assert len(cards) == 1 and cards[0]["createdByAgentId"] == manager["id"] and cards[0]["status"] == "pending"
        assert cards[0]["resolverPolicy"] == "human_only" and cards[0]["addresseeUserId"] == director
        assert cards[0]["payload"]["target"]["revisionId"] == document["latestRevisionId"]
        assert any(comment.get("authorAgentId") == manager["id"] and "Fixture decision brief" in comment["body"]
                   for comment in board(f"{path}/comments"))
        if unclear:
            clarification = clarifications[0]
            assert clarification["status"] == "done" and clarification["assigneeAgentId"] == judges[1]["id"]
            assert clarification["createdByAgentId"] == manager["id"]
            assert document["latestRevisionId"] in clarification["description"]
            comments = board(f"issues/{clarification['id']}/comments")
            assert any(comment.get("authorAgentId") == judges[1]["id"]
                       and comment["body"] == "I can support the current revision after clarification."
                       for comment in comments)
            assert cards[0]["createdAt"] >= comments[0]["createdAt"]
    manager_runs = [
        board(f"heartbeat-runs/{run['runId']}")
        for run in board(f"{path}/runs") if run["agentId"] == manager["id"]
    ]
    reasons = [(run.get("contextSnapshot") or {}).get("wakeReason") for run in manager_runs]
    details = [(run["id"], run["status"], run.get("errorCode"), (run.get("stderrExcerpt") or "")[-300:])
               for run in manager_runs]
    assert reasons.count("issue_blockers_resolved") == (2 if unclear else 1), (reasons, details)
    print("PASS: attributed natural-language comments release the manager's native review dependency;",
          "dissent returns to the Author." if dissent else
          "clarification reaches the same judge before confirmation." if unclear else
          "support reaches a Director confirmation.")


def check_review_recovery_wait(container):
    """A Manager, woken directly by the native review stage, opens its own exact-revision
    confirmation. This probes Paperclip's native stranded-work recovery, not an LLM: a
    scripted Manager always reacts the same way, so any blocked status or duplicate run
    here is a native recovery defect, not a judgment call."""
    actual = subprocess.check_output(
        ["docker", "inspect", container, "--format", "{{.Config.Image}}"], text=True
    ).strip()
    assert actual == fixture.__wrapped__.__globals__["IMAGE"]

    def board(path, body=None, method="POST"):
        return request(container, "director", path, body, method)[1]

    board("auth/sign-up/email", {
        "name": "director", "email": "director@fixture.invalid", "password": secrets.token_hex(16),
    })
    board("bootstrap/claim", {})
    director = board("auth/get-session")["user"]["id"]
    company = board("companies", {"name": "Review recovery wait fixture"})
    root = f"companies/{company['id']}"

    manager = board(f"{root}/agents", {
        "name": "Manager", "adapterType": "process",
        "adapterConfig": {
            "command": "/bin/sh", "args": ["-ec", RECOVERY_MANAGER.replace("DIRECTOR", json.dumps(director))],
        },
    })
    author = board(f"{root}/agents", {
        "name": "Author", "adapterType": "process",
        "adapterConfig": {"command": "/bin/sh", "args": ["-ec", STAGE["AUTHOR_SCRIPT"]]},
    })
    for seat in (manager, author):
        STAGE["wait_ready"](board, seat["id"])
    stage = {"type": "review", "participants": [{"type": "agent", "agentId": manager["id"]}]}
    issue = board(f"{root}/issues", {
        "title": "Recovery-wait fixture RFC", "status": "todo",
        "assigneeAgentId": author["id"], "executionPolicy": {"stages": [stage]},
    })
    path = f"issues/{issue['id']}"

    author_run, author_detail = STAGE["wait_for_agent_run"](board, path, author["id"])
    # Paperclip may label this "cancelled" because the Author's own in_review PATCH
    # reassigned the issue away from it mid-flight; see paperclip-rfc-review-stage-canary.py.
    if author_run["status"] == "cancelled":
        assert author_detail.get("error") == "Cancelled before issue reassignment", author_detail.get("error")
    reviewed = STAGE["wait_for_status"](board, path, "in_review")
    assert reviewed["assigneeAgentId"] == manager["id"], "Native review stage did not reassign the issue to the Manager"

    manager_run, manager_detail = STAGE["wait_for_agent_run"](
        board, path, manager["id"], invocation_source="assignment"
    )
    trigger = ((manager_detail.get("contextSnapshot") or {}).get("executionContinuation") or {}).get("trigger") or {}
    assert trigger.get("reason") == "execution_review_requested", \
        "Expected the native review stage, not a recovery signal, to wake the Manager directly"
    print("PASS: the native review stage woke the Manager directly to open its own confirmation")

    document = board(f"{path}/documents/rfc")
    cards = board(f"{path}/interactions")
    assert len(cards) == 1, f"Expected exactly one native confirmation, found {len(cards)}"
    card = cards[0]
    assert card["kind"] == "request_confirmation" and card["status"] == "pending"
    assert card["resolverPolicy"] == "human_only" and card["addresseeUserId"] == director
    assert card["continuationPolicy"] == "wake_assignee_on_accept"
    assert card["createdByAgentId"] == manager["id"], "The confirmation must be the Manager's own act"
    assert card["sourceRunId"] == manager_run["runId"]
    target = card["payload"]["target"]
    assert target["type"] == "issue_document" and target["issueId"] == issue["id"] and target["key"] == "rfc"
    assert target["documentId"] == document["id"] and target["revisionId"] == document["latestRevisionId"]
    assert target["revisionNumber"] == document["latestRevisionNumber"]
    assert any(comment.get("authorAgentId") == manager["id"] and "Fixture decision brief" in comment["body"]
               for comment in board(f"{path}/comments"))

    # Watch the exact window where native stranded-work recovery must not mistake a
    # completed review-wake run -- one that already opened its own confirmation -- for an
    # abandoned assignment. A regression here sends the source to blocked, or retries the
    # Manager, despite the exact same pending confirmation still standing.
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        current = board(path)
        current_cards = board(f"{path}/interactions")
        extra_runs = [run for run in board(f"{path}/runs")
                      if run["agentId"] == manager["id"] and run["runId"] != manager_run["runId"]]
        if current["status"] == "blocked" or current.get("activeRecoveryAction") or extra_runs:
            extra_contexts = []
            for run in extra_runs:
                context = board(f"heartbeat-runs/{run['runId']}").get("contextSnapshot") or {}
                extra_contexts.append(
                    (run["runId"], run["status"], context.get("wakeReason"), context.get("retryReason"))
                )
            raise AssertionError(
                "Native recovery incorrectly treated the Manager's completed review-wake run as "
                f"stranded despite its exact pending confirmation {card['id']}: status={current['status']!r} "
                f"activeRecoveryAction={current.get('activeRecoveryAction')!r} "
                f"extra manager runs={extra_contexts}"
            )
        if current_cards != cards:
            raise AssertionError(f"The pending confirmation changed unexpectedly: {cards} -> {current_cards}")
        time.sleep(2.5)
    final = board(path)
    assert final["status"] == "in_review" and final["assigneeAgentId"] == manager["id"]
    assert not final.get("activeRecoveryAction")
    assert board(f"{path}/interactions") == cards
    print("PASS: the source stayed in_review, assigned to the Manager, with the same pending "
          "confirmation and no native recovery action or duplicate Manager run")

    before_accept = {run["runId"] for run in board(f"{path}/runs")}
    status, decision = request(container, "director", f"{path}/interactions/{card['id']}/accept", {})
    assert status == 200 and decision["status"] == "accepted", decision
    print("PASS: the Director resolved the synthetic card through the board API only")

    woken_run, _ = WAKEUP["wait_for_wake_run"](board, path, before_accept, card["id"])
    assert woken_run["agentId"] == manager["id"], "The wake invoked someone other than the Manager"
    all_wakes = [
        run for run in board(f"{path}/runs")
        if run["runId"] not in before_accept
        and (board(f"heartbeat-runs/{run['runId']}").get("contextSnapshot") or {}).get("interactionId") == card["id"]
    ]
    assert len(all_wakes) == 1, f"Expected exactly one native wake tied to this confirmation, found {len(all_wakes)}"
    print("PASS: exactly one native wake was tied to this exact confirmation")

    children = [item for item in board(f"{root}/issues") if item["parentId"] == issue["id"]]
    assert len(children) == 1, f"Expected exactly one observable Manager action, found {len(children)}"
    (action,) = children
    assert action["createdByAgentId"] == manager["id"], "An operator, not the Manager, produced this action"
    assert action["originRunId"] == woken_run["runId"], \
        "The observable action was not attributed to the Manager's own wake run"
    assert card["id"] in action["title"]
    print("PASS: the Director's acceptance woke the Manager under its own run, which alone "
          "produced the observable action, distinct from the reviewer's own earlier conclusion")


if __name__ == "__main__":
    for dissent, unclear in ((False, False), (True, False), (False, True)):
        with fixture("council-comments") as container:
            check_council_comments(container, dissent=dissent, unclear=unclear)
    with fixture("review-recovery-wait") as container:
        check_review_recovery_wait(container)
