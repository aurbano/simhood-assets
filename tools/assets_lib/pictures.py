"""What the preview publisher accepts from a render artifact (design D10, ACON-018).

The artifact comes from a run of the pull request's own code, so nothing in it is trusted but
bytes that really are small PNG pictures, for model ids the verdict lists. Every limit lives here.
"""

from __future__ import annotations

import io
import json
import re
import stat
import struct
import zipfile
from dataclasses import dataclass, field

from .comment import md

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_ENTRIES = 64
MAX_TOTAL = 40 * 1024 * 1024
MAX_PNG = 2 * 1024 * 1024
MAX_SIDE = 1024
MAX_INDEX = 1024 * 1024
MAX_ZIP = 48 * 1024 * 1024
INDEX_NAME = "index.json"
META_NAME = "meta.json"
KINDS = (("image", "plain"), ("tinted", "tinted"), ("before", "before"))
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,150}$")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def png_problem(data: bytes) -> str | None:
    """Why `data` is not an acceptable picture, or None."""
    if len(data) > MAX_PNG:
        return f"is {len(data)} bytes; the limit is {MAX_PNG}"
    if len(data) < 33 or not data.startswith(PNG_SIGNATURE):
        return "does not start with the PNG signature"
    length, kind = struct.unpack(">I4s", data[8:16])
    if length != 13 or kind != b"IHDR":
        return "has no IHDR chunk after the signature"
    width, height = struct.unpack(">II", data[16:24])
    if not (1 <= width <= MAX_SIDE and 1 <= height <= MAX_SIDE):
        return f"is {width} × {height} px; the limit is {MAX_SIDE} × {MAX_SIDE}"
    return None


@dataclass
class Unpacked:
    pngs: dict[str, bytes] = field(default_factory=dict)  # accepted pictures by entry name
    index: object | None = None  # the decoded index.json
    refused: list[tuple[str, str]] = field(default_factory=list)  # (entry name, reason)
    fatal: str | None = None  # the whole artifact was refused


def unpack(zip_bytes: bytes) -> Unpacked:
    out = Unpacked()
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        out.fatal = "the artifact is not a zip file"
        return out
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            out.fatal = f"the artifact has {len(infos)} entries; the limit is {MAX_ENTRIES}"
            return out
        if sum(i.file_size for i in infos) > MAX_TOTAL:
            out.fatal = f"the artifact expands to more than {MAX_TOTAL // (1024 * 1024)} MB"
            return out
        names = [i.filename for i in infos]
        if len(set(names)) != len(names):
            out.fatal = "the artifact lists the same entry name twice"
            return out
        spent = 0
        for info in infos:
            name = info.filename
            why = _entry_problem(info)
            if why is None and name == META_NAME:
                continue  # informative only; never read
            if why is None:
                try:
                    with zf.open(info) as f:
                        data = f.read((MAX_INDEX if name == INDEX_NAME else MAX_PNG) + 1)
                except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as e:
                    out.refused.append((name, f"could not be read ({type(e).__name__})"))
                    continue
                spent += len(data)
                if spent > MAX_TOTAL:
                    out.fatal = f"the artifact expands to more than {MAX_TOTAL // (1024 * 1024)} MB"
                    return out
                if name == INDEX_NAME:
                    if len(data) > MAX_INDEX:
                        out.refused.append((name, f"is more than {MAX_INDEX} bytes"))
                    else:
                        try:
                            out.index = json.loads(data)
                        except ValueError:
                            out.refused.append((name, "is not valid JSON"))
                else:
                    why = png_problem(data)
                    if why is None:
                        out.pngs[name] = data
                    else:
                        out.refused.append((name, why))
            else:
                out.refused.append((name, why))
    return out


def _entry_problem(info: zipfile.ZipInfo) -> str | None:
    name = info.filename
    if name.endswith("/") or "/" in name or "\\" in name:
        return "has a path in its name (only flat names are accepted)"
    if ".." in name or name.startswith("/") or not _NAME.match(name):
        return "has a name outside letters, digits, `.`, `_`, `-` and `~`"
    if stat.S_ISLNK(info.external_attr >> 16):
        return "is a symbolic link"
    if info.flag_bits & 0x1:
        return "is encrypted"
    if name in (INDEX_NAME, META_NAME):
        return None
    if not name.endswith(".png"):
        return "is not a PNG picture, the index or the metadata"
    if info.file_size > MAX_PNG:
        return f"is {info.file_size} bytes; the limit is {MAX_PNG}"
    return None


@dataclass
class Picture:
    model: str
    kind: str  # plain | tinted | before
    source: str  # entry name in the artifact
    data: bytes
    dest: str = ""  # pr-<n>/<sha7>/<k>.png, chosen by the publisher


@dataclass
class ModelInfo:
    """The few index fields the comment shows, each re-validated."""

    triangles: int | None = None
    tint_coverage: float | None = None
    tint_coverage_review: float | None = None
    colours: list[tuple[str, float]] = field(default_factory=list)
    error: str | None = None


@dataclass
class Selection:
    pictures: list[Picture] = field(default_factory=list)
    info: dict[str, ModelInfo] = field(default_factory=dict)
    refused: list[tuple[str, str]] = field(default_factory=list)


def _fraction(v: object) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 else None


def select(unpacked: Unpacked, affected: list[str]) -> Selection:
    """Pairs the accepted pictures with the verdict's models through the index; names the rest as refused."""
    sel = Selection(refused=list(unpacked.refused))
    index = unpacked.index
    models = index.get("models") if isinstance(index, dict) else None
    if not isinstance(models, dict):
        sel.refused.append((INDEX_NAME, "has no `models` table"))
        return sel
    allowed = set(affected)
    used: set[str] = set()
    for mid in sorted(k for k in models if isinstance(k, str)):
        entry = models[mid]
        if mid not in allowed:
            sel.refused.append((mid, "is not a model of this pull request's verdict"))
            continue
        if not isinstance(entry, dict):
            sel.refused.append((mid, "has an index entry that is not an object"))
            continue
        for key, label in KINDS:
            name = entry.get(key)
            if name is None:
                continue
            if not isinstance(name, str) or name not in unpacked.pngs:
                if not (isinstance(name, str) and any(r[0] == name for r in unpacked.refused)):
                    sel.refused.append((mid, f"names a {label} picture that is not in the artifact"))
                continue
            used.add(name)
            sel.pictures.append(Picture(mid, label, name, unpacked.pngs[name]))
        info = ModelInfo()
        tri = entry.get("triangles")
        info.triangles = tri if isinstance(tri, int) and not isinstance(tri, bool) and 0 <= tri < 10**9 else None
        info.tint_coverage = _fraction(entry.get("tint_coverage"))
        info.tint_coverage_review = _fraction(entry.get("tint_coverage_review"))
        for c in (entry.get("colours") or [])[:8] if isinstance(entry.get("colours"), list) else []:
            if isinstance(c, dict) and isinstance(c.get("hex"), str) and _HEX.match(c["hex"]) and _fraction(c.get("share")) is not None:
                info.colours.append((c["hex"].lower(), _fraction(c["share"])))
        if isinstance(entry.get("error"), str) and entry["error"]:
            info.error = entry["error"]
        sel.info[mid] = info
    unreferenced = sorted(set(unpacked.pngs) - used)
    for name in unreferenced:
        sel.refused.append((name, "is not used by any model of the verdict's list"))
    return sel


def assign_destinations(pictures: list[Picture], pr: int, sha7: str) -> None:
    for k, p in enumerate(pictures, 1):
        p.dest = f"pr-{pr}/{sha7}/{k}.png"


def pictures_section(
    head_sha: str, sel: Selection | None, base_url: str | None, note: str | None = None, run_url: str | None = None
) -> str:
    """The comment's pictures section. `base_url` is `https://raw.githubusercontent.com/<repo>/<commit>/`."""
    out = [f"**Pictures for commit `{head_sha[:7]}`**" + (f" ([render run]({run_url}))" if run_url else ""), ""]
    if note:
        out += [md(note, 400), ""]
    if sel is not None and sel.pictures:
        assert base_url is not None
        by_model: dict[str, list[Picture]] = {}
        for p in sel.pictures:
            by_model.setdefault(p.model, []).append(p)
        for mid, pics in by_model.items():
            info = sel.info.get(mid, ModelInfo())
            facts = []
            if info.triangles is not None:
                facts.append(f"{info.triangles:,} triangles")
            if info.tint_coverage is not None:
                review = f", {info.tint_coverage_review:.0%} in the review view" if info.tint_coverage_review is not None else ""
                facts.append(f"the tint key covers {info.tint_coverage:.0%} of the model{review}")
            out += [f"**{md(mid)}**" + (f": {', '.join(facts)}" if facts else ""), ""]
            out.append(" ".join(f"![{md(mid)}, {p.kind}]({base_url}{p.dest})" for p in pics))
            captions = ", ".join(p.kind for p in pics)
            out += ["", f"_Shown: {captions}. Pictures are drawn with software rendering at a fixed camera, like the board's thumbnails; they differ slightly from the game's look._", ""]
            if info.colours:
                out += ["Main colours (candidates for a `tint_key`): " + ", ".join(f"`{h}` {s:.0%}" for h, s in info.colours), ""]
            if info.error:
                out += [f"The renderer reported: {md(info.error, 300)}", ""]
    elif sel is not None:
        out += ["No pictures were published for this commit.", ""]
    if sel is not None and sel.refused:
        out += ["These inputs from the render were refused and not published:", ""]
        out += [f"- {md(name, 90)}: {md(why, 200)}" for name, why in sel.refused[:40]]
        if len(sel.refused) > 40:
            out.append(f"- …and {len(sel.refused) - 40} more")
        out.append("")
    return "\n".join(out).rstrip()
