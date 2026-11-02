"""The catalogue: `catalogue/kinds.toml` and the eight domain files (specs/asset-catalogue)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DOMAINS = {
    "vehicles": "Vehicles",
    "building-parts": "Building parts",
    "industry": "Industry and yards",
    "farm": "Farms",
    "construction": "Construction",
    "streets": "Streets",
    "people": "People",
    "nature": "Nature",
}
CLASSES = {"prop": 1500, "vehicle": 3000, "large": 6000}
PRIORITIES = ("high", "medium", "low")
STATUSES = ("missing", "stand-in", "have", "procedural")
PLANS = ("commission", "kitbash", "procedural", "any")

SLOT_KEYS = {
    "id", "kind", "name", "size", "style", "priority", "status", "variants", "tint",
    "max_triangles", "size_tolerance", "plan", "notes",
}
KIND_KEYS = {"id", "class", "drawn", "name"}

_WORD = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_KIND_ID = re.compile(r"^[a-z0-9_]+(?:\.[a-z0-9_]+)*$")


@dataclass
class Kind:
    id: str
    cls: str
    name: str
    drawn: bool


@dataclass
class Slot:
    id: str
    domain: str
    kind: str
    name: str
    size: dict
    style: str
    priority: str
    status: str
    variants: int = 1
    tint: str | None = None
    max_triangles: int | None = None
    size_tolerance: float | None = None
    plan: str | None = None
    notes: str | None = None
    file: str = ""  # repo-relative path of the file that lists it


@dataclass
class Catalogue:
    classes: dict[str, int] = field(default_factory=dict)
    kinds: dict[str, Kind] = field(default_factory=dict)
    slots: dict[str, Slot] = field(default_factory=dict)
    # Raw slot tables by id, for the PR rule that compares a slot with its base version.
    raw: dict[str, dict] = field(default_factory=dict)
    # (path, rule, message) found while reading
    problems: list[tuple[str, str, str]] = field(default_factory=list)

    def budget(self, kind: str, slot: Slot | None) -> int | None:
        """A slot's `max_triangles`, else its kind's class budget."""
        if slot is not None and slot.max_triangles is not None:
            return slot.max_triangles
        k = self.kinds.get(kind)
        return self.classes.get(k.cls) if k else None


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _load(path: Path, rel: str, out: Catalogue) -> dict | None:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        out.problems.append((rel, "catalogue.schema", f"cannot be read: {e}"))
    except tomllib.TOMLDecodeError as e:
        out.problems.append((rel, "catalogue.schema", f"is not valid TOML: {e}"))
    return None


def load(root: Path) -> Catalogue:
    """Reads and checks the catalogue under `root`; problems go to `Catalogue.problems`."""
    cat = Catalogue()
    kinds_path = root / "catalogue" / "kinds.toml"
    rel = "catalogue/kinds.toml"
    if not kinds_path.is_file():
        cat.problems.append((rel, "catalogue.missing", "is missing"))
    else:
        _load_kinds(kinds_path, rel, cat)
    for domain in DOMAINS:
        path = root / "catalogue" / f"{domain}.toml"
        rel = f"catalogue/{domain}.toml"
        if not path.is_file():
            cat.problems.append((rel, "catalogue.missing", f"is missing: every domain has a file"))
            continue
        _load_domain(path, rel, domain, cat)
    folder = root / "catalogue"
    if folder.is_dir():
        for child in sorted(folder.iterdir()):
            if child.suffix == ".toml" and child.stem not in DOMAINS and child.name not in ("kinds.toml", "waivers.toml"):
                cat.problems.append(
                    (f"catalogue/{child.name}", "catalogue.schema", "is not one of the eight domain files or kinds.toml")
                )
    return cat


def _load_kinds(path: Path, rel: str, cat: Catalogue) -> None:
    doc = _load(path, rel, cat)
    if doc is None:
        return
    for key in doc:
        if key not in ("classes", "kind"):
            cat.problems.append((rel, "catalogue.schema", f"unknown top-level key `{key}`"))
    classes = doc.get("classes")
    if not isinstance(classes, dict):
        cat.problems.append((rel, "catalogue.schema", "has no [classes] table"))
    else:
        for name, budget in classes.items():
            if name not in CLASSES:
                cat.problems.append((rel, "catalogue.schema", f"class `{name}` is not prop, vehicle or large"))
            elif not _is_int(budget) or budget <= 0:
                cat.problems.append((rel, "catalogue.schema", f"class `{name}` budget must be a positive whole number"))
            else:
                cat.classes[name] = budget
        for name in CLASSES:
            if name not in classes:
                cat.problems.append((rel, "catalogue.schema", f"class `{name}` has no budget"))
    entries = doc.get("kind", [])
    if not isinstance(entries, list):
        cat.problems.append((rel, "catalogue.schema", "`kind` must be an array of tables"))
        return
    for entry in entries:
        if not isinstance(entry, dict):
            cat.problems.append((rel, "catalogue.schema", "a `kind` entry is not a table"))
            continue
        kid = entry.get("id")
        shown = kid if isinstance(kid, str) else "?"
        for key in entry:
            if key not in KIND_KEYS:
                cat.problems.append((rel, "catalogue.schema", f"kind `{shown}` has unknown key `{key}`"))
        if not isinstance(kid, str) or not _KIND_ID.match(kid):
            cat.problems.append((rel, "catalogue.schema", f"kind id {kid!r} is not lowercase words joined by `.`"))
            continue
        if kid in cat.kinds:
            cat.problems.append((rel, "catalogue.schema", f"kind `{kid}` is listed twice"))
            continue
        cls, name, drawn = entry.get("class"), entry.get("name"), entry.get("drawn", False)
        if cls not in CLASSES:
            cat.problems.append((rel, "catalogue.schema", f"kind `{kid}` has class {cls!r}, not prop, vehicle or large"))
            continue
        if not isinstance(name, str) or not name.strip():
            cat.problems.append((rel, "catalogue.schema", f"kind `{kid}` has no name"))
            continue
        if not isinstance(drawn, bool):
            cat.problems.append((rel, "catalogue.schema", f"kind `{kid}` has a `drawn` that is not true or false"))
            continue
        cat.kinds[kid] = Kind(kid, cls, name, drawn)


def _load_domain(path: Path, rel: str, domain: str, cat: Catalogue) -> None:
    doc = _load(path, rel, cat)
    if doc is None:
        return
    for key in doc:
        if key != "slot":
            cat.problems.append((rel, "catalogue.schema", f"unknown top-level key `{key}`"))
    entries = doc.get("slot", [])
    if not isinstance(entries, list):
        cat.problems.append((rel, "catalogue.schema", "`slot` must be an array of tables"))
        return
    id_re = re.compile(rf"^{re.escape(domain)}\.{_WORD}$")
    for entry in entries:
        if not isinstance(entry, dict):
            cat.problems.append((rel, "catalogue.schema", "a `slot` entry is not a table"))
            continue
        sid = entry.get("id")
        shown = sid if isinstance(sid, str) else "?"

        def bad(msg: str, rule: str = "catalogue.schema", shown: str = shown) -> None:
            cat.problems.append((rel, rule, f"slot `{shown}`: {msg}"))

        if not isinstance(sid, str):
            bad("has no id")
            continue
        duplicate = sid in cat.slots
        if duplicate:
            bad("is listed twice (ids are unique across the catalogue)", "catalogue.duplicate")
        id_ok = True
        if not sid.startswith(domain + "."):
            bad(f"belongs in the `{sid.split('.', 1)[0]}` file, not `{domain}.toml`", "catalogue.domain")
            id_ok = False
        elif not id_re.match(sid):
            bad(f"id is not `{domain}.<lowercase-words-joined-by-dashes>`")
            id_ok = False
        for key in entry:
            if key not in SLOT_KEYS:
                bad(f"has unknown key `{key}`")
        ok = True
        kind = entry.get("kind")
        if not isinstance(kind, str) or kind not in cat.kinds:
            bad(f"kind `{kind}` is not listed in kinds.toml", "catalogue.kind")
        for key in ("name", "style"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                bad(f"`{key}` is missing or empty")
                ok = False
        size = entry.get("size")
        if not isinstance(size, dict) or set(size) != {"length", "width", "height"} or not all(
            _is_num(v) and v > 0 for v in size.values()
        ):
            bad("`size` needs a positive `length`, `width` and `height` in metres")
            ok = False
        if entry.get("priority") not in PRIORITIES:
            bad(f"priority {entry.get('priority')!r} is not high, medium or low")
            ok = False
        if entry.get("status") not in STATUSES:
            bad(f"status {entry.get('status')!r} is not missing, stand-in, have or procedural")
            ok = False
        variants = entry.get("variants", 1)
        if not _is_int(variants) or variants < 1:
            bad("`variants` must be a positive whole number")
            ok = False
        for key in ("tint", "notes"):
            if key in entry and (not isinstance(entry[key], str) or not entry[key].strip()):
                bad(f"`{key}` must be a non-empty string")
                ok = False
        if "max_triangles" in entry and (not _is_int(entry["max_triangles"]) or entry["max_triangles"] < 1):
            bad("`max_triangles` must be a positive whole number")
            ok = False
        if "size_tolerance" in entry and (not _is_num(entry["size_tolerance"]) or entry["size_tolerance"] <= 0):
            bad("`size_tolerance` must be a positive number")
            ok = False
        if "plan" in entry and entry["plan"] not in PLANS:
            bad(f"plan {entry['plan']!r} is not commission, kitbash, procedural or any")
            ok = False
        if duplicate or not ok or not id_ok:
            continue
        cat.raw[sid] = entry
        cat.slots[sid] = Slot(
            id=sid, domain=domain, kind=kind, name=entry["name"], size={k: float(v) for k, v in size.items()},
            style=entry["style"], priority=entry["priority"], status=entry["status"], variants=variants,
            tint=entry.get("tint"), max_triangles=entry.get("max_triangles"),
            size_tolerance=float(entry["size_tolerance"]) if "size_tolerance" in entry else None,
            plan=entry.get("plan"), notes=entry.get("notes"), file=rel,
        )
