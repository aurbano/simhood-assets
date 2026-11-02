"""Credits generated from the manifests (design D8, ACON-011): never written by hand.

CC-BY-3.0 works are credited with the author (or the party named in `attribution`), title, source,
licence link and changes (licence section 4(b)); CC-BY-4.0 works the same, with a pointer to the saved
evidence for any notice that must be kept (section 3(a)); CC0 works are listed under their author for
provenance; contributors are acknowledged apart from authors; AI-generated models name their tool.
"""

from __future__ import annotations

from pathlib import Path

from . import licences, repo
from .checker import _effective


def _models(root: Path) -> list[dict]:
    """Every valid pack manifest with its effective provenance, in path order."""
    tree = repo.scan(root)
    out = []
    for pack in tree.packs:
        for entry in pack.models:
            m = entry.manifest
            if m is None:
                continue
            eff = _effective(m, pack.info)
            out.append(
                {
                    "id": m.id,
                    "pack": pack.name,
                    "title": m.title,
                    "author": eff.author or "",
                    "attribution": m.attribution,
                    "source": eff.source,
                    "licence": eff.licence,
                    "changes": list(m.changes),
                    "evidence": f"{pack.name}/{m.licence_evidence}" if m.licence_evidence else None,
                    "contributors": list(m.contributors),
                    "ai": eff.ai_generated,
                    "ai_tool": m.ai_tool,
                    "ai_model": m.ai_model,
                }
            )
    return out


def collect(root: Path) -> dict:
    """The credits as the JSON document the board reads."""
    models = _models(root)
    groups: dict[tuple, dict] = {}
    cc0: dict[str, list[dict]] = {}
    contributors: dict[str, list[str]] = {}
    ai = []
    for m in models:
        if licences.is_cc_by(m["licence"]):
            key = (m["title"], m["author"], m["attribution"], m["source"], m["licence"], tuple(m["changes"]), m["evidence"])
            group = groups.setdefault(
                key,
                {
                    "title": m["title"],
                    "author": m["author"],
                    "attribution": m["attribution"],
                    "source": m["source"],
                    "licence": m["licence"],
                    "licence_name": licences.LICENCE_NAMES[m["licence"]],
                    "licence_url": licences.LICENCE_URLS[m["licence"]],
                    "changes": m["changes"],
                    "evidence": m["evidence"],
                    "models": [],
                },
            )
            group["models"].append(m["id"])
        elif m["licence"] == "CC0-1.0":
            cc0.setdefault(m["author"], []).append({"id": m["id"], "title": m["title"], "source": m["source"]})
        for handle in m["contributors"]:
            contributors.setdefault(handle, []).append(m["id"])
        if m["ai"] == "yes":
            ai.append({"id": m["id"], "tool": m["ai_tool"], "model": m["ai_model"]})
    return {
        "cc_by": sorted(groups.values(), key=lambda g: ((g["title"] or "").lower(), g["models"][0])),
        "cc0": [{"author": a, "models": sorted(ms, key=lambda x: x["id"])} for a, ms in sorted(cc0.items(), key=lambda kv: kv[0].lower())],
        "contributors": [{"handle": h, "models": sorted(ids)} for h, ids in sorted(contributors.items(), key=lambda kv: kv[0].lower())],
        "ai_generated": sorted(ai, key=lambda x: x["id"]),
    }


def to_markdown(data: dict, ai_ids: dict[str, str] | None = None) -> str:
    """The `CREDITS.md` text for a credits document."""
    ai_by_id = {a["id"]: a for a in data["ai_generated"]}

    def ai_note(ids: list[str]) -> str:
        notes = [f" `{i}` is AI-generated with {ai_by_id[i]['tool']} ({ai_by_id[i]['model']})." for i in ids if i in ai_by_id]
        return "".join(notes)

    lines = [
        "# Credits",
        "",
        "Generated from the pack manifests by `tools/credits.py`; do not edit by hand.",
        "",
        "## Works that need a credit (Creative Commons Attribution)",
        "",
    ]
    if not data["cc_by"]:
        lines += ["None.", ""]
    for version in ("CC-BY-3.0", "CC-BY-4.0"):
        group = [g for g in data["cc_by"] if g["licence"] == version]
        if not group:
            continue
        lines += [f"### {licences.LICENCE_NAMES[version]}", ""]
        for g in group:
            who = g["attribution"] if g["attribution"] else f"by {g['author']}"
            title = f"\"{g['title']}\"" if g["title"] else "Untitled"
            text = f"- {title} {who}. Source: <{g['source']}>. Licensed under [{g['licence_name']}]({g['licence_url']})."
            if g["changes"]:
                text += f" Changes: {'; '.join(g['changes'])}."
            if version == "CC-BY-4.0" and g["evidence"]:
                text += f" Any copyright, licence or disclaimer notice is kept in `{g['evidence']}`."
            text += " Models: " + ", ".join(f"`{i}`" for i in g["models"]) + "."
            text += ai_note(g["models"])
            lines.append(text)
        lines.append("")
    lines += ["## CC0 works (no credit needed, listed for provenance)", ""]
    if not data["cc0"]:
        lines += ["None.", ""]
    for entry in data["cc0"]:
        lines += [f"### {entry['author']}", ""]
        for m in entry["models"]:
            text = f"- {m['title']} (`{m['id']}`)" if m["title"] else f"- `{m['id']}`"
            if m["source"]:
                text += f", <{m['source']}>"
            text += "." + ai_note([m["id"]])
            lines.append(text)
        lines.append("")
    if data["contributors"]:
        lines += ["## Contributors", "", "People who submitted or edited models, apart from their authors.", ""]
        for c in data["contributors"]:
            lines.append(f"- @{c['handle']}: " + ", ".join(f"`{i}`" for i in c["models"]))
        lines.append("")
    if data["ai_generated"]:
        lines += ["## AI-generated models", ""]
        for a in data["ai_generated"]:
            lines.append(f"- `{a['id']}`: AI-generated with {a['tool']} ({a['model']}).")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
