"""What the trusted workflows do with GitHub: statuses, the comment, and the binding and
publishing of preview pictures (design D10).

Nothing here reads a pull request's files except the render artifact, which `pictures` treats as
hostile bytes. Every function takes the GitHub client as an argument so tests can fake it.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import catalogue as catmod
from . import comment, pictures, previews, verdict

CHECK_WORKFLOW = ".github/workflows/check.yml"
RENDER_WORKFLOW = ".github/workflows/render.yml"
MAX_VERDICT_CANDIDATES = 30
MAX_VERDICT_ZIP = 8 * 1024 * 1024
MAX_VERDICT_FILE = 6 * 1024 * 1024


# ------------------------------------------------------------------------------------------------
# Statuses and the verdict section (check.yml)


def post_status(gh, repo: str, sha: str, context: str, state: str, description: str, url: str | None = None) -> None:
    body = {"state": state, "context": context, "description": description[:140]}
    if url:
        body["target_url"] = url
    gh.request("POST", f"/repos/{repo}/statuses/{sha}", body)


def finish_statuses(gh, repo: str, sha: str, report: dict | None, dco: dict | None, url: str) -> dict[str, str]:
    """Posts the final `assets/dco` and `assets/validate` statuses; returns their states."""
    states = {}
    for context, (state, description) in ((verdict.DCO_CONTEXT, verdict.dco_status(dco)), (verdict.VALIDATE_CONTEXT, verdict.validate_status(report))):
        post_status(gh, repo, sha, context, state, description, url)
        states[context] = state
    return states


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_check_files(folder: Path) -> tuple[dict | None, dict | None, dict | None]:
    """(report, dco, meta) from a folder holding the check job's output; each None when absent."""
    return tuple(_read_json(folder / name) for name in ("report.json", "dco.json", "meta.json"))  # type: ignore[return-value]


def post_verdict(gh, repo: str, number: int, head_sha: str, run_url: str, report: dict | None, dco: dict | None, base_root: Path | None) -> int:
    slots = None
    if base_root is not None:
        try:
            slots = catmod.load(base_root).slots
        except (OSError, ValueError):
            slots = None
    section = verdict.verdict_section(report, dco, head_sha, run_url, slots)
    return comment.upsert(gh, repo, number, "verdict", section)


# ------------------------------------------------------------------------------------------------
# The verdict as the preview publisher finds it


def _members(zip_bytes: bytes) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist()[:16]:
            if info.file_size <= MAX_VERDICT_FILE and info.filename in ("report.json", "dco.json", "meta.json"):
                with zf.open(info) as f:
                    out[info.filename] = f.read(MAX_VERDICT_FILE + 1)[:MAX_VERDICT_FILE]
    return out


def load_verdict(gh, repo: str, number: int, head_sha: str) -> dict | None:
    """The report of the newest `check.yml` verdict for this pull request and head commit.

    An artifact called `verdict` can come from any run, so each candidate must come from a
    `pull_request_target` run of check.yml in this repository (which only base code can write),
    and its own meta.json must name this pull request and this head.
    """
    listing = gh.request("GET", f"/repos/{repo}/actions/artifacts?name=verdict&per_page={MAX_VERDICT_CANDIDATES}")
    runs: dict[int, bool] = {}
    for art in listing.get("artifacts", [])[:MAX_VERDICT_CANDIDATES]:
        if art.get("expired") or art.get("size_in_bytes", 0) > MAX_VERDICT_ZIP:
            continue
        run_id = (art.get("workflow_run") or {}).get("id")
        if run_id is None:
            continue
        if run_id not in runs:
            run = gh.request("GET", f"/repos/{repo}/actions/runs/{run_id}")
            runs[run_id] = (
                run.get("event") == "pull_request_target"
                and run.get("path") == CHECK_WORKFLOW
                and (run.get("head_repository") or {}).get("full_name") == repo
            )
        if not runs[run_id]:
            continue
        try:
            files = _members(gh.download(f"/repos/{repo}/actions/artifacts/{art['id']}/zip", MAX_VERDICT_ZIP))
            meta = json.loads(files["meta.json"])
            report = json.loads(files["report.json"])
        except (KeyError, ValueError, zipfile.BadZipFile):
            continue
        if meta.get("pr") == number and meta.get("head_sha") == head_sha and isinstance(report, dict) and isinstance(report.get("models"), list):
            return report
    return None


# ------------------------------------------------------------------------------------------------
# Previews (preview.yml)


@dataclass
class PictureResult:
    pr: int | None  # the pull request whose comment to update (None: nothing to say)
    section: str | None
    published: int = 0
    refused: int = 0
    commit: str | None = None


def find_pull_request(gh, repo: str, run: dict) -> dict | None:
    """The open pull request of this repository whose head is the run's head commit and repository.

    Found from the API only; if several match (one branch opened against two bases), the lowest number.
    """
    head_repo = run.get("head_repository") or {}
    full_name, owner, branch, sha = head_repo.get("full_name"), (head_repo.get("owner") or {}).get("login"), run.get("head_branch"), run.get("head_sha")
    if not (full_name and owner and branch and sha):
        return None
    candidates = gh.paginate(f"/repos/{repo}/pulls?state=open&head={owner}:{branch}")
    match = [
        p for p in candidates
        if (p.get("head") or {}).get("sha") == sha
        and ((p.get("head") or {}).get("repo") or {}).get("full_name") == full_name
        and ((p.get("base") or {}).get("repo") or {}).get("full_name") == repo
    ]
    return min(match, key=lambda p: p["number"]) if match else None


def still_current(gh, repo: str, number: int, head_sha: str) -> bool:
    pr = gh.request("GET", f"/repos/{repo}/pulls/{number}")
    return pr.get("state") == "open" and (pr.get("head") or {}).get("sha") == head_sha


def publish_pictures(
    gh,
    repo: str,
    run_id: int,
    *,
    remote: str,
    token: str | None,
    wait_verdict: int = 0,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> PictureResult:
    """Binds the render run's artifact to its pull request and publishes the accepted pictures."""
    run = gh.request("GET", f"/repos/{repo}/actions/runs/{run_id}")
    if run.get("event") != "pull_request" or run.get("path") != RENDER_WORKFLOW:
        log(f"run {run_id} is not a pull_request run of render.yml; nothing to do")
        return PictureResult(None, None)
    pr = find_pull_request(gh, repo, run)
    if pr is None:
        log("no open pull request has this run's head commit and repository; nothing to do")
        return PictureResult(None, None)
    number, head_sha = pr["number"], run["head_sha"]
    run_url = run.get("html_url") or f"https://github.com/{repo}/actions/runs/{run_id}"

    def only_note(note: str) -> PictureResult:
        return PictureResult(number, pictures.pictures_section(head_sha, None, None, note, run_url))

    if run.get("conclusion") != "success":
        return only_note(f"The pictures could not be drawn: the render run {run.get('conclusion') or 'did not finish'}. "
                         "Everything else on this page is unaffected.")
    arts = [a for a in gh.paginate(f"/repos/{repo}/actions/runs/{run_id}/artifacts", key="artifacts") if a.get("name") == "pictures" and not a.get("expired")]
    if not arts:
        return only_note("The pictures could not be shown: the render run left no `pictures` artifact.")
    if arts[0].get("size_in_bytes", 0) > pictures.MAX_ZIP:
        return only_note("The pictures were refused: the render artifact is larger than the limit.")
    waited = 0
    report = load_verdict(gh, repo, number, head_sha)
    while report is None and waited < wait_verdict:
        sleep(10)
        waited += 10
        report = load_verdict(gh, repo, number, head_sha)
    if report is None:
        return only_note("No pictures were published: the checks have not produced a verdict for this commit.")

    unpacked = pictures.unpack(gh.download(f"/repos/{repo}/actions/artifacts/{arts[0]['id']}/zip", pictures.MAX_ZIP))
    if unpacked.fatal:
        return only_note(f"No pictures were published: {unpacked.fatal}.")
    sel = pictures.select(unpacked, verdict.affected_ids(report))
    sha7 = head_sha[:7]
    pictures.assign_destinations(sel.pictures, number, sha7)

    if not still_current(gh, repo, number, head_sha):
        log(f"pull request {number} is closed or has a newer head; nothing published")
        return PictureResult(None, None)
    files = {p.dest.split("/", 1)[1]: p.data for p in sel.pictures}

    def open_now() -> set[int]:
        return {p["number"] for p in gh.paginate(f"/repos/{repo}/pulls?state=open")}

    rebuilt = previews.rebuild(remote, open_now, replace=(number, files), token=token)
    base_url = f"https://raw.githubusercontent.com/{repo}/{rebuilt.commit}/"
    section = pictures.pictures_section(head_sha, sel, base_url, run_url=run_url)
    return PictureResult(number, section, len(sel.pictures), len(sel.refused), rebuilt.commit)


def cleanup(gh, repo: str, number: int, *, remote: str, token: str | None) -> previews.Rebuilt:
    """Rebuilds `previews` without a closed pull request's folder."""

    def open_now() -> set[int]:
        return {p["number"] for p in gh.paginate(f"/repos/{repo}/pulls?state=open")}

    return previews.rebuild(remote, open_now, drop=number, token=token, create=False)
