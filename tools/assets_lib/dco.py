"""The Developer Certificate of Origin check (design D8, ACON-008).

Every non-merge commit of a pull request must carry a `Signed-off-by: Name <email>` line whose
email matches the commit's author email, ignoring case: the semantics of the DCO app, without
the app. The commits come from the REST API, so nothing of the pull request is run or checked out.
"""

from __future__ import annotations

import re
from typing import Any

MAX_COMMITS = 250
_SIGNOFF = re.compile(r"^\s*signed-off-by:\s*(?P<name>.*?)\s*<(?P<email>[^<>\s]+)>\s*$", re.IGNORECASE)
FIX = "git commit --amend -s"
FIX_MANY = "git rebase --signoff <base branch>"


def signoff_emails(message: str) -> list[str]:
    """The lower-cased emails of every `Signed-off-by` line in a commit message."""
    return [m.group("email").lower() for line in message.splitlines() if (m := _SIGNOFF.match(line))]


def check_commits(commits: list[dict[str, Any]]) -> dict:
    """Judges API commit objects (`sha`, `parents`, `commit.message`, `commit.author.email`)."""
    failures = []
    checked = 0
    for c in commits:
        if len(c.get("parents") or []) > 1:
            continue  # a merge commit is exempt
        checked += 1
        commit = c.get("commit") or {}
        message = commit.get("message") or ""
        author_email = ((commit.get("author") or {}).get("email") or "").strip()
        subject = message.splitlines()[0] if message.strip() else ""
        found = signoff_emails(message)
        if not found:
            reason = f"has no `Signed-off-by: Your Name <{author_email or 'you@example.com'}>` line"
        elif author_email.lower() not in found:
            reason = f"is signed off as {', '.join(f'<{e}>' for e in found)}, but its author email is <{author_email}>"
        else:
            continue
        failures.append({"sha": c["sha"], "subject": subject, "author_email": author_email, "reason": reason})
    return _result(len(commits), checked, failures)


def refusal(count: int) -> dict:
    message = f"this pull request has {count} commits; the sign-off check reads at most {MAX_COMMITS}. Squash it into fewer commits (signed off with `{FIX}`) or open a smaller one."
    return {
        "ok": False, "commits": count, "checked": 0, "failures": [], "refused": message,
        "description": f"{count} commits is more than the {MAX_COMMITS} the check reads; squash and sign off",
        "lines": [message],
    }


def _result(count: int, checked: int, failures: list[dict]) -> dict:
    lines = [
        f"commit {f['sha'][:7]} ({f['subject']!r}) {f['reason']}. Fix: `{FIX}` for the last commit, "
        f"or `{FIX_MANY}` for several, then force-push."
        for f in failures
    ]
    if failures:
        first = failures[0]
        extra = f" and {len(failures) - 1} more" if len(failures) > 1 else ""
        description = f"{first['sha'][:7]}{extra} not signed off: run {FIX}"
    else:
        description = f"{checked} commit{'s' if checked != 1 else ''} signed off"
    return {"ok": not failures, "commits": count, "checked": checked, "failures": failures, "refused": None,
            "description": description[:140], "lines": lines}


def check_pull_request(gh, repo: str, number: int) -> dict:
    """Reads the pull request's commits through the API and judges them."""
    pr = gh.request("GET", f"/repos/{repo}/pulls/{number}")
    count = int(pr.get("commits") or 0)
    if count > MAX_COMMITS:
        return refusal(count)
    commits = gh.paginate(f"/repos/{repo}/pulls/{number}/commits")
    return check_commits(commits)
