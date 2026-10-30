#!/usr/bin/env python3
"""Keep a daily public status record and record availability changes."""

import base64
import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime


def main():
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    url = f"https://api.github.com/repos/{repository}/contents/status.json"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    previous, sha = {}, None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:
            response = json.load(r)
            sha = response["sha"]
            previous = json.loads(base64.b64decode(response["content"]))
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    now = datetime.now(UTC)
    status = {
        "checked_at": now.isoformat(),
        "site": "https://any-llm.online/",
        "ok": os.environ.get("PROBE_OK") == "true",
        "run": f"https://github.com/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
    }
    if (
        previous.get("checked_at", "")[:10] == now.date().isoformat()
        and previous.get("ok") == status["ok"]
    ):
        print("Today's availability state is already recorded.")
        return
    body = {
        "message": "chore(status): record availability "
        + ("recovery" if status["ok"] else "failure"),
        "content": base64.b64encode((json.dumps(status, indent=2) + "\n").encode()).decode(),
    }
    if sha:
        body["sha"] = sha
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="PUT"
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status not in (200, 201):
                raise RuntimeError("Status update was not accepted")
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Status update failed: HTTP {error.code}") from None
    print("Daily availability state recorded.")


if __name__ == "__main__":
    main()
