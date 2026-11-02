"""`pack.toml` and model manifests, checked as the client's reader checks them
(`bindings/sim-gdext/src/assets.rs`), with the provenance keys of design D4.

The reader's one difference: it reports a kind it does not draw as "not drawn yet"; the validator
reports a kind missing from `catalogue/kinds.toml` instead (see `checker`).
"""

from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass, field

MAX_LENGTH = 60.0
MAX_SCALE = 100.0

AI_VALUES = ("yes", "no", "unknown")
FORWARDS = ("+x", "-x", "+z", "-z")
AI_PLANS = ("paid", "free", "self-hosted")

PACK_KEYS = {"id", "version", "licence", "ai_generated", "author", "source"}
MANIFEST_KEYS = {
    "id", "kind", "length", "height", "scale", "forward", "weight", "tint_key", "tint_tolerance",
    "flat_shading", "licence", "ai_generated", "author", "source", "slot", "title", "attribution",
    "licence_evidence", "changes", "contributors", "ai_tool", "ai_model", "ai_plan",
}

_HANDLE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_TINT = re.compile(r"^#[0-9A-Fa-f]{6}$")


class ManifestError(ValueError):
    """A line naming what is wrong with a pack.toml or manifest."""


@dataclass
class PackInfo:
    id: str
    version: str
    licence: str
    ai_generated: str
    author: str | None = None
    source: str | None = None


@dataclass
class Manifest:
    id: str
    kind: str
    measure: str  # length | height | scale
    measure_value: float
    forward: str = "+x"
    weight: int = 1
    tint_key: str | None = None
    tint_tolerance: float = 0.25
    flat_shading: bool = False
    licence: str | None = None
    ai_generated: str | None = None
    author: str | None = None
    source: str | None = None
    slot: str | None = None
    title: str | None = None
    attribution: str | None = None
    licence_evidence: str | None = None
    changes: list[str] = field(default_factory=list)
    contributors: list[str] = field(default_factory=list)
    ai_tool: str | None = None
    ai_model: str | None = None
    ai_plan: str | None = None
    has_changes_key: bool = False


def check_id(value: object) -> str:
    """A namespaced id: `namespace:name`, both non-empty, lowercase letters, digits, `-`, `_`, `.`."""
    def ok(part: str) -> bool:
        return bool(part) and all(c.islower() and c.isascii() or c.isdigit() and c.isascii() or c in "-_." for c in part)

    if isinstance(value, str) and ":" in value:
        ns, name = value.split(":", 1)
        if ok(ns) and ok(name):
            return value
    raise ManifestError(
        f"id {value!r} is not `namespace:name` in lowercase letters, digits, `-`, `_` or `.`"
    )


def _load(text: str) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ManifestError(f"not valid TOML: {e}") from e


def _unknown(doc: dict, allowed: set[str]) -> None:
    for key in doc:
        if key not in allowed:
            raise ManifestError(f"unknown key `{key}`")


def _string(doc: dict, key: str, required: bool = False) -> str | None:
    if key not in doc:
        if required:
            raise ManifestError(f"missing key `{key}`")
        return None
    value = doc[key]
    if not isinstance(value, str):
        raise ManifestError(f"`{key}` must be a string")
    return value


def _number(doc: dict, key: str) -> float | None:
    if key not in doc:
        return None
    value = doc[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManifestError(f"`{key}` must be a number")
    return float(value)


def _string_list(doc: dict, key: str) -> list[str]:
    if key not in doc:
        return []
    value = doc[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ManifestError(f"`{key}` must be a list of strings")
    return list(value)


def parse_pack(text: str) -> PackInfo:
    doc = _load(text)
    _unknown(doc, PACK_KEYS)
    pack_id = check_id(_string(doc, "id", True))
    version = _string(doc, "version", True)
    licence = _string(doc, "licence", True)
    ai = _string(doc, "ai_generated", True)
    if not version.strip():
        raise ManifestError("version is empty")
    if not licence.strip():
        raise ManifestError("licence is empty")
    if ai not in AI_VALUES:
        raise ManifestError(f"ai_generated `{ai}` is not yes, no or unknown")
    return PackInfo(pack_id, version, licence, ai, _string(doc, "author"), _string(doc, "source"))


def parse_manifest(text: str) -> Manifest:
    doc = _load(text)
    _unknown(doc, MANIFEST_KEYS)
    model_id = check_id(_string(doc, "id", True))
    kind = _string(doc, "kind", True)
    sizes = [(k, _number(doc, k)) for k in ("length", "height", "scale") if k in doc]
    if len(sizes) != 1:
        raise ManifestError("give exactly one of length, height or scale")
    measure, value = sizes[0]
    limit = MAX_SCALE if measure == "scale" else MAX_LENGTH
    if not (math.isfinite(value) and 0 < value <= limit):
        raise ManifestError(f"{measure} {value:g} is not above 0 and at most {limit:g}")
    weight = doc.get("weight", 1)
    if isinstance(weight, bool) or not isinstance(weight, int) or not 0 <= weight <= 0xFFFFFFFF:
        raise ManifestError("`weight` must be a whole number")
    if weight == 0:
        raise ManifestError("weight is 0; remove the model instead")
    tint_tolerance = _number(doc, "tint_tolerance")
    if tint_tolerance is None:
        tint_tolerance = 0.25
    if not 0 < tint_tolerance <= 1.0:
        raise ManifestError(f"tint_tolerance {tint_tolerance:g} is not in (0, 1]")
    tint_key = _string(doc, "tint_key")
    if tint_key is not None and not _TINT.match(tint_key):
        raise ManifestError(f"tint_key `{tint_key}` is not a `#rrggbb` colour")
    forward = _string(doc, "forward") or "+x"
    if forward not in FORWARDS:
        raise ManifestError(f"forward `{forward}` is not one of {', '.join(FORWARDS)}")
    flat = doc.get("flat_shading", False)
    if not isinstance(flat, bool):
        raise ManifestError("`flat_shading` must be true or false")
    ai = _string(doc, "ai_generated")
    if ai is not None and ai not in AI_VALUES:
        raise ManifestError(f"ai_generated `{ai}` is not yes, no or unknown")
    contributors = _string_list(doc, "contributors")
    for handle in contributors:
        if not _HANDLE.match(handle):
            raise ManifestError(f"contributor `{handle}` is not a GitHub handle")
    return Manifest(
        id=model_id,
        kind=kind,
        measure=measure,
        measure_value=value,
        forward=forward,
        weight=weight,
        tint_key=tint_key.lower() if tint_key else None,
        tint_tolerance=tint_tolerance,
        flat_shading=flat,
        licence=_string(doc, "licence"),
        ai_generated=ai,
        author=_string(doc, "author"),
        source=_string(doc, "source"),
        slot=_string(doc, "slot"),
        title=_string(doc, "title"),
        attribution=_string(doc, "attribution"),
        licence_evidence=_string(doc, "licence_evidence"),
        changes=_string_list(doc, "changes"),
        contributors=contributors,
        ai_tool=_string(doc, "ai_tool"),
        ai_model=_string(doc, "ai_model"),
        ai_plan=_string(doc, "ai_plan"),
        has_changes_key="changes" in doc,
    )
