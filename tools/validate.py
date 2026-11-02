# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Validates the assets repository: catalogue, licences, files, glTF, budgets and sizes.

  uv run tools/validate.py [--repo <root>] [--strict | --pr --base <base-root> --pr-author <login>]
                           [--report <file.json>] [--no-gltf-validator]

Exit 0 when there are no errors (warnings allowed), 1 on errors, 2 on usage problems.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from assets_lib import checker  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent, help="the repository root (default: this repository)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--strict", action="store_true", help="validate the whole repository (the default)")
    mode.add_argument("--pr", action="store_true", help="compare --repo (the head) with --base")
    parser.add_argument("--base", type=Path, help="the base tree, for --pr")
    parser.add_argument("--pr-author", help="the login that opened the pull request, for --pr")
    parser.add_argument("--report", type=Path, help="write the JSON report here")
    parser.add_argument("--no-gltf-validator", action="store_true", help="skip the Khronos glTF Validator (unit tests only)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    if args.pr and (args.base is None or not args.pr_author):
        print("--pr needs --base <base-root> and --pr-author <login>", file=sys.stderr)
        return 2
    if not args.pr and (args.base is not None or args.pr_author):
        print("--base and --pr-author belong to --pr", file=sys.stderr)
        return 2
    for label, path in (("--repo", args.repo), ("--base", args.base)):
        if path is not None and not path.is_dir():
            print(f"{label} {path} is not a folder", file=sys.stderr)
            return 2
    report = checker.validate(
        args.repo, pr=args.pr, base=args.base, pr_author=args.pr_author, gltf_validator=not args.no_gltf_validator
    )
    for e in report["errors"]:
        print(f"ERROR {e['path']}: {e['message']}")
    for w in report["warnings"]:
        print(f"WARN {w['path']}: {w['message']}")
    if args.pr:
        # The reviewer must see how each affected model presents itself: a third-party work filed
        # without licence evidence cannot be detected.
        for m in report["models"]:
            print(f"MODEL {m['manifest_path']}: {m['id']} ({m['change']}) origin: {m['origin']}")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"{len(report['errors'])} error(s), {len(report['warnings'])} warning(s), {len(report['waived'])} waived, "
        f"{len(report['models'])} model(s) checked",
        file=sys.stderr,
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
