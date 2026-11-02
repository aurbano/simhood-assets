"""The one comment per pull request, with a verdict section and a pictures section.

Each workflow replaces only its own section; the comment is found by a hidden marker and by
its author, so another user's comment quoting the marker is never edited.
"""

from __future__ import annotations

import re
import time

MARKER = "<!-- simhood-assets:pr-comment -->"
BOT_LOGIN = "github-actions[bot]"
SECTIONS = ("verdict", "pictures")
MAX_BODY = 60000  # GitHub allows 65536 characters

_PLACEHOLDERS = {
    "verdict": "_The checks have not finished yet._",
    "pictures": "_The pictures have not been drawn yet._",
}
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]")
_SPECIAL = re.compile(r"([\\`*_\[\]()#|~!<>&@])")


def md(text: object, limit: int = 300) -> str:
    """Text made safe for markdown: no formatting, links, mentions, HTML or comment markers."""
    s = _CONTROL.sub(" ", str(text))
    s = " ".join(s.split())
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    s = s.replace("://", "&#58;//")
    return _SPECIAL.sub(lambda m: f"&#{ord(m.group(1))};", s)


def start(name: str) -> str:
    return f"<!-- {name}:start -->"


def end(name: str) -> str:
    return f"<!-- {name}:end -->"


def skeleton() -> str:
    parts = [MARKER, "## Asset checks", ""]
    for name in SECTIONS:
        parts += [start(name), _PLACEHOLDERS[name], end(name), ""]
    return "\n".join(parts)


def replace_section(body: str | None, name: str, content: str) -> str:
    """`body` with the `name` section's content replaced; every other byte is kept."""
    if name not in SECTIONS:
        raise ValueError(f"unknown section {name}")
    for other in SECTIONS:
        if start(other) in content or end(other) in content or MARKER in content:
            raise ValueError("section content contains a marker")
    body = body if body and MARKER in body else skeleton()
    a, b = start(name), end(name)
    i = body.find(a)
    j = body.find(b, i + len(a)) if i >= 0 else -1
    block = f"{a}\n{content.strip()}\n{b}"
    if i < 0 or j < 0:
        return body.rstrip("\n") + "\n\n" + block + "\n"
    return body[:i] + block + body[j + len(b):]


def get_section(body: str, name: str) -> str | None:
    i = body.find(start(name))
    j = body.find(end(name), i + 1) if i >= 0 else -1
    if i < 0 or j < 0:
        return None
    return body[i + len(start(name)):j].strip()


def find_comment(comments: list[dict], bot_login: str = BOT_LOGIN) -> dict | None:
    """The oldest comment by the bot that carries the marker."""
    for c in sorted(comments, key=lambda c: c["id"]):
        if MARKER in (c.get("body") or "") and (c.get("user") or {}).get("login") == bot_login:
            return c
    return None


def upsert(gh, repo: str, number: int, name: str, content: str, bot_login: str = BOT_LOGIN, attempts: int = 3) -> int:
    """Writes one section of the pull request's comment, creating the comment if there is none.

    The caller holds the `comment-pr-<n>` concurrency group; the read-back below catches the
    rare write that another run overwrote anyway (a section is then written again).
    """
    comment_id = 0
    for attempt in range(attempts):
        comments = gh.paginate(f"/repos/{repo}/issues/{number}/comments")
        existing = find_comment(comments, bot_login)
        body = replace_section(existing["body"] if existing else None, name, content)
        if len(body) > MAX_BODY:
            raise ValueError(f"the comment would be {len(body)} characters, over the {MAX_BODY} this tool writes")
        if existing is None:
            created = gh.request("POST", f"/repos/{repo}/issues/{number}/comments", {"body": body})
            comment_id = created["id"]
        elif existing["body"] == body:
            return existing["id"]
        else:
            comment_id = existing["id"]
            gh.request("PATCH", f"/repos/{repo}/issues/comments/{comment_id}", {"body": body})
        after = find_comment(gh.paginate(f"/repos/{repo}/issues/{number}/comments"), bot_login)
        if after is not None and get_section(after["body"], name) == content.strip():
            return after["id"]
        time.sleep(attempt)
    return comment_id
