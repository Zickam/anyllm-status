# ANY LLM availability

Read-only checks for the public website at https://any-llm.online/:

- Homepage and its JavaScript bundle load successfully.
- Chat page opens, allowing redirects within the same website.
- Public readiness endpoint returns `{"status":"ok"}`.

The standard GitHub-hosted runner checks every ten minutes. A failed check is
retried after fifteen seconds. GitHub may delay scheduled jobs; this is periodic
availability monitoring, not a guaranteed detection time.

Enable **Email → Only notify for failed workflows** in
[GitHub notification settings](https://github.com/settings/notifications).
Scheduled workflow notifications go to the user who configured its schedule.

`status.json` records the first result each UTC day and availability changes,
with a link to the corresponding run. It contains only the public site URL,
check time and success flag. Daily status commits also keep the repository active;
GitHub disables scheduled workflows in public repositories after 60 days without
repository activity.

To run locally, use `python3 check_site.py`. No extra packages are required.
