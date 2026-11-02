"""Pull request mode: what changed between the base tree and the head tree, who may change it
(ACON-005) and what an edit must keep (ACON-015)."""

from __future__ import annotations

import difflib
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import catalogue as catmod
from .repo import ModelEntry, Tree, list_files, sha256

ALLOWED_TRANSITIONS = {("missing", "stand-in"), ("missing", "have"), ("stand-in", "have")}
_STATUS_LINE = re.compile(r"^\s*status\s*=")


@dataclass
class FileChanges:
    added: set[str] = field(default_factory=set)
    modified: set[str] = field(default_factory=set)
    deleted: set[str] = field(default_factory=set)
    head_files: dict[str, Path] = field(default_factory=dict)
    base_files: dict[str, Path] = field(default_factory=dict)

    @property
    def changed(self) -> set[str]:
        return self.added | self.modified | self.deleted

    def added_bytes(self) -> int:
        return sum(self.head_files[p].stat().st_size for p in self.added | self.modified if self.head_files[p].is_file())


def diff_trees(base: Path, head: Path) -> FileChanges:
    """Files added, modified and deleted in `head` against `base`.

    Each tree is listed by `repo.list_files`: ignored files and `.git` are not part of a tree.
    """
    out = FileChanges(head_files=list_files(head), base_files=list_files(base))
    for rel, path in out.head_files.items():
        other = out.base_files.get(rel)
        if other is None:
            out.added.add(rel)
        elif path.is_symlink() != other.is_symlink() or (
            path.is_symlink() and path.readlink() != other.readlink()
        ):
            out.modified.add(rel)
        elif not path.is_symlink() and (
            path.stat().st_size != other.stat().st_size or sha256(path) != sha256(other)
        ):
            out.modified.add(rel)
    out.deleted = set(out.base_files) - set(out.head_files)
    return out


def codeowners(root: Path) -> set[str]:
    """Lower-case GitHub handles named in CODEOWNERS (teams are not resolved)."""
    for name in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"):
        path = root / name
        if path.is_file():
            owners: set[str] = set()
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.split("#", 1)[0].strip()
                for token in line.split()[1:]:
                    if token.startswith("@") and "/" not in token:
                        owners.add(token[1:].lower())
            return owners
    return set()


def gltf_resources(root: Path, model_path: str) -> list[str]:
    """Repo-relative paths a `.gltf` names by relative URI (empty for a `.glb`)."""
    if not model_path.endswith(".gltf"):
        return []
    path = root / model_path
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    if isinstance(doc, dict):
        for key in ("buffers", "images"):
            for item in doc.get(key, []) if isinstance(doc.get(key), list) else []:
                uri = item.get("uri") if isinstance(item, dict) else None
                if isinstance(uri, str) and not uri.startswith("data:") and ":" not in uri:
                    from urllib.parse import unquote

                    rel = (path.parent / unquote(uri)).resolve()
                    try:
                        out.append(rel.relative_to(root.resolve()).as_posix())
                    except ValueError:
                        continue
    return out


def model_files(root: Path, entry: ModelEntry) -> list[str]:
    """Files whose change affects a model: manifest, model, its resources, its evidence."""
    files = [entry.manifest_path]
    if entry.model_path:
        files.append(entry.model_path)
        files.extend(gltf_resources(root, entry.model_path))
    if entry.manifest is not None and entry.manifest.licence_evidence and entry.pack:
        files.append(f"packs/{entry.pack}/{entry.manifest.licence_evidence}")
    return files


@dataclass
class ModelChange:
    entry: ModelEntry  # the head entry (the base entry when deleted)
    change: str  # added | changed | unchanged | deleted | renamed
    base_entry: ModelEntry | None = None  # the base version, for changed, renamed
    renamed_from: str | None = None


def classify(head: Tree, base: Tree, changes: FileChanges) -> list[ModelChange]:
    """Each pack model of the head tree and each deleted one, with how the pull request touched it."""
    head_models = [m for p in head.packs for m in p.models]
    base_models = {m.manifest_path: m for p in base.packs for m in p.models}
    head_paths = {m.manifest_path for m in head_models}
    pack_changed = {p.name for p in head.packs if f"{p.path}/pack.toml" in changes.changed}
    out: list[ModelChange] = []
    added: list[ModelChange] = []
    for m in head_models:
        old = base_models.get(m.manifest_path)
        if old is None:
            mc = ModelChange(m, "added")
            added.append(mc)
            out.append(mc)
            continue
        touched = any(f in changes.changed for f in model_files(head.root, m)) or m.pack in pack_changed
        out.append(ModelChange(m, "changed" if touched else "unchanged", old))
    deleted = [ModelChange(m, "deleted") for path, m in base_models.items() if path not in head_paths]
    # A deleted model and an added one with the same model file bytes are one rename.
    by_digest: dict[str, ModelChange] = {}
    for d in deleted:
        if d.entry.model_path and (base.root / d.entry.model_path).is_file():
            by_digest.setdefault(sha256(base.root / d.entry.model_path), d)
    remaining = list(deleted)
    for a in added:
        if a.entry.model_path and (head.root / a.entry.model_path).is_file():
            match = by_digest.pop(sha256(head.root / a.entry.model_path), None)
            if match is not None:
                a.change = "renamed"
                a.base_entry = match.entry
                a.renamed_from = match.entry.manifest_path
                remaining.remove(match)
    return out + remaining


def raw_slots(path: Path) -> dict[str, dict]:
    """Slot tables of a domain file by id, unchecked (a missing or broken file has none)."""
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict] = {}
    for entry in doc.get("slot", []) if isinstance(doc.get("slot"), list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            out[entry["id"]] = entry
    return out


def check_scope(
    changes: FileChanges, base: Path, head: Path, touched_slots: set[str]
) -> list[tuple[str, str, str]]:
    """The owner-only rule for a pull request from outside the code owners: (path, rule, message).

    `touched_slots` are the slots named by the pull request's added, changed or renamed models.
    """
    problems: list[tuple[str, str, str]] = []
    domains = {f"catalogue/{d}.toml" for d in catmod.DOMAINS}
    for path in sorted(changes.changed):
        parts = path.split("/")
        if parts[0] == "packs" and len(parts) >= 3:
            continue
        if path in domains:
            problems.extend(_check_catalogue_file(path, base, head, touched_slots, path in changes.deleted))
            continue
        problems.append(
            (
                path,
                "pr.owner-only",
                "is changed by a pull request from outside the code owners; only files inside packs/<pack>/ "
                "(and the status of the slots your models fill) may change. Ask the owner to make this change.",
            )
        )
    return problems


def _check_catalogue_file(
    path: str, base: Path, head: Path, touched_slots: set[str], deleted: bool
) -> list[tuple[str, str, str]]:
    if deleted:
        return [(path, "pr.owner-only", "is deleted by a pull request from outside the code owners; the catalogue is owner-only")]
    old, new = raw_slots(base / path), raw_slots(head / path)
    problems: list[tuple[str, str, str]] = []
    for sid in sorted(set(old) | set(new)):
        if sid not in old:
            problems.append((path, "pr.owner-only", f"slot {sid} is added; adding slots is owner-only"))
        elif sid not in new:
            problems.append((path, "pr.owner-only", f"slot {sid} is removed; removing slots is owner-only"))
        else:
            for key in sorted((set(old[sid]) | set(new[sid])) - {"status"}):
                if old[sid].get(key) != new[sid].get(key):
                    problems.append((path, "pr.owner-only", f"slot {sid}: `{key}` is changed; changing a slot's {key} is owner-only"))
            a, b = old[sid].get("status"), new[sid].get("status")
            if a != b:
                if (a, b) not in ALLOWED_TRANSITIONS:
                    problems.append(
                        (path, "pr.status", f"slot {sid}: status {a} -> {b} is not allowed in a contribution; only missing -> stand-in or have, and stand-in -> have")
                    )
                elif sid not in touched_slots:
                    problems.append(
                        (path, "pr.status", f"slot {sid}: status {a} -> {b} may be set only by a pull request whose own added or changed model names that slot")
                    )
    if not problems:
        before = (base / path).read_text(encoding="utf-8").splitlines() if (base / path).is_file() else []
        after = (head / path).read_text(encoding="utf-8").splitlines()
        stray = [
            line
            for line in difflib.unified_diff(before, after, lineterm="", n=0)
            if line[:1] in "+-" and line[:3] not in ("+++", "---") and not _STATUS_LINE.match(line[1:])
        ]
        if stray:
            problems.append((path, "pr.owner-only", "is changed beyond a slot's status line (comments, order or formatting); that is owner-only"))
    return problems


def check_append_only(old: ModelEntry, new: ModelEntry, old_author: str | None, new_author: str | None) -> list[tuple[str, str]]:
    """(rule, message) for an edit that drops earlier credit: `changes` and `contributors` keep
    every earlier entry in order, and the author stays."""
    problems: list[tuple[str, str]] = []
    if old.manifest is None or new.manifest is None:
        return problems
    for key, rule, noun in (("changes", "append-only.changes", "change"), ("contributors", "append-only.contributors", "contributor")):
        before, after = getattr(old.manifest, key), getattr(new.manifest, key)
        for i, item in enumerate(before):
            if i >= len(after) or after[i] != item:
                what = f"moves earlier {noun} `{item}`" if item in after else f"drops earlier {noun} `{item}`"
                problems.append(
                    (
                        rule,
                        f"{what}; `{key}` is append-only (keep every earlier entry, in order, and add yours at the end)",
                    )
                )
                break
    if old_author != new_author:
        problems.append(("append-only.author", f"changes the author from `{old_author}` to `{new_author}`; the original author is never replaced"))
    return problems
