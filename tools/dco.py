# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Checks that every non-merge commit of a pull request is signed off (the DCO, design D8).

  GITHUB_TOKEN=... uv run tools/dco.py --repo <owner/name> --pr <number> [--out dco.json]

Reads the commits through the API (nothing is checked out), refuses more than 250. Prints one
line per failing commit with the command that fixes it. Exit 0 when all are signed off, 1 when
not, 2 when GitHub could not be read.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from assets_lib import dco  # noqa: E402
from assets_lib.github import GitHub, GitHubError  # noqa: E402


def main(argv: list[str] | None = None, gh: GitHub | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--pr", required=True, type=int, help="pull request number")
    parser.add_argument("--out", type=Path, help="write the result as JSON here")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        result = dco.check_pull_request(gh or GitHub(os.environ.get("GITHUB_TOKEN", "")), args.repo, args.pr)
    except GitHubError as e:
        print(f"could not read the pull request: {e}", file=sys.stderr)
        return 2
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for line in result["lines"]:
        print(f"ERROR {line}")
    print(result["description"], file=sys.stderr)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
