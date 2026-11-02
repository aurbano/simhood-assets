# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""What the trusted workflows do on GitHub: commit statuses, the pull request comment and the
preview pictures (design D10). Runs base-branch code only; never run on a pull request's files.

  GITHUB_TOKEN=... uv run tools/publish.py <command> --repo <owner/name> ...

  status     --sha S --context C --state S --description D [--url U]
  statuses   --sha S --dir <check output> --url U           final assets/dco and assets/validate
  verdict    --pr N --head-sha S --dir <check output> --url U [--base <root>]   comment's verdict section
  pictures   --run-id ID --section-out F [--wait-verdict SECS]   bind, check and publish a render
  comment    --pr N --section pictures --file F              write a prepared section
  cleanup    --pr N                                          drop a closed pull request's previews

`<check output>` holds report.json, dco.json and meta.json. Outputs for the workflow go to
$GITHUB_OUTPUT (`pr`, `commit`).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from assets_lib import comment, publisher  # noqa: E402
from assets_lib.github import GitHub, GitHubError  # noqa: E402


def _output(**values: object) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for k, v in values.items():
                f.write(f"{k}={v}\n")


def main(argv: list[str] | None = None, gh=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["status", "statuses", "verdict", "pictures", "comment", "cleanup"])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha")
    parser.add_argument("--head-sha")
    parser.add_argument("--context")
    parser.add_argument("--state")
    parser.add_argument("--description")
    parser.add_argument("--url", default="")
    parser.add_argument("--dir", type=Path)
    parser.add_argument("--pr", type=int)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--base", type=Path)
    parser.add_argument("--section", choices=list(comment.SECTIONS))
    parser.add_argument("--file", type=Path)
    parser.add_argument("--section-out", type=Path)
    parser.add_argument("--wait-verdict", type=int, default=0)
    parser.add_argument("--remote", help="git remote of the repository (default: its https address)")
    try:
        a = parser.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0

    token = os.environ.get("GITHUB_TOKEN", "")
    remote = a.remote or f"https://github.com/{a.repo}.git"

    def need(*names: str) -> bool:
        missing = [n for n in names if getattr(a, n.replace("-", "_")) in (None, "")]
        if missing:
            print(f"{a.command} needs " + ", ".join(f"--{n}" for n in missing), file=sys.stderr)
        return not missing

    try:
        client = gh or GitHub(token)
        if a.command == "status":
            if not need("sha", "context", "state", "description"):
                return 2
            publisher.post_status(client, a.repo, a.sha, a.context, a.state, a.description, a.url or None)
        elif a.command == "statuses":
            if not need("sha", "dir", "url"):
                return 2
            report, dco, _ = publisher.load_check_files(a.dir)
            states = publisher.finish_statuses(client, a.repo, a.sha, report, dco, a.url)
            print(", ".join(f"{k}: {v}" for k, v in states.items()))
        elif a.command == "verdict":
            if not need("pr", "head-sha", "dir", "url"):
                return 2
            report, dco, _ = publisher.load_check_files(a.dir)
            publisher.post_verdict(client, a.repo, a.pr, a.head_sha, a.url, report, dco, a.base)
        elif a.command == "pictures":
            if not need("run-id", "section-out"):
                return 2
            result = publisher.publish_pictures(client, a.repo, a.run_id, remote=remote, token=token, wait_verdict=a.wait_verdict)
            if result.pr is not None and result.section is not None:
                a.section_out.parent.mkdir(parents=True, exist_ok=True)
                a.section_out.write_text(result.section + "\n", encoding="utf-8")
                _output(pr=result.pr, commit=result.commit or "")
            print(f"published {result.published} picture(s), refused {result.refused}; pull request {result.pr}")
        elif a.command == "comment":
            if not need("pr", "section", "file"):
                return 2
            publisher_text = a.file.read_text(encoding="utf-8")
            comment.upsert(client, a.repo, a.pr, a.section, publisher_text)
        elif a.command == "cleanup":
            if not need("pr"):
                return 2
            result = publisher.cleanup(client, a.repo, a.pr, remote=remote, token=token)
            print("previews rebuilt" if result.pushed else "previews unchanged")
    except (GitHubError, RuntimeError, ValueError) as e:
        print(f"{a.command}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
