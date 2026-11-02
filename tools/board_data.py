# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Writes the board's data from the catalogue, the validator report and the renderer index.

  uv run tools/board_data.py --repo <root> --report <report.json> --render-index <index.json> --out <board.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from assets_lib import board  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--render-index", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    for label, path in (("--report", args.report), ("--render-index", args.render_index)):
        if not path.is_file():
            print(f"{label} {path} is not a file", file=sys.stderr)
            return 2
    report = json.loads(args.report.read_text(encoding="utf-8"))
    index = json.loads(args.render_index.read_text(encoding="utf-8"))
    data = board.build(args.repo, report, index)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
