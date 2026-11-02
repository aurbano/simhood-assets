"""A small GitHub REST client over the standard library.

The workflows' scripts (`dco.py`, `publish.py`) talk to GitHub only through this class's three
methods (`request`, `paginate`, `download`), so tests replace it with an in-memory fake.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

API = "https://api.github.com"
PER_PAGE = 100


class GitHubError(RuntimeError):
    """A request GitHub refused or that could not be made."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class GitHub:
    def __init__(self, token: str, api: str = API):
        if not token:
            raise GitHubError("no GitHub token (set GITHUB_TOKEN)")
        self.token = token
        self.api = api.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "simhood-assets-ci",
        }

    def request(self, method: str, path: str, body: dict | None = None) -> Any:
        """One JSON request; `path` starts with `/` and may carry a query. Returns the decoded body."""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.api + path, data=data, method=method, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise GitHubError(f"{method} {path}: HTTP {e.code} {detail}") from e
        except urllib.error.URLError as e:
            raise GitHubError(f"{method} {path}: {e.reason}") from e
        return json.loads(raw) if raw else None

    def paginate(self, path: str, key: str | None = None, max_pages: int = 30) -> list:
        """Every item of a list endpoint (`key` names the list inside an object response, as artifacts and runs do)."""
        items: list = []
        sep = "&" if "?" in path else "?"
        for page in range(1, max_pages + 1):
            chunk = self.request("GET", f"{path}{sep}per_page={PER_PAGE}&page={page}")
            part = chunk[key] if key else chunk
            items.extend(part)
            if len(part) < PER_PAGE:
                break
        return items

    def download(self, path: str, max_bytes: int) -> bytes:
        """The bytes behind an endpoint that redirects to storage (artifact zips).

        The redirect is followed by hand so the token never goes to the storage host.
        """
        opener = urllib.request.build_opener(_NoRedirect)
        req = urllib.request.Request(self.api + path, headers=self._headers())
        try:
            resp = opener.open(req, timeout=60)
            url = None
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308):
                raise GitHubError(f"GET {path}: HTTP {e.code}") from e
            url = e.headers.get("Location")
            resp = None
        if url:
            if not url.startswith("https://"):
                raise GitHubError(f"GET {path}: redirected to a non-https address")
            resp = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "simhood-assets-ci"}), timeout=120)
        assert resp is not None
        with resp:
            data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise GitHubError(f"GET {path}: more than {max_bytes} bytes")
        return data
