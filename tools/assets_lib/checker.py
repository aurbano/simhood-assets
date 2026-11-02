"""The validator: every rule of specs/asset-catalogue and specs/asset-contributions that runs
without a network or a GPU, in strict mode over the whole repository or in pull request mode.

`validate()` returns the report document (contract, report version 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import catalogue as catmod
from . import gltf, khronos, licences, manifests, normalise, prdiff, repo, waivers
from .manifests import AI_PLANS, Manifest

REPORT_VERSION = 1
HUNYUAN_REASON = (
    "Hunyuan3D's licence forbids using or showing its outputs in the EU, the UK and South Korea, so its "
    "models cannot ship"
)
MESHY_FREE_REASON = (
    "Meshy's free plan makes its output CC BY 4.0 to Meshy and needs a credit on the store page, so it "
    "cannot ship"
)


@dataclass
class Findings:
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)

    def error(self, path: str, rule: str, message: str, model: str | None = None) -> None:
        self.errors.append({"path": path, "model": model, "rule": rule, "message": message})

    def warn(self, path: str, rule: str, message: str, model: str | None = None) -> None:
        self.warnings.append({"path": path, "model": model, "rule": rule, "message": message})


@dataclass
class Effective:
    """A model's provenance after the pack's values fill the gaps."""

    licence: str | None
    author: str | None
    source: str | None
    ai_generated: str | None


def _effective(m: Manifest, info: manifests.PackInfo | None) -> Effective:
    return Effective(
        m.licence or (info.licence if info else None),
        m.author or (info.author if info else None),
        m.source or (info.source if info else None),
        m.ai_generated or (info.ai_generated if info else None),
    )


def _norm(text: str | None) -> str:
    return "".join(c for c in (text or "").lower() if c.isalnum())


# ------------------------------------------------------------------------------------------------
# Per-manifest rules


def check_provenance(
    f: Findings, entry: repo.ModelEntry, eff: Effective, evidence_root: Path | None, require_ai: bool = True
) -> None:
    m = entry.manifest
    assert m is not None
    path, mid = entry.manifest_path, m.id
    third_party = m.licence_evidence is not None
    if eff.licence not in licences.ALLOWLIST:
        shown = f"`{eff.licence}`" if eff.licence else "no licence"
        f.error(path, "licence.allowlist", f"has {shown}; the allowlist is {', '.join(licences.ALLOWLIST)}", mid)
    elif not third_party and eff.licence not in licences.ORIGINAL_LICENCES:
        f.error(path, "licence.original", f"is original work under {eff.licence}; original work is CC0-1.0 or CC-BY-4.0", mid)
    if not (eff.author or "").strip():
        f.error(path, "provenance.author", "has no `author` (in its manifest or its pack.toml)", mid)
    if eff.ai_generated is None and require_ai:
        f.error(path, "provenance.ai", "has no `ai_generated` (yes, no or unknown)", mid)
    if third_party:
        evidence = m.licence_evidence
        assert evidence is not None
        parts = evidence.replace("\\", "/").split("/")
        if parts[0] != "licences" or len(parts) < 2 or any(p in ("", ".", "..") for p in parts):
            f.error(path, "licence.evidence", f"licence_evidence `{evidence}` must be a path under the pack's licences/ folder", mid)
        elif evidence_root is None or not (evidence_root / evidence).is_file() or (evidence_root / evidence).is_symlink():
            f.error(path, "licence.evidence", f"licence_evidence `{evidence}` is not a file in the pack", mid)
        if not (eff.source or "").startswith("https://") or len((eff.source or "")) <= len("https://"):
            f.error(path, "licence.source", f"is a third-party work, so `source` must be an https URL (found `{eff.source}`)", mid)
    elif eff.ai_generated not in ("yes", "no") and eff.ai_generated is not None:
        f.error(
            path, "provenance.ai-original",
            f"is original work (no `licence_evidence`) and says ai_generated = `{eff.ai_generated}`; "
            "original work must say `yes` or `no` (`unknown` is only for third-party works whose source does not say)",
            mid,
        )
    if licences.is_cc_by(eff.licence) and not (m.title or "").strip():
        f.error(path, "licence.title", f"is {eff.licence} and needs a `title` (CC-BY credits name the work)", mid)


def check_ai(f: Findings, entry: repo.ModelEntry, eff: Effective) -> None:
    m = entry.manifest
    assert m is not None
    path, mid = entry.manifest_path, m.id
    if eff.ai_generated == "yes":
        for key in ("ai_tool", "ai_model", "ai_plan"):
            if not (getattr(m, key) or "").strip():
                f.error(path, "ai.missing", f"says ai_generated = `yes` and needs `{key}` (tool, model and plan are disclosed)", mid)
        if m.ai_plan and m.ai_plan not in AI_PLANS:
            f.error(path, "ai.plan", f"ai_plan `{m.ai_plan}` is not one of {', '.join(AI_PLANS)}", mid)
    if "meshy" in _norm(m.ai_tool) and m.ai_plan == "free":
        f.error(path, "ai.refused", MESHY_FREE_REASON, mid)
    if "hunyuan3d" in _norm(m.ai_tool) or "hunyuan3d" in _norm(m.ai_model):
        f.error(path, "ai.refused", HUNYUAN_REASON, mid)


# ------------------------------------------------------------------------------------------------
# Models


@dataclass
class Measured:
    data: gltf.ModelData | None = None
    scaled: normalise.Scaled | None = None
    budget: int | None = None


def _model_kind_allows_motion(kind: str) -> bool:
    return kind.startswith("person.") or kind.startswith("animal.")


def _gltf_checkable(entry: repo.ModelEntry, grandfathered: bool) -> bool:
    return entry.model_path is not None and (
        entry.model_path.endswith(".glb") or (entry.model_path.endswith(".gltf") and grandfathered)
    )


def _read_and_check_model(
    f: Findings,
    root: Path,
    entry: repo.ModelEntry,
    grandfathered: bool,
    khronos_results: dict[Path, dict] | None,
    pack_root: Path,
    style: bool = True,
) -> Measured:
    """glTF rules for one model: the Khronos verdict, the files and the measuring profile.

    The texture limits are style rules (photographic textures are against the style guide), so
    `style=False`, for the rejected examples that exist to break it, leaves them out.
    """
    m = entry.manifest
    assert m is not None and entry.model_path is not None
    model_file = root / entry.model_path
    data = gltf.read_model(model_file, pack_root, graph_ok=grandfathered)
    for issue in data.issues:
        if not style and issue.rule == "gltf.texture":
            continue
        f.error(entry.model_path, issue.rule, issue.message, m.id)
    if khronos_results is not None:
        verdict = khronos_results.get(model_file)
        if verdict is not None:
            if verdict["fatal"]:
                f.error(entry.model_path, "gltf.validator", f"the glTF Validator cannot read it: {verdict['fatal']}", m.id)
            for e in verdict["errors"]:
                where = f" at {e['pointer']}" if e.get("pointer") else ""
                f.error(entry.model_path, "gltf.validator", f"glTF Validator {e['code']}: {e['message']}{where}", m.id)
    if (data.animated or data.skinned) and not _model_kind_allows_motion(m.kind):
        what = "animations" if data.animated else "skins"
        f.error(entry.model_path, "gltf.animation", f"has {what}, which only person.* and animal.* kinds may (kind is `{m.kind}`)", m.id)
    result = Measured(data=data)
    if data.measured and data.bounds_min and data.bounds_max:
        scaled = normalise.normalise(data.bounds_min, data.bounds_max, m.forward, m.measure, m.measure_value)
        if isinstance(scaled, str):
            f.error(entry.model_path, "size.invalid", scaled, m.id)
            data.measured = False
        else:
            result.scaled = scaled
    return result


def _check_slot_fit(
    f: Findings, entry: repo.ModelEntry, measured: Measured, slot: catmod.Slot | None, cat: catmod.Catalogue
) -> None:
    m = entry.manifest
    assert m is not None
    path = entry.manifest_path
    measured.budget = cat.budget(slot.kind if slot else m.kind, slot)
    data, scaled = measured.data, measured.scaled
    if data is None or data.triangles is None or scaled is None:
        return
    if measured.budget is not None and data.triangles > measured.budget:
        source = "the slot's max_triangles" if slot and slot.max_triangles is not None else "its kind's class budget"
        f.error(
            entry.model_path or path, "budget.triangles",
            f"has {data.triangles} triangles, over the budget of {measured.budget} ({source})", m.id,
        )
    if slot is None:
        return
    found = normalise.compare(scaled, slot.size, slot.size_tolerance)
    if not found:
        return
    long_m, short_m = max(scaled.length, scaled.width), min(scaled.length, scaled.width)
    long_w, short_w = max(slot.size["length"], slot.size["width"]), min(slot.size["length"], slot.size["width"])
    size = f"{long_m:.2f} x {short_m:.2f} x {scaled.height:.2f} m"
    wanted = f"{long_w:g} x {short_w:g} x {slot.size['height']:g} m"
    order = "footprint long side x footprint short side x height"
    parts = {"fail": [], "warn": []}
    for axis, level, d, tol in found:
        parts[level].append(f"{axis} is {d * 100:.0f}% off")
    tol = found[0][3]
    if parts["fail"]:
        f.error(
            entry.model_path or path, "size.fail",
            f"scaled size {size} ({order}) is far from slot {slot.id}'s {wanted}: "
            + ", ".join(parts["fail"]) + f" (limit {tol * 100:.0f}%)", m.id,
        )
    if parts["warn"]:
        f.warn(
            entry.model_path or path, "size.warn",
            f"scaled size {size} ({order}) differs from slot {slot.id}'s {wanted}: "
            + ", ".join(parts["warn"]) + f" (warns beyond {normalise.WARN_SHARE * tol * 100:.0f}%)", m.id,
        )


def origin(m: Manifest) -> str:
    """How the model presents itself: a third-party work names its saved licence evidence, original
    work does not. A third-party model filed without evidence cannot be told apart, so a reviewer must
    see this line."""
    if m.licence_evidence:
        return f"third-party (licence evidence: {m.licence_evidence})"
    return "original (no licence_evidence)"


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def _row(
    entry: repo.ModelEntry, pack: repo.PackEntry | None, eff: Effective, measured: Measured | None, change: str,
    root: Path, extra: dict | None = None,
) -> dict:
    m = entry.manifest
    assert m is not None
    data = measured.data if measured else None
    scaled = measured.scaled if measured else None
    files = [entry.model_path] if entry.model_path else []
    if entry.model_path and data is not None:
        base = f"{pack.path}/" if pack else ""
        files.extend(f"{base}{r}" for r in data.resources)
    row = {
        "id": m.id,
        "pack": entry.pack,
        "pack_id": pack.info.id if pack and pack.info else None,
        "model_path": entry.model_path,
        "manifest_path": entry.manifest_path,
        "model_files": files,
        "slot": m.slot,
        "kind": m.kind,
        "measure": m.measure,
        "measure_value": m.measure_value,
        "forward": m.forward,
        "factor": scaled.factor if scaled else None,
        "flat_shading": m.flat_shading,
        "tint_key": m.tint_key,
        "tint_tolerance": m.tint_tolerance,
        "triangles": data.triangles if data and data.measured else None,
        "budget": measured.budget if measured else None,
        "textures": data.textures if data is not None else None,
        "size_m": (
            {"length": _round(scaled.length), "width": _round(scaled.width), "height": _round(scaled.height)}
            if scaled else None
        ),
        "licence": eff.licence,
        "author": eff.author,
        "title": m.title,
        "attribution": m.attribution,
        "source": eff.source,
        "licence_evidence": m.licence_evidence,
        "origin": origin(m),
        "changes": list(m.changes),
        "contributors": list(m.contributors),
        "ai_generated": eff.ai_generated,
        "ai_tool": m.ai_tool,
        "ai_model": m.ai_model,
        "ai_plan": m.ai_plan,
        "materials": list(data.materials) if data is not None and data.measured else [],
        "change": change,
    }
    if extra:
        row.update(extra)
    return row


# ------------------------------------------------------------------------------------------------
# The run


def validate(
    root: Path,
    *,
    pr: bool = False,
    base: Path | None = None,
    pr_author: str | None = None,
    gltf_validator: bool = True,
) -> dict:
    """Validates the repository at `root` and returns the report document."""
    root = root.resolve()
    f = Findings()
    tree = repo.scan(root)
    cat = catmod.load(root)
    for path, rule, message in cat.problems:
        f.error(path, rule, message)

    changes: prdiff.FileChanges | None = None
    classified: list[prdiff.ModelChange] | None = None
    base_tree: repo.Tree | None = None
    if pr:
        assert base is not None
        base = base.resolve()
        base_tree = repo.scan(base)
        changes = prdiff.diff_trees(base, root)
        classified = prdiff.classify(tree, base_tree, changes)
    change_of = {mc.entry.manifest_path: mc for mc in classified or [] if mc.change != "deleted"}

    def affected(entry: repo.ModelEntry) -> bool:
        return classified is None or change_of[entry.manifest_path].change != "unchanged"

    # Pack files and manifests
    all_entries: list[tuple[repo.PackEntry, repo.ModelEntry]] = []
    for pack in tree.packs:
        if pack.info_error is not None:
            f.error(f"{pack.path}/pack.toml", "pack.invalid", pack.info_error)
        for path, rule, message in repo.check_pack_files(root, pack):
            f.error(path, rule, message)
        for orphan in pack.orphans:
            f.error(orphan, "manifest.missing", "has no manifest (a .toml of the same name beside it)")
        for entry in pack.models:
            all_entries.append((pack, entry))

    # Manifests: validity, duplicates, slots
    seen_ids: dict[str, str] = {}
    by_slot: dict[str, list[str]] = {}
    manifest_paths_by_slot: dict[str, list[str]] = {}
    for pack, entry in all_entries:
        if entry.manifest is None:
            f.error(entry.manifest_path, "manifest.invalid", entry.manifest_error or "is invalid")
            continue
        m = entry.manifest
        if entry.model_path is None:
            f.error(entry.manifest_path, "manifest.model-missing", "has no .glb or .gltf of the same name beside it", m.id)
        if m.id in seen_ids:
            f.error(entry.manifest_path, "manifest.duplicate-id", f"id `{m.id}` is also used by {seen_ids[m.id]}", m.id)
        else:
            seen_ids[m.id] = entry.manifest_path
        if m.slot:
            by_slot.setdefault(m.slot, []).append(m.id)
            manifest_paths_by_slot.setdefault(m.slot, []).append(entry.manifest_path)
        # Slot rules (ACAT-003)
        slot = cat.slots.get(m.slot) if m.slot else None
        if not m.slot:
            f.error(entry.manifest_path, "slot.missing", "has no `slot`; every shipped model names the catalogue slot it fills", m.id)
        elif slot is None:
            f.error(entry.manifest_path, "slot.unknown", f"names slot `{m.slot}`, which the catalogue does not list", m.id)
        elif slot.kind != m.kind:
            f.error(entry.manifest_path, "slot.kind", f"has kind `{m.kind}` but slot `{slot.id}` is kind `{slot.kind}`", m.id)
        elif m.kind not in cat.kinds:
            f.error(entry.manifest_path, "manifest.kind", f"kind `{m.kind}` is not listed in catalogue/kinds.toml", m.id)
        if slot is None and m.kind not in cat.kinds and not m.slot:
            f.error(entry.manifest_path, "manifest.kind", f"kind `{m.kind}` is not listed in catalogue/kinds.toml", m.id)

    # Slot consistency (ACAT-004) and wanted variants (ACAT-007). A manifest that could not be read, or
    # one naming a slot the catalogue lacks, may be the model a `have` slot is missing.
    may_follow = any(e["rule"] in ("manifest.invalid", "slot.unknown") for e in f.errors)
    knock_on = " (this may follow from an error above)" if may_follow else ""
    slot_rows = []
    for sid, slot in cat.slots.items():
        named = by_slot.get(sid, [])
        paths = manifest_paths_by_slot.get(sid, [])
        if slot.status in ("have", "stand-in") and not named:
            f.error(slot.file, "slot.status", f"slot `{sid}` is `{slot.status}` but no manifest names it (set it to `missing` or add a model){knock_on}")
        if slot.status in ("missing", "procedural") and named:
            f.error(
                slot.file, "slot.status",
                f"slot `{sid}` is `{slot.status}` but {len(named)} manifest{'s' if len(named) > 1 else ''} name{'' if len(named) > 1 else 's'} it: "
                + ", ".join(paths),
            )
        slot_rows.append({"id": sid, "status": slot.status, "models": sorted(named), "wanted_more": 0 if slot.status == "procedural" else max(0, slot.variants - len(named))})

    # glTF checks: which models get measured
    to_check = [(p, e) for p, e in all_entries if e.manifest is not None and e.model_path and affected(e)]
    example_checks = [e for e in tree.examples if e.manifest is not None and e.model_path]
    examples_changed = changes is not None and any(c.startswith("examples/") for c in changes.changed)
    if pr and not examples_changed:
        example_checks = []
    khronos_results: dict[Path, dict] | None = None
    if gltf_validator:
        files = [
            root / e.model_path
            for p, e in to_check
            if _gltf_checkable(e, p.name in repo.GRANDFATHERED_PACKS)
        ] + [root / e.model_path for e in example_checks if e.model_path and e.model_path.endswith(".glb")]
        if files:
            try:
                khronos_results = khronos.run(files)
            except khronos.Unavailable as e:
                f.error("tools/gltf_validate.mjs", "gltf.validator-unavailable", str(e))

    # Per-model rules and report rows
    rows: list[dict] = []
    for pack, entry in to_check:
        m = entry.manifest
        assert m is not None
        eff = _effective(m, pack.info)
        check_provenance(f, entry, eff, root / pack.path)
        check_ai(f, entry, eff)
        slot = cat.slots.get(m.slot) if m.slot else None
        measured: Measured | None = None
        grandfathered = pack.name in repo.GRANDFATHERED_PACKS
        if _gltf_checkable(entry, grandfathered):
            measured = _read_and_check_model(f, root, entry, grandfathered, khronos_results, root / pack.path)
            _check_slot_fit(f, entry, measured, slot, cat)
        else:
            measured = Measured(budget=cat.budget(slot.kind if slot else m.kind, slot))
        mc = change_of.get(entry.manifest_path)
        extra = {"renamed_from": mc.renamed_from} if mc and mc.renamed_from else None
        rows.append(_row(entry, pack, eff, measured, mc.change if mc else "unchanged", root, extra))

    # Pull request rules
    if pr and changes is not None and classified is not None and base_tree is not None:
        assert base is not None
        for mc in classified:
            if mc.change == "deleted":
                m = mc.entry.manifest
                if m is not None:
                    base_pack = next((p for p in base_tree.packs if p.name == mc.entry.pack), None)
                    eff = _effective(m, base_pack.info if base_pack else None)
                    rows.append(_row(mc.entry, base_pack, eff, None, "deleted", base))
            elif mc.change in ("changed", "renamed") and mc.base_entry is not None:
                head_pack = next((p for p in tree.packs if p.name == mc.entry.pack), None)
                base_pack = next((p for p in base_tree.packs if p.name == mc.base_entry.pack), None)
                if mc.entry.manifest and mc.base_entry.manifest:
                    old_eff = _effective(mc.base_entry.manifest, base_pack.info if base_pack else None)
                    new_eff = _effective(mc.entry.manifest, head_pack.info if head_pack else None)
                    for rule, message in prdiff.check_append_only(mc.base_entry, mc.entry, old_eff.author, new_eff.author):
                        f.error(mc.entry.manifest_path, rule, message, mc.entry.manifest.id)
        if changes.added_bytes() > repo.MAX_PR_BYTES:
            f.error("", "files.pr-size", f"adds {changes.added_bytes() / (1024 * 1024):.1f} MiB; a pull request may add at most 20 MiB")
        owners = prdiff.codeowners(base)
        if (pr_author or "").lower() not in owners:
            touched = {
                mc.entry.manifest.slot
                for mc in classified
                if mc.change in ("added", "changed", "renamed") and mc.entry.manifest and mc.entry.manifest.slot
            }
            for path, rule, message in prdiff.check_scope(changes, base, root, touched):
                f.error(path, rule, message)

    # Examples (file, licence and provenance rules; no slot, budget or size)
    example_rows = _check_examples(f, root, tree, example_checks, khronos_results, set(seen_ids), pr, examples_changed)

    only = {r["id"] for r in rows} if pr else None  # a pull request's affected models, deleted ones included
    waived = _apply_waivers(f, root, tree, only)
    errors = sorted(f.errors, key=lambda e: (e["path"], e["rule"], e["message"]))
    warnings = sorted(f.warnings, key=lambda e: (e["path"], e["rule"], e["message"]))
    rows.sort(key=lambda r: (r["manifest_path"], r["id"]))
    return {
        "version": REPORT_VERSION,
        "mode": "pr" if pr else "strict",
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "waived": sorted(waived, key=lambda e: (e["path"], e["rule"], e["message"])),
        "models": rows,
        "slots": slot_rows,
        "examples": example_rows,
    }


def _apply_waivers(f: Findings, root: Path, tree: repo.Tree, only: set[str] | None = None) -> list[dict]:
    """Moves the findings a still-valid waiver excuses from the errors to the waived list.

    `only` limits the check to those model ids (a pull request's affected models); strict mode checks
    every waiver."""
    loaded, problems = waivers.load(root)
    for message in problems:
        f.error(waivers.PATH, "waiver.schema", message)
    entries = {e.manifest.id: e for p in tree.packs for e in p.models if e.manifest is not None}
    waived: list[dict] = []
    for w in loaded:
        if only is not None and w.model not in only:
            continue
        entry = entries.get(w.model)
        if entry is None or entry.model_path is None:
            f.error(waivers.PATH, "waiver.stale", f"waiver for `{w.model}`: no such model any more; remove the waiver", w.model)
            continue
        actual = repo.sha256(root / entry.model_path)
        if actual != w.file_sha256:
            f.error(
                waivers.PATH, "waiver.stale",
                f"waiver for `{w.model}`: {entry.model_path} changed (sha256 is now {actual}), so the waiver no longer applies and the full rules do; remove the waiver", w.model,
            )
            continue
        for rule in w.rules:
            hits = [e for e in f.errors if e["model"] == w.model and e["rule"] == rule]
            if not hits:
                f.warn(waivers.PATH, "waiver.unused", f"waiver for `{w.model}` excuses `{rule}`, which the model no longer breaks", w.model)
            for hit in hits:
                f.errors.remove(hit)
                waived.append({**hit, "reason": w.reason})
    return waived


def _check_examples(
    f: Findings, root: Path, tree: repo.Tree, entries: list[repo.ModelEntry], khronos_results: dict[Path, dict] | None,
    shipped_ids: set[str], pr: bool, examples_changed: bool,
) -> list[dict]:
    rows: list[dict] = []
    examples_toml = root / "examples" / "examples.toml"
    rejected_dir = root / "examples" / "rejected"
    if rejected_dir.is_dir() and (not pr or entries):
        pseudo = repo.PackEntry("rejected", "examples/rejected")
        for path, rule, message in repo.check_pack_files(root, pseudo):
            f.error(path, rule, message)
    seen_ids: set[str] = set()
    for entry in tree.examples:
        if entry.manifest is None:
            f.error(entry.manifest_path, "manifest.invalid", entry.manifest_error or "is invalid")
            continue
        m = entry.manifest
        if m.id in seen_ids:
            f.error(entry.manifest_path, "manifest.duplicate-id", f"example id `{m.id}` is used twice", m.id)
        seen_ids.add(m.id)
        if entry.model_path is None:
            f.error(entry.manifest_path, "manifest.model-missing", "has no .glb of the same name beside it", m.id)
        if entry not in entries:
            continue
        eff = _effective(m, None)
        check_provenance(f, entry, eff, rejected_dir)
        check_ai(f, entry, eff)
        measured: Measured | None = None
        if entry.model_path and entry.model_path.endswith(".glb"):
            measured = _read_and_check_model(f, root, entry, False, khronos_results, rejected_dir, style=False)
        elif entry.model_path:
            f.error(entry.model_path, "files.type", "an example model must be a .glb", m.id)
        rows.append(_row(entry, None, eff, measured, "unchanged", root))
    if examples_toml.is_file() and (not pr or examples_changed):
        _check_examples_toml(f, examples_toml, shipped_ids, seen_ids)
    rows.sort(key=lambda r: r["id"])
    return rows


def _check_examples_toml(f: Findings, path: Path, shipped_ids: set[str], example_ids: set[str]) -> None:
    import tomllib

    rel = "examples/examples.toml"
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        f.error(rel, "examples.schema", f"cannot be read: {e}")
        return
    for key in doc:
        if key not in ("good", "rejected"):
            f.error(rel, "examples.schema", f"unknown top-level key `{key}`")
    for kind, ids, what in (("good", shipped_ids, "a shipped model"), ("rejected", example_ids, "an example under examples/rejected/")):
        entries = doc.get(kind, [])
        if not isinstance(entries, list):
            f.error(rel, "examples.schema", f"`{kind}` must be an array of tables")
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                f.error(rel, "examples.schema", f"a `{kind}` entry is not a table")
                continue
            ref = entry.get("model") if kind == "good" else entry.get("id")
            if not isinstance(ref, str) or ref not in ids:
                f.error(rel, "examples.schema", f"a `{kind}` entry names `{ref}`, which is not {what}")
            if not isinstance(entry.get("why"), str) or not entry["why"].strip():
                f.error(rel, "examples.schema", f"a `{kind}` entry for `{ref}` has no `why`")
