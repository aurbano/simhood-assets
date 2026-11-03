# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Starts the manifest of a model: measures the .glb the way the validator does, looks up the slot
it fills, and writes `<model>.toml` beside it with the id, slot, kind, size key and a suggested
`forward`, plus commented placeholders for what only you can say (licence, author, source...).

  uv run tools/new_model.py <path/to/model.glb> --slot <slot-id> [--repo <root>]

It never overwrites a manifest. Afterwards it prints the model's scaled size against the slot's
and its triangles against the budget, so you see at once whether the size key and `forward` are
right. The model must sit inside its pack, `packs/<pack>/.../<name>.glb`; when the pack has no
`pack.toml` the tool prints what to create.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from assets_lib import catalogue, gltf, manifests, normalise  # noqa: E402


class Problem(Exception):
    """Something the contributor must fix before a manifest can be written."""


def find_repo(model: Path, given: Path | None) -> Path:
    if given is not None:
        return given
    for parent in model.parents:
        if (parent / "catalogue" / "kinds.toml").is_file():
            return parent
    return Path(__file__).resolve().parent.parent


def find_pack(model: Path) -> tuple[Path, bool]:
    """(the pack folder, whether it has a pack.toml). The pack is the nearest folder above the model
    holding a pack.toml, else the folder directly under a `packs` folder."""
    for parent in model.parents:
        if (parent / "pack.toml").is_file():
            return parent, True
    for parent in model.parents:
        if parent.parent.name == "packs":
            return parent, False
    raise Problem(f"{model} is not inside a pack: put it at packs/<your-pack>/<folder>/{model.name} first")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-.") or "model"


def pack_template(pack: Path) -> str:
    return (
        f"create {pack / 'pack.toml'} with:\n"
        f'  id = "{slug(pack.name)}:base"\n'
        '  version = "1.0.0"\n'
        '  licence = "CC0-1.0"          # CC0-1.0, CC-BY-3.0 or CC-BY-4.0\n'
        '  ai_generated = "no"          # yes, no or unknown\n'
        '  author = "Your Name"\n'
        '  source = "https://..."'
    )


def namespace(pack: Path, has_toml: bool) -> str:
    if has_toml:
        try:
            return manifests.parse_pack((pack / "pack.toml").read_text(encoding="utf-8")).id.split(":", 1)[0]
        except (OSError, manifests.ManifestError) as e:
            raise Problem(f"{pack / 'pack.toml'}: {e}") from e
    return slug(pack.name)


def suggest_forward(low: tuple[float, float, float], high: tuple[float, float, float]) -> tuple[str, float, float]:
    """The horizontal axis of the longest footprint extent, as `+x` or `+z`, with the two extents."""
    ex, ez = high[0] - low[0], high[2] - low[2]
    return ("+x" if ex >= ez else "+z"), ex, ez


def manifest_text(
    model_id: str, slot: catalogue.Slot, size_key: str, size_value: float, forward: str, ex: float, ez: float,
    colours: list[str],
) -> str:
    axis = "x" if forward == "+x" else "z"
    other = "z" if axis == "x" else "x"
    lines = [
        f'id = "{model_id}"',
        f'slot = "{slot.id}"',
        f'kind = "{slot.kind}"',
        "",
    ]
    if size_key == "length":
        lines += [
            f"# The game scales the model so its length along `forward` is {size_value:g} m (the slot's length).",
            f"length = {size_value:g}",
        ]
    else:
        lines += [
            f"# The game scales the model so it is {size_value:g} m tall (the slot's height).",
            f"height = {size_value:g}",
        ]
    lines += [
        "",
        f"# Suggested: the model's longest footprint extent runs along {axis} ({max(ex, ez):.3g} against {min(ex, ez):.3g} along {other}, in model units).",
        "# The sign is a guess: `+` and `-` look the same to a measurement. Check which way the front points in",
        "# the review picture (`pnpm --dir tools render`, see CONTRIBUTING.md) and flip to the opposite sign",
        f'# ({"-x" if forward == "+x" else "-z"}) if the front is at the wrong end. A footprint close to square, or a model with no front, needs no care.',
        f'forward = "{forward}"',
        "",
    ]
    if slot.tint:
        lines += [
            f"# This slot is tinted ({slot.tint}). Pick the one flat colour of that part that nothing else in the model",
            "# uses, as `tint_key`; see CONTRIBUTING.md, \"Choosing a tint_key\".",
        ]
        if colours:
            lines.append(f"# Colours in the model: {', '.join(colours)}")
        lines += ['# tint_key = "#rrggbb"', ""]
    lines += [
        "# Fill these in (delete the `#`), or remove the ones pack.toml already gives for the whole pack.",
        "# They cannot be measured, so they are left for you; the validator names any that are missing.",
        '# title = ""                 # the work\'s name as its source gives it (required for CC-BY)',
        '# licence = "CC0-1.0"        # CC0-1.0, CC-BY-3.0 or CC-BY-4.0',
        '# author = ""                # who made the original',
        '# source = "https://..."     # where the model comes from',
        '# licence_evidence = "licences/<saved-licence-page>.html"   # third-party work only',
        '# ai_generated = "no"        # yes, no or unknown (yes also needs ai_tool, ai_model, ai_plan)',
        '# contributors = ["your-github-handle"]',
        "changes = []   # what you did to the original, oldest first, e.g. [\"converted to glb\", \"scaled\"]",
        "",
    ]
    return "\n".join(lines)


def run(model: Path, slot_id: str, repo: Path | None = None, out=print) -> int:
    model = model.resolve()
    if model.suffix.lower() != ".glb" or not model.is_file():
        raise Problem(f"{model} is not a .glb file (convert it first with tools/convert.sh)")
    root = find_repo(model, repo.resolve() if repo else None)
    cat = catalogue.load(root)
    if not cat.slots:
        detail = "; ".join(f"{p}: {m}" for p, _, m in cat.problems[:3]) or "no slots found"
        raise Problem(f"cannot read the catalogue under {root}: {detail}")
    slot = cat.slots.get(slot_id)
    if slot is None:
        near = difflib.get_close_matches(slot_id, list(cat.slots), n=5, cutoff=0.4)
        raise Problem(f"no slot `{slot_id}` in the catalogue" + (f"; did you mean {', '.join(near)}?" if near else ""))
    pack, has_toml = find_pack(model)
    target = model.with_suffix(".toml")
    if target.exists():
        raise Problem(f"{target} already exists; not overwriting it")
    model_id = f"{namespace(pack, has_toml)}:{slug(model.stem)}"
    try:
        manifests.check_id(model_id)
    except manifests.ManifestError as e:
        raise Problem(str(e)) from e

    data = gltf.read_model(model, pack)
    if not data.measured:
        raise Problem("the validator's reader cannot measure this model:\n  " + "\n  ".join(i.message for i in data.issues))

    klass = cat.kinds[slot.kind].cls
    size_key, size_value = ("length", slot.size["length"]) if klass == "vehicle" else ("height", slot.size["height"])
    forward, ex, ez = suggest_forward(data.bounds_min, data.bounds_max)
    colours = []
    for m in data.materials:
        if m["hex"] not in colours and not m["textured"]:
            colours.append(m["hex"])
    target.write_text(manifest_text(model_id, slot, size_key, size_value, forward, ex, ez, colours), encoding="utf-8")

    # What the validator will say about size and triangles for this manifest.
    scaled = normalise.normalise(data.bounds_min, data.bounds_max, forward, size_key, size_value)
    if isinstance(scaled, str):
        raise Problem(f"wrote {target}, but the model {scaled}")
    budget = cat.budget(slot.kind, slot)
    size = tuple(b - a for a, b in zip(data.bounds_min, data.bounds_max))
    out(f"wrote {target}")
    out(f"id {model_id}, slot {slot.id} ({slot.name}), kind {slot.kind}")
    out(f"model units: {size[0]:.3f} x {size[1]:.3f} x {size[2]:.3f} (x, y, z)")
    out(f"{size_key} = {size_value:g} with forward = \"{forward}\" scales it by {scaled.factor:.4g}")
    wanted = slot.size
    tol = slot.size_tolerance
    levels = {axis: (level, d) for axis, level, d, _ in normalise.compare(scaled, wanted, tol)}
    long_m, short_m = max(scaled.length, scaled.width), min(scaled.length, scaled.width)
    long_w, short_w = max(wanted["length"], wanted["width"]), min(wanted["length"], wanted["width"])
    for axis, got, want in (
        (normalise.LONG_SIDE, long_m, long_w),
        (normalise.SHORT_SIDE, short_m, short_w),
        ("height", scaled.height, wanted["height"]),
    ):
        level, d = levels.get(axis, ("ok", normalise.deviation(got, want)))
        shown = "ok" if level == "ok" else level.upper()
        flat = " (flat piece, not compared)" if got < normalise.FLAT_AXIS_M else ""
        out(f"  {axis}: {got:.2f} m against the slot's {want:.2f} m ({d * 100:.0f}% off) {shown}{flat}")
    out(f"  scaled footprint {scaled.length:.2f} m along forward by {scaled.width:.2f} m across")
    if budget is not None:
        state = "ok" if data.triangles <= budget else "OVER BUDGET"
        out(f"triangles: {data.triangles} of {budget} {state}")
    if any(level == "fail" for level, _ in levels.values()):
        if abs(ex - ez) > 0.1 * max(ex, ez):
            out("a size is far off: if length and width look swapped, try the other `forward` values (+x, -x, +z, -z)")
        else:
            out("a size is far off and the footprint is nearly square, so `forward` is not the cause: the model's proportions do not fit the slot")
    if not has_toml:
        out("")
        out(f"{pack.name} has no pack.toml; " + pack_template(pack))
    out("")
    out("next: fill in the commented lines in the manifest, then run `uv run tools/validate.py`")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model", type=Path, help="the .glb, inside packs/<pack>/")
    parser.add_argument("--slot", required=True, help="the catalogue slot the model fills, e.g. vehicles.panel-van")
    parser.add_argument("--repo", type=Path, help="the repository root (default: found above the model, else this repository)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        return run(args.model, args.slot, args.repo)
    except Problem as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
