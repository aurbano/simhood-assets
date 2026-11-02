# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Generates the credits from the manifests.

  uv run tools/credits.py --repo <root> [--format md|json] [--out <file>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from assets_lib import credits  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--format", choices=("md", "json"), default="md")
    parser.add_argument("--out", type=Path, help="write here instead of stdout")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    if not args.repo.is_dir():
        print(f"--repo {args.repo} is not a folder", file=sys.stderr)
        return 2
    data = credits.collect(args.repo)
    text = json.dumps(data, indent=2) + "\n" if args.format == "json" else credits.to_markdown(data)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
