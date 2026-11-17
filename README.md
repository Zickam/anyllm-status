# ANY LLM availability

Read-only checks for the public website at https://any-llm.online/:

- Homepage and its JavaScript bundle load successfully.
- Chat page opens, allowing redirects within the same website.
- Public readiness endpoint returns `{"status":"ok"}`.
- HTTP and HTTPS versions of `any-llm.ru` and `www.any-llm.ru` redirect the same
  public path and query to the exact HTTPS URL on `any-llm.online`.

The standard GitHub-hosted runner checks every ten minutes. A failed check is
retried after fifteen seconds. GitHub may delay scheduled jobs; this is periodic
availability monitoring, not a guaranteed detection time.

Enable **Email → Only notify for failed workflows** in
[GitHub notification settings](https://github.com/settings/notifications).
Scheduled workflow notifications go to the user who configured its schedule.

On an outage, the workflow opens one issue assigned to `Zickam`; repeated failed
polls do not add comments or duplicate issues. A successful probe comments on
the open issue and closes it. The issue lists failed public checks and links to
the workflow run. The separate `--test` mode creates and immediately closes a
clearly labeled notification test issue.

`status.json` records the first result each UTC day and availability changes,
with a link to the corresponding run. It contains only the public site URL,
check time and success flag. Daily status commits also keep the repository active;
GitHub disables scheduled workflows in public repositories after 60 days without
repository activity.

To run locally, use `python3 check_site.py`. This includes all four production
redirect probes. An explicit `--base-url` checks only that site unless
`--redirect-origin` is also supplied. No extra packages are required.
