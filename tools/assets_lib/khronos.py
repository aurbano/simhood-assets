"""Runs the Khronos glTF Validator through `tools/gltf_validate.mjs` (Node)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "gltf_validate.mjs"
_BATCH = 40


class Unavailable(RuntimeError):
    """The validator could not be run at all."""


def run(paths: list[Path]) -> dict[Path, dict]:
    """The validator's verdict per file: {"errors": [{code, message, pointer}], "warnings": n, "fatal": str | None}."""
    node = shutil.which("node")
    if node is None:
        raise Unavailable("node is not installed, so the glTF Validator cannot run")
    out: dict[Path, dict] = {}
    for start in range(0, len(paths), _BATCH):
        batch = paths[start : start + _BATCH]
        try:
            done = subprocess.run(
                [node, str(SCRIPT), *[str(p) for p in batch]],
                capture_output=True, text=True, timeout=600, check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise Unavailable("the glTF Validator timed out") from e
        if done.returncode != 0:
            raise Unavailable(f"the glTF Validator failed to run: {done.stderr.strip()[:400]}")
        try:
            doc = json.loads(done.stdout)
        except ValueError as e:
            raise Unavailable(f"the glTF Validator printed no JSON: {done.stdout[:200]!r}") from e
        for path in batch:
            out[path] = doc["files"][str(path)]
    return out
