"""Board data (`board.json`, schema version 1): the catalogue, the validator report and the renderer
index merged for the website. `digest` is filled in by the game repository's sync."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

from . import catalogue, credits, licences

REPO_URL = "https://github.com/aurbano/simhood-assets"
VERSION = 1


def _commit(root: Path) -> str | None:
    try:
        done = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    except OSError:
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


def build(root: Path, report: dict, index: dict, warn=lambda msg: print(f"WARN {msg}", file=sys.stderr)) -> dict:
    """The board document. Models missing from the renderer index get null thumbnails and a warning."""
    cat = catalogue.load(root)
    slot_status = {s["id"]: s for s in report["slots"]}
    models_by_slot: dict[str, list[dict]] = {}
    for m in report["models"]:
        if m["slot"]:
            models_by_slot.setdefault(m["slot"], []).append(m)
    for ms in models_by_slot.values():
        ms.sort(key=lambda m: m["id"])

    slots = []
    counts = {"slots": len(cat.slots), "have": 0, "stand-in": 0, "missing": 0, "procedural": 0, "models": len(report["models"]), "wanted": 0}
    for slot in cat.slots.values():
        row = slot_status.get(slot.id, {"models": [], "wanted_more": 0})
        have = len(row["models"])
        kind = cat.kinds.get(slot.kind)
        counts[slot.status] += 1
        counts["wanted"] += row["wanted_more"]
        slots.append(
            {
                "id": slot.id,
                "domain": slot.domain,
                "kind": slot.kind,
                "kind_name": kind.name if kind else slot.kind,
                "kind_drawn": bool(kind and kind.drawn),
                "name": slot.name,
                "size": dict(slot.size),
                "style": slot.style,
                "priority": slot.priority,
                "status": slot.status,
                "variants": slot.variants,
                "have": have,
                "wanted_more": row["wanted_more"],
                "tint": slot.tint,
                "plan": slot.plan,
                "notes": slot.notes,
                "budget": cat.budget(slot.kind, slot),
            }
        )

    rendered = index.get("models", {})
    models = []
    for m in report["models"]:
        entry = rendered.get(m["id"])
        if entry is None:
            warn(f"no renderer index entry for {m['id']}; its thumbnails are null")
        slot = cat.slots.get(m["slot"]) if m["slot"] else None
        siblings = models_by_slot.get(m["slot"], []) if m["slot"] else [m]
        evidence = bool(m["licence_evidence"])
        models.append(
            {
                "id": m["id"],
                "slot": m["slot"],
                "pack": m["pack"],
                "model_files": m["model_files"],
                "title": m["title"],
                "author": m["author"],
                "source": m["source"],
                "licence": m["licence"],
                "licence_name": licences.LICENCE_NAMES.get(m["licence"], m["licence"]),
                "licence_url": licences.LICENCE_URLS.get(m["licence"]),
                "changes": m["changes"],
                "contributors": m["contributors"],
                "attribution": m["attribution"],
                "ai": {
                    "status": m["ai_generated"],
                    "label": licences.ai_label(m["ai_generated"], evidence, m["ai_tool"], m["ai_model"]),
                    "tool": m["ai_tool"],
                    "model": m["ai_model"],
                },
                "triangles": m["triangles"],
                "size_m": m["size_m"],
                "thumb": entry.get("image") if entry else None,
                "thumb_tinted": entry.get("tinted") if entry else None,
                "variant": {
                    "index": [s["id"] for s in siblings].index(m["id"]) + 1,
                    "of": max(slot.variants if slot else 1, len(siblings)),
                },
                "stand_in": bool(slot and slot.status == "stand-in"),
            }
        )

    return {
        "version": VERSION,
        "commit": _commit(root),
        "digest": None,
        "repo_url": REPO_URL,
        "domains": [{"id": d, "name": n} for d, n in catalogue.DOMAINS.items()],
        "counts": counts,
        "slots": slots,
        "models": models,
        "examples": _examples(root, report, index, warn),
        "credits": credits.collect(root),
    }


def _examples(root: Path, report: dict, index: dict, warn) -> dict:
    out: dict = {"good": [], "rejected": []}
    path = root / "examples" / "examples.toml"
    if not path.is_file():
        return out
    doc = tomllib.loads(path.read_text(encoding="utf-8"))
    for entry in doc.get("good", []):
        out["good"].append({"model": entry["model"], "why": entry["why"]})
    rows = {r["id"]: r for r in report.get("examples", [])}
    rendered = index.get("examples", {})
    for entry in doc.get("rejected", []):
        row = rows.get(entry["id"], {})
        image = rendered.get(entry["id"])
        if isinstance(image, str):
            image = {"image": image}
        if image is None:
            warn(f"no renderer index entry for example {entry['id']}; its image is null")
        out["rejected"].append(
            {
                "id": entry["id"],
                "name": entry.get("name") or row.get("title") or entry["id"],
                "why": entry["why"],
                "image": image.get("image") if image else None,
                "licence": row.get("licence"),
                "author": row.get("author"),
                "source": row.get("source"),
            }
        )
    return out
