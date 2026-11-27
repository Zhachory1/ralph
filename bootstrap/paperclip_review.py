"""Fail-closed policy and provenance checks for Paperclip PR review."""

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
import uuid
from urllib.parse import quote, urlsplit

if __package__:
    from .paperclip_client import DocumentNotFound, PaperclipClient, PermissionDenied, ProtocolError
else:
    from paperclip_client import DocumentNotFound, PaperclipClient, PermissionDenied, ProtocolError

class ReviewError(ValueError):
    """Review authority or policy could not be established."""


class PullRequestMetadataError(ReviewError):
    """One pull request lacks metadata needed for eligibility."""


class PinnedPullRequestError(ReviewError):
    """One pull request changed after discovery."""


class DispatchUnknown(ReviewError):
    """A task reservation has an unknown outcome."""


class SearchIncomplete(ReviewError):
    """A complete provider search could not be established."""


PAGE_SIZE = 100
MAX_PAGES = 10


def read_tool(client, connection, name, parameters):
    return client.call_tool(connection, name, parameters, str(uuid.uuid4()))[1]


def search_pages(client, connection, tool, query, max_pages=MAX_PAGES):
    items = []
    expected = None
    for page in range(1, max_pages + 1):
        try:
            result = read_tool(client, connection, tool,
                               {"query": query, "page": page, "perPage": PAGE_SIZE})
        except PermissionDenied:
            raise
        except ProtocolError:
            raise SearchIncomplete("Provider search unavailable") from None
        if (not isinstance(result, dict) or type(result.get("total_count")) is not int
                or result["total_count"] < 0 or result.get("incomplete_results") is not False
                or not isinstance(result.get("items"), list)
                or len(result["items"]) > PAGE_SIZE):
            raise SearchIncomplete("Incomplete search results")
        if expected is not None and expected != result["total_count"]:
            raise SearchIncomplete("Search changed during pagination")
        expected = result["total_count"]
        items.extend(result["items"])
        if len(items) > expected:
            raise SearchIncomplete("Invalid search count")
        if len(items) == expected:
            return items
        if len(result["items"]) < PAGE_SIZE:
            raise SearchIncomplete("Incomplete search results")
    raise SearchIncomplete("Search page limit reached")


def review_pages(client, connection, owner, repo, number, max_pages=MAX_PAGES):
    reviews = []
    for page in range(1, max_pages + 1):
        result = read_tool(client, connection, "pull_request_read", {
            "method": "get_reviews", "owner": owner, "repo": repo,
            "pullNumber": number, "page": page, "perPage": PAGE_SIZE,
        })
        if not isinstance(result, list) or len(result) > PAGE_SIZE:
            raise ReviewError("Incomplete review history")
        reviews.extend(result)
        if len(result) < PAGE_SIZE:
            return reviews
    raise ReviewError("Review history page limit reached")


def _string(value):
    return isinstance(value, str) and bool(value.strip())


def _strings(value):
    return isinstance(value, list) and all(_string(item) for item in value)


def validate_config(config):
    fields = {
        "companyId", "projectId", "routineAgentId", "expertAgentId", "reviewerAgentId",
        "gatewayConnectionId", "ledgerIssueId", "assignmentLogin", "publisherLogin",
    }
    required = fields | {"publisherAccountId", "authors", "repoFilter"}
    if not isinstance(config, dict) or not required <= config.keys() or config.keys() - (
            required | {"repoOverrides", "reviewHistoryUnfiltered"}):
        raise ReviewError("Invalid review configuration")
    if (not all(_string(config[key]) and config[key] == config[key].strip() for key in fields)
            or type(config["publisherAccountId"]) is not int or config["publisherAccountId"] < 1
            or not _strings(config["authors"]) or not _strings(config["repoFilter"])):
        raise ReviewError("Invalid review configuration")
    if any(item != item.strip() for item in config["authors"] + config["repoFilter"]):
        raise ReviewError("Invalid review configuration")
    if any(not re.fullmatch(r"[A-Za-z0-9-]+", login)
           for login in config["authors"] + [config["assignmentLogin"], config["publisherLogin"]]):
        raise ReviewError("Invalid review login")
    if type(config.get("reviewHistoryUnfiltered", False)) is not bool:
        raise ReviewError("Invalid review configuration")
    overrides = config.get("repoOverrides", {})
    if not isinstance(overrides, dict):
        raise ReviewError("Invalid review configuration")
    repos = config["repoFilter"] + list(overrides)
    if any(not _string(repo) or len(repo.split("/")) != 2
           or not all(_string(part) and part == part.strip() for part in repo.split("/"))
           for repo in repos):
        raise ReviewError("Invalid review configuration")
    if len({repo.casefold() for repo in overrides}) != len(overrides):
        raise ReviewError("Invalid review configuration")
    for route in overrides.values():
        if (not isinstance(route, dict) or not route
                or route.keys() - {"expertAgentId", "reviewerAgentId"}
                or not all(_string(value) and value == value.strip() for value in route.values())):
            raise ReviewError("Invalid review configuration")
    for repo in [""] + list(overrides):
        route = next((value for name, value in overrides.items()
                      if name.casefold() == repo.casefold()), {})
        if route.get("expertAgentId", config["expertAgentId"]) == route.get(
                "reviewerAgentId", config["reviewerAgentId"]):
            raise ReviewError("Expert and reviewer must differ")


def repository_from_search_item(item):
    if not isinstance(item, dict) or type(item.get("number")) is not int or item["number"] < 1:
        raise ReviewError("Invalid search item")
    pull = item.get("pull_request")
    if pull is not None and not isinstance(pull, dict):
        raise ReviewError("Invalid search item")
    urls = [item.get("html_url"), item.get("repository_url")]
    if pull:
        urls.extend((pull.get("html_url"), pull.get("url")))
    repos = []
    for value in urls:
        if value is None:
            continue
        if not isinstance(value, str):
            raise ReviewError("Invalid repository locator")
        url = urlsplit(value)
        if url.scheme != "https" or url.query or url.fragment or url.username or url.password:
            raise ReviewError("Invalid repository locator")
        if url.netloc == "github.com":
            match = re.fullmatch(r"/([^/]+)/([^/]+)/pull/([1-9][0-9]*)", url.path)
            if not match or int(match.group(3)) != item["number"]:
                raise ReviewError("Invalid repository locator")
        elif url.netloc == "api.github.com":
            match = re.fullmatch(r"/repos/([^/]+)/([^/]+)(?:/pulls/([1-9][0-9]*))?", url.path)
            if not match or (match.group(3) and int(match.group(3)) != item["number"]):
                raise ReviewError("Invalid repository locator")
        else:
            raise ReviewError("Invalid repository locator")
        if (not re.fullmatch(r"[A-Za-z0-9-]+", match.group(1))
                or not re.fullmatch(r"[A-Za-z0-9_.-]+", match.group(2))
                or match.group(2) in (".", "..")):
            raise ReviewError("Invalid repository locator")
        repos.append(match.group(1) + "/" + match.group(2))
    if not repos or len({repo.casefold() for repo in repos}) != 1:
        raise ReviewError("Missing or inconsistent repository locator")
    return repos[0]


def fetch_pr(client, connection, repository, number):
    owner, repo = repository.split("/")
    matches = [item for item in search_pages(client, connection, "search_repositories", repository)
               if isinstance(item, dict) and _string(item.get("full_name"))
               and item["full_name"].casefold() == repository.casefold()]
    if (len(matches) != 1 or type(matches[0].get("id")) is not int or matches[0]["id"] < 1):
        raise ReviewError("Repository identity not established")
    raw = read_tool(client, connection, "pull_request_read", {
        "method": "get", "owner": owner, "repo": repo, "pullNumber": number,
    })
    if (not isinstance(raw, dict) or raw.get("number") != number
            or not isinstance(raw.get("user"), dict) or not _string(raw["user"].get("login"))
            or not isinstance(raw.get("head"), dict) or not isinstance(raw.get("base"), dict)
            or not all(_string(raw[part].get(field)) for part in ("head", "base")
                       for field in ("sha", "ref"))):
        raise PullRequestMetadataError("Incomplete pull request metadata")
    pr = {"repo": matches[0]["full_name"], "repoId": matches[0]["id"], "number": number,
          "author": raw["user"]["login"], "authorId": raw["user"].get("id"),
          "assignees": raw.get("assignees"), "draft": raw.get("draft"),
          "state": raw.get("state"), "head": raw["head"]["sha"], "base": raw["base"]["sha"],
          "headRef": raw["head"]["ref"], "baseRef": raw["base"]["ref"]}
    if not _strings(pr["assignees"]) or type(pr["draft"]) is not bool or not _string(pr["state"]):
        raise PullRequestMetadataError("Incomplete pull request metadata")
    if pr["authorId"] is not None and (type(pr["authorId"]) is not int or pr["authorId"] < 1):
        raise PullRequestMetadataError("Invalid author identity")
    return pr


def classify_reviews(reviews, publisher_id, publisher_login, head, marker):
    found = []
    manual = False
    for review in reviews:
        if (not isinstance(review, dict) or not _string(review.get("state"))
                or not isinstance(review.get("user"), dict)
                or not _string(review["user"].get("login"))):
            raise ReviewError("Incomplete review history")
        user = review["user"]
        if (user["login"].casefold() == publisher_login.casefold()
                or user.get("id") == publisher_id):
            if type(user.get("id")) is not int or user["id"] != publisher_id:
                raise ReviewError("Review identity mismatch")
            if user["login"].casefold() != publisher_login.casefold():
                raise ReviewError("Review identity mismatch")
            if review["state"].upper() == "PENDING":
                return "conflict"
            if not _string(review.get("commit_id")):
                raise ReviewError("Review commit missing")
            if review["state"].upper() not in (
                    "APPROVED", "COMMENTED", "CHANGES_REQUESTED", "DISMISSED"):
                raise ReviewError("Unknown review state")
            if review.get("commit_id") == head and review["state"].upper() in (
                    "APPROVED", "COMMENTED", "CHANGES_REQUESTED", "DISMISSED"):
                if not _string(review.get("body")) or marker not in review["body"]:
                    manual = True
                else:
                    if type(review.get("id")) is not int or review["id"] < 1:
                        raise ReviewError("Review receipt missing ID")
                    found.append(review)
    if manual:
        return "manual"
    if len(found) > 1:
        raise ReviewError("Ambiguous review receipt")
    return found[0] if found else None


def render_summary(result, pr, publisher, publisher_id=None):
    if (not isinstance(result, dict) or result.get("verdict") not in ("APPROVE", "COMMENT")
            or not _string(result.get("coverage")) or not isinstance(result.get("findings"), list)
            or not isinstance(result.get("checks"), list) or not result["checks"]
            or not _string(result.get("reviewedHead")) or not _string(result.get("reviewedBase"))
            or result["reviewedHead"] != pr.get("head") or result["reviewedBase"] != pr.get("base")):
        raise ReviewError("Invalid review result")
    findings = {"blocking": [], "non-blocking": []}
    for finding in result["findings"]:
        if (not isinstance(finding, dict) or finding.get("classification") not in findings
                or not _string(finding.get("path")) or type(finding.get("line")) is not int
                or finding["line"] < 1 or not _string(finding.get("evidence"))
                or not _string(finding.get("correction"))):
            raise ReviewError("Invalid finding")
        path = finding["path"]
        if (path.startswith(("/", "\\")) or "\\" in path or ":" in path
                or any(part in ("", ".", "..") for part in path.split("/"))
                or any(ord(char) < 32 or ord(char) == 127 for char in path)):
            raise ReviewError("Invalid finding path")
        findings[finding["classification"]].append(
            "- {}:{} — {}. Correction: {}".format(finding["path"], finding["line"],
                                                  finding["evidence"], finding["correction"]))
    if findings["blocking"] and result["verdict"] == "APPROVE":
        raise ReviewError("Blocking findings cannot be approved")
    checks = []
    for check in result["checks"]:
        if (not isinstance(check, dict) or not _string(check.get("command"))
                or not _string(check.get("outcome"))):
            raise ReviewError("Invalid check evidence")
        outcome = check["outcome"]
        if outcome.strip().casefold().replace("_", " ") in ("not run", "skipped", "not executed"):
            if not _string(check.get("reason")):
                raise ReviewError("Missing reason for omitted check")
            outcome += ": " + check["reason"]
        checks.append("- `{}`: {}".format(check["command"], outcome))
    is_self = (pr["author"].casefold() == publisher.casefold()
               or (publisher_id is not None and pr.get("authorId") == publisher_id))
    event = "COMMENT" if is_self else result["verdict"]
    body = ("Automated review of {}#{} at head {} and base {}.\n"
            "Coverage: {}\n\n## Blocking findings\n{}\n\n"
            "## Non-blocking suggestions\n{}\n\n## Checks\n{}").format(
                pr["repo"], pr["number"], pr["head"], pr["base"], result["coverage"],
                "\n".join(findings["blocking"]) or "None",
                "\n".join(findings["non-blocking"]) or "None", "\n".join(checks))
    return event, body


def routine_requests(config, config_path="<private-config-path>"):
    validate_config(config)
    return {
        "routine": {
            "method": "POST",
            "path": "/api/companies/" + quote(config["companyId"], safe="") + "/routines",
            "body": {
                "projectId": config["projectId"],
                "title": "PR review discovery",
                "description": "Run `{} discover --config {}` in the native routine task. "
                               "Finish the scan with a complete or PARTIAL report; do not wait for reviews. "
                               "For blocked keys requiring human reconciliation, use the Paperclip skill to create "
                               "a separate native key-specific followup, not a child or blocker of this scan. "
                               "Do not reset "
                               "unknown intents or claim full completion from the CLI exit alone.".format(
                    _command(), shlex.quote(os.path.abspath(config_path))),
                "assigneeAgentId": config["routineAgentId"],
                "priority": "medium",
                "status": "paused",
                "concurrencyPolicy": "coalesce_if_active",
                "catchUpPolicy": "skip_missed",
                "activityGatePolicy": "always",
                "activityGateScope": "company",
            },
        },
        "trigger": {
            "method": "POST",
            "path": "/api/routines/{routineId}/triggers",
            "body": {"kind": "schedule", "cronExpression": "*/3 * * * *",
                     "timezone": "UTC", "enabled": False},
        },
    }


def validate_publisher_identity(identity, publisher_id, login):
    if (type(publisher_id) is not int or publisher_id < 1 or not _string(login)
            or not isinstance(identity, dict) or type(identity.get("id")) is not int
            or identity["id"] != publisher_id or not _string(identity.get("login"))
            or identity["login"].casefold() != login.casefold()):
        raise ReviewError("Publishing identity mismatch")


def validate_ledger_issue(issue, company_id, project_id):
    if (not _string(company_id) or not _string(project_id) or not isinstance(issue, dict)
            or issue.get("companyId") != company_id or issue.get("projectId") != project_id
            or "assigneeAgentId" not in issue or "assigneeUserId" not in issue
            or issue["assigneeAgentId"] is not None or issue["assigneeUserId"] is not None):
        raise ReviewError("Ledger issue must be local and unassigned")


def allowed_pr(policy, pr):
    if (not isinstance(policy, dict) or not _strings(policy.get("authors"))
            or not _strings(policy.get("repoFilter")) or not _string(policy.get("assignmentLogin"))):
        raise ReviewError("Invalid review policy")
    if (not isinstance(pr, dict) or not _string(pr.get("repo")) or not _string(pr.get("author"))
            or not _strings(pr.get("assignees")) or not isinstance(pr.get("draft"), bool)
            or not _string(pr.get("state"))):
        raise ReviewError("Incomplete pull request metadata")
    authors = {item.casefold() for item in policy["authors"]}
    repos = {item.casefold() for item in policy["repoFilter"]}
    assignees = {item.casefold() for item in pr["assignees"]}
    return (pr["author"].casefold() in authors
            and (not repos or pr["repo"].casefold() in repos)
            and policy["assignmentLogin"].casefold() in assignees
            and pr["state"].casefold() == "open" and not pr["draft"])


def validate_pinned_pr(policy, pinned, current):
    fields = ("repoId", "number", "head", "base", "headRef", "baseRef")
    if (not isinstance(pinned, dict) or not isinstance(current, dict)
            or not allowed_pr(policy, current) or not allowed_pr(policy, pinned)
            or pinned["repo"].casefold() != current["repo"].casefold()
            or pinned["author"].casefold() != current["author"].casefold()
            or pinned.get("authorId") != current.get("authorId")
            or any(pinned.get(field) != current.get(field) or pinned.get(field) is None
                   for field in fields)):
        raise PinnedPullRequestError("Pull request policy or pinned metadata changed")


def validate_run(run, run_id, company_id, agent_id, task_id, status):
    if not all(_string(item) for item in (run_id, company_id, agent_id, task_id, status)):
        raise ReviewError("Missing run provenance")
    if not isinstance(run, dict):
        raise ReviewError("Missing run provenance")
    context = run.get("contextSnapshot")
    if context is None:
        context = {}
    if not isinstance(context, dict):
        raise ReviewError("Invalid run context")
    binding = next((item for item in (
        run.get("nativeIssueId"), run.get("issueId"),
        context.get("issueId"), context.get("taskId")) if _string(item)), None)
    if (run.get("id") != run_id or run.get("companyId") != company_id
            or run.get("agentId") != agent_id or run.get("status") != status or binding != task_id):
        raise ReviewError("Run provenance mismatch")


def validate_result_revision(task, revision, run, company_id, parent_id, reviewer_id, key, revision_id):
    if not all(_string(item) for item in (company_id, parent_id, reviewer_id, key, revision_id)):
        raise ReviewError("Missing result provenance")
    if not isinstance(task, dict) or not isinstance(revision, dict):
        raise ReviewError("Missing result provenance")
    task_id = task.get("id")
    if (not _string(task_id) or task.get("companyId") != company_id
            or task.get("parentId") != parent_id or task.get("assigneeAgentId") != reviewer_id
            or task.get("assigneeUserId") is not None or task.get("status") != "done"):
        raise ReviewError("Reviewer task mismatch")
    if (revision.get("id") != revision_id or revision.get("issueId") != task_id
            or revision.get("companyId") != company_id or revision.get("key") != key
            or revision.get("isLatest") is not True or revision.get("latestRevisionId") != revision_id
            or revision.get("createdByAgentId") != reviewer_id
            or revision.get("createdByUserId") is not None):
        raise ReviewError("Review result revision mismatch")
    validate_run(run, revision.get("createdByRunId"), company_id, reviewer_id, task_id, "succeeded")


def publication_key(company_id, repo_id, pr_number, head, publisher_id):
    if type(repo_id) is int and repo_id > 0:
        repo_id = str(repo_id)
    if (not _string(company_id) or not _string(repo_id) or not _string(head)
            or type(pr_number) is not int or pr_number < 1
            or type(publisher_id) is not int or publisher_id < 1):
        raise ReviewError("Invalid publication identity")
    if repo_id.isascii() and repo_id.isdecimal():
        if int(repo_id) < 1:
            raise ReviewError("Invalid publication identity")
        repo_id = str(int(repo_id))
    payload = json.dumps(["v1", company_id, repo_id, pr_number, head, publisher_id],
                         separators=(",", ":"), ensure_ascii=True).encode()
    return "pr1-" + hashlib.sha256(payload).hexdigest()[:60]


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _document_key(prefix, key):
    return prefix + "-" + hashlib.sha256(key.encode()).hexdigest()[:63 - len(prefix)]


def _command():
    return shlex.quote(os.path.abspath(sys.executable)) + " " + shlex.quote(os.path.abspath(__file__))


def _analysis_key(pr, key):
    return _document_key("analysis", _encoded([key, pr["base"], pr["baseRef"], pr["headRef"]]))


class ReviewWorkflow:
    """Run a native task using only server-attributed plans, documents and gateway calls."""

    def __init__(self, client, config, context, config_path):
        validate_config(config)
        self.client, self.config, self.context = client, config, context
        self.config_path = os.path.abspath(config_path)
        self.connection = config["gatewayConnectionId"]
        if (not isinstance(context, dict) or context.get("companyId") != config["companyId"]
                or not all(_string(context.get(key)) for key in ("agentId", "runId", "taskId"))):
            raise ReviewError("Invalid native context")
        self._actor()
        validate_ledger_issue(client.get_issue(config["ledgerIssueId"]),
                              config["companyId"], config["projectId"])
        client.open_gateway(context["taskId"], config["projectId"])
        self._identity()

    def _actor(self):
        agent = self.client.get_me()
        task = self.client.get_issue(self.context["taskId"])
        if (not isinstance(agent, dict) or agent.get("id") != self.context["agentId"]
                or agent.get("companyId") != self.config["companyId"]
                or not isinstance(task, dict) or task.get("id") != self.context["taskId"]
                or task.get("companyId") != self.config["companyId"]
                or task.get("projectId") != self.config["projectId"]
                or task.get("assigneeAgentId") != self.context["agentId"]
                or task.get("assigneeUserId") is not None):
            raise ReviewError("Native actor or task mismatch")
        validate_run(self.client.get_run(self.context["runId"]), self.context["runId"],
                     self.config["companyId"], self.context["agentId"],
                     self.context["taskId"], "running")
        return task

    def _identity(self):
        validate_publisher_identity(read_tool(self.client, self.connection, "get_me", {}),
                                    self.config["publisherAccountId"], self.config["publisherLogin"])

    def _route(self, repo):
        route = next((value for name, value in self.config.get("repoOverrides", {}).items()
                      if name.casefold() == repo.casefold()), {})
        return {"expertAgentId": route.get("expertAgentId", self.config["expertAgentId"]),
                "reviewerAgentId": route.get("reviewerAgentId", self.config["reviewerAgentId"])}

    def _key(self, pr):
        return publication_key(self.config["companyId"], pr["repoId"], pr["number"],
                               pr["head"], self.config["publisherAccountId"])

    def _fresh_pr(self, pinned):
        pr = fetch_pr(self.client, self.connection, pinned["repo"], pinned["number"])
        validate_pinned_pr(self.config, pinned, pr)
        return pr

    def _plan(self, kind):
        task = self._actor()
        document = self.client.get_document(task["id"], "plan")
        revision = self.client.get_revision(task["id"], "plan", document["latestRevisionId"])
        try:
            plan = json.loads(document["body"], object_pairs_hook=_unique_object)
        except (ValueError, TypeError):
            raise ReviewError("Invalid task plan") from None
        if (not isinstance(plan, dict) or plan.get("kind") != kind
                or not isinstance(plan.get("pr"), dict)
                or plan.get("key") != self._key(plan["pr"])
                or plan.get("analysisKey") != _analysis_key(plan["pr"], plan["key"])
                or plan.get("route") != self._route(plan["pr"]["repo"])
                or revision.get("id") != document["latestRevisionId"]
                or revision.get("issueId") != task["id"] or revision.get("key") != "plan"
                or revision.get("companyId") != self.config["companyId"]
                or revision.get("isLatest") is not True
                or revision.get("latestRevisionId") != document["latestRevisionId"]
                or revision.get("body") != document["body"]
                or revision.get("createdByUserId") is not None
                or revision.get("createdByAgentId") != plan.get("creatorAgentId")
                or revision.get("createdByRunId") != plan.get("creatorRunId")):
            raise ReviewError("Task plan provenance mismatch")
        creator = self.client.get_run(plan.get("creatorRunId"))
        if not isinstance(creator, dict) or not _string(creator.get("status")):
            raise ReviewError("Plan creator run unavailable")
        validate_run(creator, plan["creatorRunId"], self.config["companyId"],
                     plan["creatorAgentId"], plan.get("creatorTaskId"), creator["status"])
        if kind == "expert":
            if (task.get("parentId") is not None or self.context["agentId"] != plan["route"]["expertAgentId"]
                    or plan["creatorAgentId"] != self.config["routineAgentId"]):
                raise ReviewError("Expert task mismatch")
        elif (task.get("parentId") != plan["creatorTaskId"]
              or self.context["agentId"] != plan["route"]["reviewerAgentId"]
              or plan["creatorAgentId"] != plan["route"]["expertAgentId"]):
            raise ReviewError("Reviewer task mismatch")
        return plan

    def _instructions(self, kind):
        command = _command()
        config = shlex.quote(self.config_path)
        if kind == "expert":
            return ("Run `{} delegate --config {}`; after the reviewer completes, "
                    "run `{} publish --config {}`. pending means wait for native continuation, "
                    "not done; submission_unknown or conflict means wait in the native task for human reconciliation, "
                    "not done. submitted_stale reports staleness, not current approval. "
                    "Only confirmed submitted or existing_review completes the task; never mark done from "
                    "a CLI exit alone. Never manually post, choose a new key, or merge.").format(
                        command, config, command, config)
        return ("Run `{} preflight --config {}` before reading PR content or checks. "
                "In a separate local checkout, verify and review the exact pinned head and base commits, "
                "not moving branches; run appropriate checks and record actual outcomes. "
                "Save JSON with your verified reviewedHead and reviewedBase, verdict APPROVE or COMMENT, "
                "coverage, findings "
                "(classification blocking or non-blocking, path, line, evidence, correction), and checks "
                "(command, outcome, reason when omitted). Run `{} record-result --config {} "
                "--result <json-file>` then complete your own task through the native lifecycle. "
                "Do not post a review or comment on GitHub.").format(command, config, command, config)

    def _brief(self, kind, pr, route):
        key = self._key(pr)
        return {"kind": kind, "pr": pr, "key": key, "analysisKey": _analysis_key(pr, key), "route": route,
                "creatorAgentId": self.context["agentId"], "creatorRunId": self.context["runId"],
                "creatorTaskId": self.context["taskId"], "instructions": self._instructions(kind)}

    def _create_task(self, plan, parent, agent):
        task = self.client.create_issue(self.config["companyId"], {
            "projectId": self.config["projectId"], "parentId": parent, "assigneeAgentId": agent,
            "title": "Review " + plan["pr"]["repo"] + "#" + str(plan["pr"]["number"]),
            "description": plan["instructions"], "initialPlan": _encoded(plan),
            "idempotencyKey": _document_key("task", plan["analysisKey"] + ":" + plan["kind"]),
        })
        if (not isinstance(task, dict) or not _string(task.get("id"))
                or task.get("companyId") != self.config["companyId"]
                or task.get("projectId") != self.config["projectId"]
                or task.get("parentId") != parent or task.get("assigneeAgentId") != agent):
            raise DispatchUnknown("Task creation outcome unknown")
        self._verify_task_plan(task["id"], plan, self.context["taskId"], exact=True)
        return task["id"]

    def _verify_task_plan(self, task_id, expected, creator_task, exact=False):
        document = self.client.get_document(task_id, "plan")
        revision = self.client.get_revision(task_id, "plan", document["latestRevisionId"])
        try:
            saved = json.loads(document["body"], object_pairs_hook=_unique_object)
        except (TypeError, ValueError):
            raise ReviewError("Invalid created task plan") from None
        if (not isinstance(saved, dict) or any(saved.get(field) != expected[field]
                for field in ("kind", "key", "analysisKey", "route", "instructions", "creatorAgentId"))
                or (exact and document["body"] != _encoded(expected))
                or saved.get("creatorTaskId") != creator_task
                or revision.get("id") != document["latestRevisionId"]
                or revision.get("issueId") != task_id or revision.get("companyId") != self.config["companyId"]
                or revision.get("key") != "plan" or revision.get("isLatest") is not True
                or revision.get("latestRevisionId") != document["latestRevisionId"]
                or revision.get("body") != document["body"]
                or revision.get("createdByAgentId") != saved["creatorAgentId"]
                or revision.get("createdByUserId") is not None
                or revision.get("createdByRunId") != saved.get("creatorRunId")):
            raise ReviewError("Created task plan mismatch")
        validate_pinned_pr(self.config, saved.get("pr"), expected["pr"])
        run = self.client.get_run(saved.get("creatorRunId"))
        if not isinstance(run, dict) or not _string(run.get("status")):
            raise ReviewError("Task plan creator run unavailable")
        validate_run(run, saved["creatorRunId"], self.config["companyId"], saved["creatorAgentId"],
                     creator_task, run["status"])

    def _reserve_task(self, key, plan, parent, agent):
        ledger = self.config["ledgerIssueId"]
        name = _document_key(plan["kind"], key)
        try:
            doc = self.client.get_document(ledger, name)
        except DocumentNotFound:
            reservation = {"key": key, "nonce": str(uuid.uuid4()), "kind": plan["kind"],
                           "ownerTaskId": self.context["taskId"]}
            doc = self.client.create_document(ledger, name, _encoded(reservation))
            task_id = self._create_task(plan, parent, agent)
            reservation["taskId"] = task_id
            self.client.update_document(ledger, name, _encoded(reservation), doc["latestRevisionId"])
            return task_id
        reservation = self._ledger_body(name, doc, self.context["agentId"])
        if reservation.get("key") != key or reservation.get("kind") != plan["kind"]:
            raise ReviewError("Task reservation mismatch")
        if not _string(reservation.get("taskId")):
            raise DispatchUnknown("Task dispatch outcome unknown")
        if parent is not None and reservation.get("ownerTaskId") != parent:
            raise ReviewError("Task reservation owner mismatch")
        task = self.client.get_issue(reservation["taskId"])
        if (task.get("parentId") != parent or task.get("assigneeAgentId") != agent
                or task.get("companyId") != self.config["companyId"]
                or task.get("projectId") != self.config["projectId"]):
            raise ReviewError("Task reservation mismatch")
        self._verify_task_plan(task["id"], plan, reservation.get("ownerTaskId"))
        return reservation["taskId"]

    def _ledger_body(self, key, doc, expected_agent):
        revision = self.client.get_revision(self.config["ledgerIssueId"], key, doc["latestRevisionId"])
        if (not isinstance(revision, dict) or revision.get("id") != doc["latestRevisionId"]
                or revision.get("issueId") != self.config["ledgerIssueId"]
                or revision.get("companyId") != self.config["companyId"] or revision.get("key") != key
                or revision.get("isLatest") is not True
                or revision.get("latestRevisionId") != doc["latestRevisionId"]
                or revision.get("body") != doc["body"] or revision.get("createdByUserId") is not None
                or revision.get("createdByAgentId") != expected_agent):
            raise ReviewError("Ledger provenance mismatch")
        try:
            data = json.loads(doc["body"], object_pairs_hook=_unique_object)
        except (ValueError, TypeError):
            raise ReviewError("Invalid ledger document") from None
        if not isinstance(data, dict):
            raise ReviewError("Invalid ledger document")
        run = self.client.get_run(revision.get("createdByRunId"))
        if not isinstance(run, dict) or not _string(run.get("status")):
            raise ReviewError("Ledger writer run unavailable")
        validate_run(run, revision.get("createdByRunId"), self.config["companyId"], expected_agent,
                     data.get("ownerTaskId"), run["status"])
        return data

    def discover(self):
        if self.context["agentId"] != self.config["routineAgentId"]:
            raise ReviewError("Discovery requires routine actor")
        if not self.config["authors"]:
            return []
        query = "is:pr is:open assignee:" + self.config["assignmentLogin"]
        candidates = search_pages(self.client, self.connection, "search_pull_requests", query)
        seen, eligible, blocked = set(), [], []
        for item in candidates:
            repo = None
            number = item.get("number") if isinstance(item, dict) else None
            try:
                repo = repository_from_search_item(item)
            except ReviewError:
                blocked.append({"repo": None, "number": number, "head": None, "key": None,
                                "error": "metadata unavailable"})
                continue
            identity = (repo.casefold(), item["number"])
            if identity in seen:
                raise ReviewError("Duplicate search result")
            seen.add(identity)
            if (self.config["repoFilter"] and repo.casefold() not in
                    {value.casefold() for value in self.config["repoFilter"]}):
                continue
            try:
                pr = fetch_pr(self.client, self.connection, repo, item["number"])
                if not allowed_pr(self.config, pr):
                    continue
                route = self._route(pr["repo"])
            except (PermissionDenied, SearchIncomplete):
                raise
            except (ReviewError, ProtocolError):
                blocked.append({"repo": repo, "number": item["number"], "head": None, "key": None,
                                "error": "metadata unavailable"})
                continue
            eligible.append((pr, route))
        dispatched = []
        for pr, route in eligible:
            self._actor()
            plan = None
            reason = "metadata changed"
            try:
                plan = self._brief("expert", pr, route)
                self._fresh_pr(pr)
                reason = "history unavailable"
                try:
                    self.client.get_document(self.config["ledgerIssueId"], _document_key("send", plan["key"]))
                except DocumentNotFound:
                    pass
                else:
                    continue
                history = self._reviews(pr, "\x00")
                if history == "conflict":
                    reason = "review conflict"
                    raise ReviewError("Pending review conflict")
                if history is not None:
                    continue
                reason = "dispatch outcome unknown"
                dispatched.append(self._reserve_task(plan["analysisKey"], plan, None, route["expertAgentId"]))
            except (PermissionDenied, SearchIncomplete):
                raise
            except (ReviewError, ProtocolError):
                blocked.append({"repo": pr["repo"], "number": pr["number"], "head": pr["head"],
                                "key": plan["key"] if plan else None, "error": reason})
        if blocked:
            return {"status": "partial", "dispatched": dispatched, "blocked": blocked}
        return dispatched

    def delegate(self):
        plan = self._plan("expert")
        self._fresh_pr(plan["pr"])
        self._identity()
        task = self._actor()
        blockers = []
        relations = [task[key] for key in ("blockedBy", "blockedByIssueIds") if key in task]
        if not relations:
            raise ReviewError("Missing dependency relations")
        for relation in relations:
            if not isinstance(relation, list):
                raise ReviewError("Invalid dependency relations")
            for item in relation:
                identifier = item.get("id", item.get("issueId")) if isinstance(item, dict) else item
                if not _string(identifier):
                    raise ReviewError("Invalid dependency relations")
                if identifier not in blockers:
                    blockers.append(identifier)
        child = self._brief("reviewer", plan["pr"], plan["route"])
        task_id = self._reserve_task(plan["analysisKey"], child, self.context["taskId"],
                                     plan["route"]["reviewerAgentId"])
        if task_id not in blockers:
            blockers.append(task_id)
        self.client.update_issue(self.context["taskId"], {"blockedByIssueIds": blockers})
        return task_id

    def preflight(self):
        plan = self._plan("reviewer")
        self._identity()
        return self._fresh_pr(plan["pr"])

    def record_result(self, result):
        plan = self._plan("reviewer")
        self._identity()
        current = self._fresh_pr(plan["pr"])
        render_summary(result, current, self.config["publisherLogin"], self.config["publisherAccountId"])
        body = _encoded({"key": plan["key"], "pr": plan["pr"], "result": result})
        try:
            existing = self.client.get_document(self.context["taskId"], "review-result")
        except DocumentNotFound:
            doc = self.client.create_document(self.context["taskId"], "review-result", body)
        else:
            doc = self.client.update_document(self.context["taskId"], "review-result",
                                              body, existing["latestRevisionId"])
        return {"revisionId": doc["latestRevisionId"]}

    def _result(self, plan):
        name = _document_key("reviewer", plan["analysisKey"])
        doc = self.client.get_document(self.config["ledgerIssueId"], name)
        link = self._ledger_body(name, doc, self.context["agentId"])
        if (link.get("key") != plan["analysisKey"] or link.get("kind") != "reviewer"
                or link.get("ownerTaskId") != self.context["taskId"]
                or not _string(link.get("taskId"))):
            raise ReviewError("Reviewer link mismatch")
        task = self.client.get_issue(link["taskId"])
        if task.get("status") != "done":
            return None
        if task.get("projectId") != self.config["projectId"]:
            raise ReviewError("Reviewer project mismatch")
        doc = self.client.get_document(task["id"], "review-result")
        revision = self.client.get_revision(task["id"], "review-result", doc["latestRevisionId"])
        run = self.client.get_run(revision.get("createdByRunId"))
        if isinstance(run, dict) and run.get("status") == "running":
            return None
        validate_result_revision(task, revision, run, self.config["companyId"], self.context["taskId"],
                                 plan["route"]["reviewerAgentId"], "review-result",
                                 doc["latestRevisionId"])
        if revision.get("body") != doc["body"]:
            raise ReviewError("Result body provenance mismatch")
        try:
            result = json.loads(doc["body"], object_pairs_hook=_unique_object)
        except (ValueError, TypeError):
            raise ReviewError("Invalid result document") from None
        if (not isinstance(result, dict) or result.get("key") != plan["key"]
                or result.get("pr") != plan["pr"]):
            raise ReviewError("Result pin mismatch")
        return result.get("result"), doc["latestRevisionId"]

    def _reviews(self, pr, marker):
        owner, repo = pr["repo"].split("/")
        reviews = review_pages(self.client, self.connection, owner, repo, pr["number"])
        return classify_reviews(reviews, self.config["publisherAccountId"],
                                self.config["publisherLogin"], pr["head"], marker)

    def _intent(self, plan, result, revision):
        event, summary = render_summary(result, plan["pr"], self.config["publisherLogin"],
                                        self.config["publisherAccountId"])
        owner, repo = plan["pr"]["repo"].split("/")
        payload = {"method": "create", "owner": owner, "repo": repo, "pullNumber": plan["pr"]["number"],
                   "commitID": plan["pr"]["head"], "event": event, "body": summary}
        digest = hashlib.sha256(_encoded(payload).encode()).hexdigest()
        marker = "<!-- ralph-review {} {} -->".format(plan["key"], digest)
        payload["body"] += "\n\n" + marker
        payload_hash = hashlib.sha256(_encoded(payload).encode()).hexdigest()
        return {"key": plan["key"], "pr": plan["pr"], "resultRevisionId": revision,
                "payload": payload, "payloadHash": payload_hash, "marker": marker,
                "ownerAgentId": self.context["agentId"], "ownerTaskId": self.context["taskId"],
                "ownerRunId": self.context["runId"], "nonce": str(uuid.uuid4())}

    def _validate_intent(self, plan, saved):
        fields = {"key", "pr", "resultRevisionId", "payload", "payloadHash", "marker",
                  "ownerAgentId", "ownerTaskId", "ownerRunId", "nonce"}
        if (not isinstance(saved, dict) or set(saved) != fields
                or saved["key"] != plan["key"] or saved["pr"] != plan["pr"]
                or saved["ownerAgentId"] != self.context["agentId"]
                or saved["ownerTaskId"] != self.context["taskId"]
                or not all(_string(saved[field]) for field in ("ownerRunId", "resultRevisionId", "nonce"))
                or not isinstance(saved["payload"], dict)):
            raise ReviewError("Invalid send intent")
        try:
            nonce = uuid.UUID(saved["nonce"])
        except (ValueError, TypeError, AttributeError):
            raise ReviewError("Invalid send nonce") from None
        if nonce.version != 4 or str(nonce) != saved["nonce"]:
            raise ReviewError("Invalid send nonce")
        payload = saved["payload"]
        owner, repo = plan["pr"]["repo"].split("/")
        if (set(payload) != {"method", "owner", "repo", "pullNumber", "commitID", "event", "body"}
                or payload["method"] != "create" or payload["owner"] != owner or payload["repo"] != repo
                or payload["pullNumber"] != plan["pr"]["number"] or payload["commitID"] != plan["pr"]["head"]
                or payload["event"] not in ("APPROVE", "COMMENT") or not _string(payload["body"])
                or not _string(saved["marker"])
                or not payload["body"].endswith("\n\n" + saved["marker"])):
            raise ReviewError("Invalid send payload")
        if (payload["event"] == "APPROVE" and
                (plan["pr"]["author"].casefold() == self.config["publisherLogin"].casefold()
                 or plan["pr"].get("authorId") == self.config["publisherAccountId"])):
            raise ReviewError("Invalid self approval")
        unmarked = dict(payload, body=payload["body"][:-(len(saved["marker"]) + 2)])
        digest = hashlib.sha256(_encoded(unmarked).encode()).hexdigest()
        if (saved["marker"] != "<!-- ralph-review {} {} -->".format(plan["key"], digest)
                or saved["payloadHash"] != hashlib.sha256(_encoded(payload).encode()).hexdigest()):
            raise ReviewError("Send digest mismatch")

    def _audit(self, intent):
        result = self.client.get_decisions(self.config["companyId"], intent["ownerRunId"])
        if not isinstance(result, dict) or result.get("runId") != intent["ownerRunId"]:
            return None
        if not isinstance(result.get("decisions"), list):
            return None
        alias = self.client.tool_name(self.connection, "pull_request_review_write")
        matches = []
        for item in result["decisions"]:
            invocation = item.get("invocation") if isinstance(item, dict) else None
            if (isinstance(invocation, dict)
                    and invocation.get("companyId") == self.config["companyId"]
                    and invocation.get("idempotencyKey") == intent["key"]
                    and invocation.get("agentId") == intent["ownerAgentId"]
                    and invocation.get("issueId") == intent["ownerTaskId"]
                    and invocation.get("runId") == intent["ownerRunId"]
                    and invocation.get("connectionId") == self.connection
                    and invocation.get("toolName") == alias
                    and invocation.get("status") in ("succeeded", "failed", "timed_out", "executing")
                    and _string(invocation.get("startedAt")) and _string(invocation.get("id"))):
                matches.append(invocation["id"])
        return matches[0] if len(matches) == 1 else None

    def _receipt_review(self, intent):
        pr = intent["pr"]
        owner, repo = pr["repo"].split("/")
        reviews = review_pages(self.client, self.connection, owner, repo, pr["number"])
        state = "APPROVED" if intent["payload"]["event"] == "APPROVE" else "COMMENTED"
        found = []
        for review in reviews:
            if (not isinstance(review, dict) or not isinstance(review.get("user"), dict)
                    or not _string(review["user"].get("login")) or not _string(review.get("state"))):
                raise ReviewError("Incomplete review history")
            if (review["state"].upper() != state or review.get("commit_id") != pr["head"]
                    or review.get("body") != intent["payload"]["body"]):
                continue
            user = review["user"]
            if (user["login"].casefold() != self.config["publisherLogin"].casefold()
                    and user.get("id") != self.config["publisherAccountId"]):
                continue
            if (user["login"].casefold() != self.config["publisherLogin"].casefold()
                    or type(user.get("id")) is not int or user["id"] != self.config["publisherAccountId"]
                    or type(review.get("id")) is not int or review["id"] < 1):
                raise ReviewError("Review receipt identity mismatch")
            found.append(review)
        if len(found) > 1:
            raise ReviewError("Ambiguous review receipt")
        return found[0] if found else None

    def _reconcile(self, intent, plan):
        unknown = {"status": "submission_unknown"}
        try:
            review = self._receipt_review(intent)
            invocation = self._audit(intent)
            if (not isinstance(review, dict) or not invocation
                    or review.get("body") != intent["payload"]["body"]
                    or review.get("state", "").upper() != (
                        "APPROVED" if intent["payload"]["event"] == "APPROVE" else "COMMENTED")):
                return unknown
            url = "https://github.com/{}/pull/{}#pullrequestreview-{}".format(
                intent["pr"]["repo"], intent["pr"]["number"], review["id"])
            receipt = {"key": intent["key"], "reviewId": review["id"], "url": url,
                       "invocationId": invocation, "payloadHash": intent["payloadHash"],
                       "resultRevisionId": intent["resultRevisionId"],
                       "ownerTaskId": intent["ownerTaskId"]}
            name = _document_key("receipt", intent["key"])
            try:
                existing = self.client.get_document(self.config["ledgerIssueId"], name)
            except DocumentNotFound:
                self.client.create_document(self.config["ledgerIssueId"], name, _encoded(receipt))
            else:
                stored = self._ledger_body(name, existing, intent["ownerAgentId"])
                if stored != receipt:
                    return unknown
            try:
                self._actor()
                self._identity()
                self._fresh_pr(intent["pr"])
                current_result = self._result(plan)
                if current_result is None or current_result[1] != intent["resultRevisionId"]:
                    raise ReviewError("Result revision changed after submission")
            except (ReviewError, ProtocolError, KeyError, TypeError):
                return {"status": "submitted_stale", "url": url}
            return {"status": "submitted", "url": url}
        except (ReviewError, ProtocolError, KeyError, TypeError):
            return unknown

    def publish(self):
        plan = self._plan("expert")
        if self.config.get("reviewHistoryUnfiltered", False) is not True:
            raise ReviewError("Unfiltered review history is not qualified")
        ledger, name = self.config["ledgerIssueId"], _document_key("send", plan["key"])
        try:
            existing = self.client.get_document(ledger, name)
        except DocumentNotFound:
            existing = None
        if existing is not None:
            saved = self._ledger_body(name, existing, self.context["agentId"])
            try:
                self._validate_intent(plan, saved)
            except (ReviewError, KeyError, TypeError):
                return {"status": "submission_unknown"}
            revision = self.client.get_revision(ledger, name, existing["latestRevisionId"])
            if revision.get("createdByRunId") != saved["ownerRunId"]:
                return {"status": "submission_unknown"}
            return self._reconcile(saved, plan)
        self._actor()
        self._identity()
        self._fresh_pr(plan["pr"])
        result = self._result(plan)
        if result is None:
            return {"status": "pending"}
        intent = self._intent(plan, *result)
        prior = self._reviews(plan["pr"], intent["marker"])
        if prior == "conflict":
            return {"status": "conflict"}
        if prior is not None:
            return {"status": "existing_review"}
        try:
            self.client.create_document(ledger, name, _encoded(intent))
        except ProtocolError:
            return {"status": "submission_unknown"}
        try:
            self._actor()
            self._identity()
            validate_ledger_issue(self.client.get_issue(ledger), self.config["companyId"],
                                  self.config["projectId"])
            self._fresh_pr(plan["pr"])
            if self._result(plan) != result:
                return {"status": "submission_unknown"}
            if self._reviews(plan["pr"], intent["marker"]) is not None:
                return {"status": "submission_unknown"}
            self._actor()
            self._identity()
            self._fresh_pr(plan["pr"])
            self.client.call_tool(self.connection, "pull_request_review_write",
                                  intent["payload"], intent["key"])
        except (ProtocolError, ReviewError):
            return self._reconcile(intent, plan)
        return self._reconcile(intent, plan)

def main(argv=None):
    parser = argparse.ArgumentParser(description="Native Paperclip assigned PR review helper.")
    parser.add_argument("command", choices=("routine", "discover", "delegate", "preflight",
                                            "record-result", "publish"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--result")
    args = parser.parse_args(argv)
    try:
        with open(args.config, encoding="utf-8") as config_file:
            config = json.load(config_file, object_pairs_hook=_unique_object)
        if args.command == "routine":
            output = routine_requests(config, args.config)
        else:
            if (args.command == "record-result") != bool(args.result):
                raise ReviewError("Result file is required only for record-result")
            context = {"companyId": os.environ["PAPERCLIP_COMPANY_ID"],
                       "agentId": os.environ["PAPERCLIP_AGENT_ID"],
                       "runId": os.environ["PAPERCLIP_RUN_ID"],
                       "taskId": os.environ["PAPERCLIP_TASK_ID"]}
            client = PaperclipClient(os.environ["PAPERCLIP_API_URL"], os.environ["PAPERCLIP_API_KEY"],
                                     context["runId"])
            workflow = ReviewWorkflow(client, config, context, os.path.abspath(args.config))
            if args.command == "record-result":
                with open(args.result, encoding="utf-8") as result_file:
                    result = json.load(result_file, object_pairs_hook=_unique_object)
                output = workflow.record_result(result)
            else:
                output = getattr(workflow, args.command)()
        print(json.dumps(output, indent=2))
        if args.command == "publish" and output.get("status") not in ("submitted", "existing_review"):
            return 2
        if args.command == "discover" and isinstance(output, dict) and output.get("status") == "partial":
            return 2
    except (OSError, ValueError, KeyError, TypeError, ProtocolError):
        print("Review helper refused: invalid authority, policy, input, or unknown outcome", file=sys.stderr)
        return 2
    return 0


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReviewError("Duplicate configuration key")
        result[key] = value
    return result


if __name__ == "__main__":
    sys.exit(main())
