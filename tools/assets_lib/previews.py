"""The `previews` branch: one parentless commit holding only open pull requests' latest pictures.

Every update fetches the branch's current tree, replaces or drops pull request folders and
force-pushes a brand-new root commit, guarded by a lease on the commit it started from. A lost
race is retried from the fresh tree, so two publishers for different pull requests both land.
Old pictures are unreachable afterwards and do not pile up in history.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

BRANCH = "previews"
REF = f"refs/heads/{BRANCH}"
README = (
    "# Previews\n\nPictures of the models in open pull requests, drawn by CI. This branch is rebuilt as a single\n"
    "commit every time and holds only open pull requests' latest pictures; never edit it by hand.\n"
)
_FOLDER = re.compile(r"^pr-([0-9]+)$")
_FILE = re.compile(r"^[0-9a-f]{7}/[0-9]+\.png$")


class PushLost(RuntimeError):
    """The branch kept moving under us."""


@dataclass
class Rebuilt:
    commit: str
    pushed: bool  # False when the branch already held exactly this tree


def _git(cwd: Path, *args: str, remote_auth: str | None = None, check: bool = True, input: bytes | None = None) -> subprocess.CompletedProcess:
    cmd = ["git"]
    if remote_auth:
        cmd += ["-c", f"http.extraheader=AUTHORIZATION: basic {remote_auth}"]
    cmd += list(args)
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "simhood-assets previews", "GIT_AUTHOR_EMAIL": "noreply@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "simhood-assets previews", "GIT_COMMITTER_EMAIL": "noreply@users.noreply.github.com",
    }
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, env=env, input=input, check=False)
    if check and done.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {done.stderr.decode('utf-8', 'replace').strip()[:400]}")
    return done


def rebuild(
    remote: str,
    fetch_open: Callable[[], set[int]],
    *,
    replace: tuple[int, dict[str, bytes]] | None = None,
    drop: int | None = None,
    token: str | None = None,
    attempts: int = 3,
    create: bool = True,
) -> Rebuilt:
    """Rebuilds the branch on `remote`.

    `fetch_open` returns the numbers of the pull requests that are open now (called afresh each
    attempt); `replace` is `(number, {"<sha7>/<k>.png": bytes})` and swaps that pull request's whole
    folder; `drop` removes a pull request's folder whatever `fetch_open` says (a closing event);
    `create=False` leaves a missing branch missing.
    """
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode() if token and remote.startswith("https://") else None
    if replace is not None:
        for rel in replace[1]:
            if not _FILE.match(rel):
                raise ValueError(f"unexpected preview file name {rel!r}")
    last = ""
    for attempt in range(attempts):
        with tempfile.TemporaryDirectory(prefix="previews-") as tmp:
            work = Path(tmp)
            _git(work, "init", "-q")
            listing = _git(work, "ls-remote", remote, REF, remote_auth=auth).stdout.decode().split()
            old = listing[0] if listing else None
            old_tree = None
            if old is None and not create:
                return Rebuilt("", False)
            if old:
                _git(work, "fetch", "-q", "--depth=1", remote, old, remote_auth=auth)
                _git(work, "checkout", "-q", "--detach", old)
                old_tree = _git(work, "rev-parse", f"{old}^{{tree}}").stdout.decode().strip()
            open_prs = fetch_open()
            for child in sorted(work.iterdir()):
                if child.name == ".git":
                    continue
                m = _FOLDER.match(child.name)
                keep = bool(m) and child.is_dir() and not child.is_symlink() and int(m.group(1)) in open_prs and int(m.group(1)) != drop
                if not keep:
                    shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
            if replace is not None:
                number, files = replace
                folder = work / f"pr-{number}"
                shutil.rmtree(folder, ignore_errors=True)
                for rel, data in files.items():
                    path = folder / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            (work / "README.md").write_text(README, encoding="utf-8")
            _git(work, "add", "-A", "-f")
            tree = _git(work, "write-tree").stdout.decode().strip()
            if old and tree == old_tree:
                return Rebuilt(old, False)
            commit = _git(work, "commit-tree", tree, "-m", "Previews of the open pull requests").stdout.decode().strip()
            lease = f"--force-with-lease={REF}:{old or ''}"
            pushed = _git(work, "push", "-q", lease, remote, f"{commit}:{REF}", remote_auth=auth, check=False)
            if pushed.returncode == 0:
                return Rebuilt(commit, True)
            last = pushed.stderr.decode("utf-8", "replace").strip()[:300]
    raise PushLost(f"the {BRANCH} branch moved {attempts} times while it was being rebuilt: {last}")
