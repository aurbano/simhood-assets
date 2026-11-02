# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Lays the renderer's output out flat for the `pictures` artifact (render.yml).

  uv run tools/pack_pictures.py --renders <renderer out dir> --dest <artifact dir> --pr <n> --sha <head sha>

Copies every picture the renderer's `index.json` names to `<dest>/<folder>~<file>.png`, writes the
index with those flat names and a `meta.json` ({pr, head_sha}, informative only). The publisher
accepts flat names only and re-checks every byte, so this is layout, not trust.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def pack(renders: Path, dest: Path, pr: int, sha: str) -> int:
    index_file = renders / "index.json"
    dest.mkdir(parents=True, exist_ok=True)
    if not index_file.is_file():
        index = {"version": 1, "models": {}, "examples": {}}
    else:
        index = json.loads(index_file.read_text(encoding="utf-8"))
    copied = 0
    for entry in index.get("models", {}).values():
        for key in ("image", "tinted", "before"):
            rel = entry.get(key)
            if not rel:
                continue
            flat = rel.replace("/", "~")
            shutil.copyfile(renders / rel, dest / flat)
            entry[key] = flat
            copied += 1
    index["examples"] = {}
    (dest / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    (dest / "meta.json").write_text(json.dumps({"pr": pr, "head_sha": sha}) + "\n", encoding="utf-8")
    return copied


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--renders", type=Path, required=True)
    p.add_argument("--dest", type=Path, required=True)
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--sha", required=True)
    try:
        a = p.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    print(f"packed {pack(a.renders, a.dest, a.pr, a.sha)} picture(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
