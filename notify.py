#!/usr/bin/env python3
"""Create and close one actionable GitHub issue for each availability outage."""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

OUTAGE_MARKER = "<!-- any-llm-availability-outage -->"
TEST_MARKER = "<!-- any-llm-availability-notification-test -->"
API = "https://api.github.com"


def github_request(method, path, token, payload=None):
    request = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response) if response.status != 204 else None
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise RuntimeError(f"GitHub API returned HTTP {status}") from None
    except urllib.error.URLError:
        raise RuntimeError("GitHub API request failed") from None


def repository_path(repository):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid GitHub repository name")
    return "/repos/" + repository


def issue_list(repository, token):
    issues = github_request(
        "GET", repository_path(repository) + "/issues?state=open&per_page=100", token
    )
    return [issue for issue in issues if "pull_request" not in issue]


def matching_issue(repository, token, marker):
    return next(
        (
            issue
            for issue in issue_list(repository, token)
            if marker in issue.get("body", "")
        ),
        None,
    )


def failed_checks(raw_results):
    try:
        results = json.loads(raw_results or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(results, list):
        return []
    failures = []
    for item in results:
        if not isinstance(item, dict) or item.get("ok") is not False:
            continue
        check = item.get("check")
        if not isinstance(check, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", check):
            check = "unknown_check"
        detail = check
        error_type = item.get("error_type")
        if isinstance(error_type, str) and re.fullmatch(r"[A-Za-z0-9_]+", error_type):
            detail += f" ({error_type})"
        status = item.get("http_status")
        if isinstance(status, int) and 100 <= status <= 599:
            detail += f" HTTP {status}"
        failures.append(detail)
    return failures


def outage_body(repository, run_url, raw_results):
    checks = failed_checks(raw_results)
    check_lines = "\n".join(f"- `{check}`" for check in checks) or "- Review the failed probe output in the workflow run."
    return (
        f"{OUTAGE_MARKER}\n\n"
        "Public availability probes failed for the Any LLM site.\n\n"
        f"Failed checks:\n{check_lines}\n\n"
        "Action: inspect the probe logs, then check public DNS, TLS, the site response, "
        "and the configured any-llm.ru redirect target as applicable. Resolve the cause "
        "and verify a successful probe before closing this issue.\n\n"
        f"Workflow run: {run_url}\n"
        f"Repository: {repository}\n"
    )


def notify(repository, token, probe_ok, run_url, raw_results=""):
    path = repository_path(repository)
    current = matching_issue(repository, token, OUTAGE_MARKER)
    if probe_ok:
        if current:
            issue_number = current["number"]
            github_request(
                "POST",
                f"{path}/issues/{issue_number}/comments",
                token,
                {"body": f"Availability probes recovered.\n\nWorkflow run: {run_url}"},
            )
            github_request(
                "PATCH", f"{path}/issues/{issue_number}", token, {"state": "closed"}
            )
            print("Closed the availability issue after a successful probe.")
        else:
            print("No open availability issue needs recovery.")
        return
    if current:
        print("An availability issue is already open.")
        return
    issue = github_request(
        "POST",
        path + "/issues",
        token,
        {
            "title": "[Availability] Public Any LLM site probe failed",
            "body": outage_body(repository, run_url, raw_results),
            "assignees": ["Zickam"],
        },
    )
    print(f"Created availability issue #{issue['number']} and assigned it to Zickam.")


def notification_test(repository, token):
    path = repository_path(repository)
    issue = github_request(
        "POST",
        path + "/issues",
        token,
        {
            "title": "[Availability test] GitHub issue notifications",
            "body": (
                f"{TEST_MARKER}\n\n"
                "Automated notification test. This issue is closed immediately after creation."
            ),
            "assignees": ["Zickam"],
        },
    )
    number = issue["number"]
    github_request(
        "POST",
        f"{path}/issues/{number}/comments",
        token,
        {"body": "Notification test completed; closing this test issue."},
    )
    github_request("PATCH", f"{path}/issues/{number}", token, {"state": "closed"})
    print(f"Created and closed notification test issue #{number}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true", help="Create and close a labeled test issue")
    args = parser.parse_args()
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required")
    if args.test:
        notification_test(repository, token)
        return
    probe_ok = os.environ.get("PROBE_OK") == "true"
    run_url = f"https://github.com/{repository}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    notify(repository, token, probe_ok, run_url, os.environ.get("PROBE_RESULTS", ""))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
