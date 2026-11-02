"""Owner-only waivers (`catalogue/waivers.toml`): findings a shipped model is allowed to keep.

A waiver names a model, the SHA-256 of the model's main file and the rules it excuses. It applies
only while the file's hash matches, so any edit to the file meets the full rules; a waiver whose model
is gone or whose hash moved is itself an error (`waiver.stale`).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

PATH = "catalogue/waivers.toml"
_KEYS = {"model", "file_sha256", "rules", "reason"}


@dataclass
class Waiver:
    model: str
    file_sha256: str
    rules: list[str]
    reason: str


def load(root: Path) -> tuple[list[Waiver], list[str]]:
    """The waivers of the repository at `root` and a message per problem in the file."""
    path = root / PATH
    if not path.is_file():
        return [], []
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [], [f"cannot be read: {e}"]
    problems: list[str] = []
    for key in doc:
        if key != "waiver":
            problems.append(f"unknown top-level key `{key}`")
    out: list[Waiver] = []
    entries = doc.get("waiver", [])
    if not isinstance(entries, list):
        return [], problems + ["`waiver` must be an array of tables"]
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            problems.append("a `waiver` entry is not a table")
            continue
        shown = entry.get("model")
        for key in entry:
            if key not in _KEYS:
                problems.append(f"waiver for `{shown}` has unknown key `{key}`")
        rules = entry.get("rules")
        if not (
            isinstance(shown, str) and isinstance(entry.get("file_sha256"), str)
            and isinstance(rules, list) and rules and all(isinstance(r, str) for r in rules)
            and isinstance(entry.get("reason"), str) and entry["reason"].strip()
        ):
            problems.append(f"waiver for `{shown}` needs `model`, `file_sha256`, a non-empty `rules` list and a `reason`")
            continue
        if shown in seen:
            problems.append(f"model `{shown}` has two waivers")
            continue
        seen.add(shown)
        out.append(Waiver(shown, entry["file_sha256"].lower(), list(rules), entry["reason"].strip()))
    return out, problems
