"""An in-memory stand-in for the GitHub REST API, enough for the workflows' scripts."""

from __future__ import annotations

import re
from typing import Any


class FakeGitHub:
    def __init__(self, repo: str = "aurbano/simhood-assets"):
        self.repo = repo
        self.pulls: dict[int, dict] = {}
        self.pull_commits: dict[int, list[dict]] = {}
        self.runs: dict[int, dict] = {}
        self.artifacts: dict[int, dict] = {}  # id -> artifact dict (+ "zip" bytes)
        self.comments: list[dict] = []
        self.statuses: list[dict] = []
        self.requests: list[tuple[str, str]] = []
        self._next_comment = 1

    # -- building the world

    def add_pull(self, number: int, sha: str, *, state="open", head_repo=None, branch="feature", owner="contributor", base_repo=None, commits=1) -> dict:
        head_repo = head_repo or f"{owner}/simhood-assets"
        pr = {
            "number": number, "state": state, "commits": commits,
            "head": {"sha": sha, "ref": branch, "repo": {"full_name": head_repo}},
            "base": {"repo": {"full_name": base_repo or self.repo}},
        }
        self.pulls[number] = pr
        return pr

    def add_run(self, run_id: int, *, event: str, path: str, head_sha: str, branch="feature", owner="contributor",
                head_repo=None, conclusion="success") -> dict:
        run = {
            "id": run_id, "event": event, "path": path, "head_sha": head_sha, "head_branch": branch, "conclusion": conclusion,
            "status": "completed", "html_url": f"https://github.com/{self.repo}/actions/runs/{run_id}",
            "head_repository": {"full_name": head_repo or f"{owner}/simhood-assets", "owner": {"login": owner}},
        }
        self.runs[run_id] = run
        return run

    def add_artifact(self, art_id: int, name: str, run_id: int, zip_bytes: bytes, expired=False) -> None:
        self.artifacts[art_id] = {"id": art_id, "name": name, "workflow_run": {"id": run_id}, "expired": expired,
                                  "size_in_bytes": len(zip_bytes), "zip": zip_bytes}

    def add_comment(self, body: str, login="github-actions[bot]") -> dict:
        c = {"id": self._next_comment, "body": body, "user": {"login": login}}
        self._next_comment += 1
        self.comments.append(c)
        return c

    # -- the client interface

    def _route(self, method: str, path: str, body: dict | None) -> Any:
        path, _, _query = path.partition("?")
        m = re.fullmatch(rf"/repos/{re.escape(self.repo)}/(.*)", path)
        if not m:
            raise AssertionError(f"unexpected path {path}")
        rest = m.group(1)
        if method == "GET":
            if rest == "pulls":
                return [p for p in self.pulls.values() if p["state"] == "open"]
            if g := re.fullmatch(r"pulls/(\d+)", rest):
                return self.pulls[int(g.group(1))]
            if g := re.fullmatch(r"pulls/(\d+)/commits", rest):
                return self.pull_commits[int(g.group(1))]
            if g := re.fullmatch(r"actions/runs/(\d+)", rest):
                return self.runs[int(g.group(1))]
            if g := re.fullmatch(r"actions/runs/(\d+)/artifacts", rest):
                rid = int(g.group(1))
                return {"artifacts": [self._public(a) for a in self.artifacts.values() if a["workflow_run"]["id"] == rid]}
            if rest == "actions/artifacts":
                name = re.search(r"name=([^&]+)", _query)
                items = [self._public(a) for a in sorted(self.artifacts.values(), key=lambda a: -a["id"]) if not name or a["name"] == name.group(1)]
                return {"artifacts": items}
            if g := re.fullmatch(r"issues/(\d+)/comments", rest):
                return list(self.comments)
        if method == "POST":
            if re.fullmatch(r"issues/\d+/comments", rest):
                return self.add_comment((body or {})["body"])
            if g := re.fullmatch(r"statuses/([0-9a-f]+)", rest):
                self.statuses.append({"sha": g.group(1), **(body or {})})
                return {}
        if method == "PATCH" and (g := re.fullmatch(r"issues/comments/(\d+)", rest)):
            for c in self.comments:
                if c["id"] == int(g.group(1)):
                    c["body"] = (body or {})["body"]
                    return c
        raise AssertionError(f"unexpected request {method} {path}")

    @staticmethod
    def _public(a: dict) -> dict:
        return {k: v for k, v in a.items() if k != "zip"}

    def request(self, method: str, path: str, body: dict | None = None) -> Any:
        self.requests.append((method, path))
        return self._route(method, path, body)

    def paginate(self, path: str, key: str | None = None, max_pages: int = 30) -> list:
        self.requests.append(("GET", path))
        out = self._route("GET", path, None)
        return out[key] if key else out

    def download(self, path: str, max_bytes: int) -> bytes:
        self.requests.append(("GET", path))
        g = re.fullmatch(rf"/repos/{re.escape(self.repo)}/actions/artifacts/(\d+)/zip", path)
        assert g, path
        data = self.artifacts[int(g.group(1))]["zip"]
        assert len(data) <= max_bytes
        return data
