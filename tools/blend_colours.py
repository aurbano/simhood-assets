# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Gives a converted .glb back the colours and textures its FBX or OBJ export lost.

Exporting from Blender to FBX or OBJ often drops the colours of node-based materials and the
links to their textures, so the converted model comes out white. The .blend file still knows
them. This tool reads the materials straight out of the .blend (no Blender needed) and patches
the .glb so each material named the same gets the colour, or the PNG texture, the .blend gives it.

  uv run tools/blend_colours.py --blend <file.blend> --list
  uv run tools/blend_colours.py --blend <file.blend> --glb <model.glb> [--out <patched.glb>]
                                [--textures <folder>]... [--map <glb-material>=<blend-material>]...

Without --out the .glb is patched in place; run the converter again to start over. Materials are
matched by name (also with `.` and `_` read as the same, and without a trailing number such as
`.001`); `--map` pairs one the names do not connect. A texture is looked up by file name in the
.blend's folder and in each --textures folder. Only PNG textures are embedded, as they are; any
other image type is refused, so convert it to PNG first (the repository takes nothing else).
Colours are used as the .blend stores them, as glTF base colour factors.

Reads .blend files from Blender 2.5x to 4.1, plain or gzip-compressed; Blender 3.0 and later save
zstd-compressed files by default, which need Python 3.14 or newer (or Save As with File >
Preferences > Save & Load > Compress off). Blender 4.2 and later changed the file header and are
not read.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import struct
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path

SIGNATURE_PNG = b"\x89PNG\r\n\x1a\n"
MAX_TEXTURE_PX = 1024
MAX_TEXTURES = 2
DIFFUSE_NODES = {"ShaderNodeBsdfDiffuse", "ShaderNodeBsdfPrincipled", "ShaderNodeEmission", "ShaderNodeBsdfGlossy", "ShaderNodeBsdfGlass"}


class BlendError(Exception):
    """The .blend cannot be read; the message says why."""


class GlbError(Exception):
    """The .glb cannot be read or patched; the message says why."""


class TextureError(Exception):
    """A texture the .blend links cannot be used; the message says what to do."""


# ------------------------------------------------------------------------------------------------
# Reading a .blend


@dataclass
class BlendMaterial:
    name: str
    rgb: tuple[float, float, float]  # the material's own diffuse colour
    node_rgb: tuple[float, float, float] | None  # the colour of its first diffuse-like shader node
    images: list[tuple[str, str]] = field(default_factory=list)  # (image name, path as saved)

    @property
    def colour(self) -> tuple[float, float, float]:
        return self.node_rgb or self.rgb


def _decompress(raw: bytes, path: Path) -> bytes:
    if raw[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(raw)
        except (OSError, EOFError, zlib.error) as e:
            raise BlendError(f"{path.name}: damaged gzip-compressed .blend ({e})") from e
    if raw[:4] == b"\x28\xb5\x2f\xfd":
        try:
            from compression import zstd  # Python 3.14 and later
        except ImportError:
            raise BlendError(
                f"{path.name} is zstd-compressed (Blender 3.0 and later do that by default) and this Python "
                f"({sys.version_info.major}.{sys.version_info.minor}) cannot read it. Run with Python 3.14 or newer "
                "(`uv run --python 3.14 tools/blend_colours.py ...`), or open it in Blender and use File > Save As "
                "with the Compress option off."
            ) from None
        try:
            return zstd.decompress(raw)
        except Exception as e:  # the module's error types vary by version
            raise BlendError(f"{path.name}: damaged zstd-compressed .blend ({e})") from e
    return raw


def _cstr(data: bytes) -> str:
    return data.split(b"\0", 1)[0].decode(errors="replace")


class _Blend:
    """The blocks of a .blend and the layout of its structs (the file's own DNA table)."""

    def __init__(self, path: Path) -> None:
        d = _decompress(path.read_bytes(), path)
        if d[:7] != b"BLENDER":
            raise BlendError(f"{path.name} is not a .blend file")
        if d[7:9].isdigit():
            raise BlendError(
                f"{path.name} uses the file header of Blender 4.2 and later, which this reader does not handle; "
                "save it from Blender 4.1 or older, or give the colours by hand"
            )
        if d[7:8] not in (b"-", b"_") or d[8:9] not in (b"v", b"V"):
            raise BlendError(f"{path.name} has a .blend header this reader does not recognise")
        self.d = d
        self.ps = 8 if d[7:8] == b"-" else 4
        self.e = "<" if d[8:9] == b"v" else ">"
        self.pf = "Q" if self.ps == 8 else "I"
        self._blocks()
        self._dna()

    def _blocks(self) -> None:
        d, e, ps = self.d, self.e, self.ps
        pos = 12
        self.blocks: list[tuple[bytes, int, int, int, int, int]] = []  # code, old pointer, struct index, count, data offset, size
        head = 16 + ps
        while pos + head <= len(d):
            code = d[pos : pos + 4]
            (size,) = struct.unpack_from(e + "I", d, pos + 4)
            (old,) = struct.unpack_from(e + self.pf, d, pos + 8)
            sdna, count = struct.unpack_from(e + "II", d, pos + 8 + ps)
            self.blocks.append((code, old, sdna, count, pos + head, size))
            pos += head + size
            if code == b"ENDB":
                break
        self.by_ptr = {b[1]: b for b in self.blocks}

    def _dna(self) -> None:
        d, e, ps = self.d, self.e, self.ps
        found = [b for b in self.blocks if b[0] == b"DNA1"]
        if not found:
            raise BlendError("the file has no DNA table, so it is damaged or cut short")
        p = found[0][4] + 4

        def expect(tag: bytes, p: int) -> int:
            if d[p : p + 4] != tag:
                raise BlendError(f"the DNA table has no {tag.decode()} section")
            return p + 4

        def strings(p: int) -> tuple[list[str], int]:
            (n,) = struct.unpack_from(e + "I", d, p)
            p += 4
            out = []
            for _ in range(n):
                z = d.index(b"\0", p)
                out.append(d[p:z].decode())
                p = z + 1
            return out, (p + 3) & ~3

        p = expect(b"NAME", p)
        names, p = strings(p)
        p = expect(b"TYPE", p)
        types, p = strings(p)
        p = expect(b"TLEN", p)
        tlen = list(struct.unpack_from(e + f"{len(types)}H", d, p))
        p = ((p + 2 * len(types)) + 3) & ~3
        p = expect(b"STRC", p)
        (n_structs,) = struct.unpack_from(e + "I", d, p)
        p += 4
        structs = []
        for _ in range(n_structs):
            t, nf = struct.unpack_from(e + "HH", d, p)
            p += 4
            fields = []
            for _ in range(nf):
                fields.append(struct.unpack_from(e + "HH", d, p))
                p += 4
            structs.append((t, fields))

        def field_size(ft: int, fn: int) -> int:
            nm = names[fn]
            count = 1
            for part in nm.split("[")[1:]:
                count *= int(part.rstrip("]"))
            if nm.startswith("*") or nm.startswith("(*"):
                return ps * count
            return tlen[ft] * count

        self.layout: dict[str, dict[str, int]] = {}
        for t, fields in structs:
            off = 0
            table = {}
            for ft, fn in fields:
                table[names[fn].lstrip("*").split("[")[0]] = off
                off += field_size(ft, fn)
            self.layout[types[t]] = table
        self.struct_names = {i: types[t] for i, (t, _) in enumerate(structs)}

    # accessors -------------------------------------------------------------------------------

    def block(self, ptr: int, want: str):
        b = self.by_ptr.get(ptr)
        return b if b and self.struct_names.get(b[2]) == want else None

    def has(self, struct_name: str, name: str) -> bool:
        return name in self.layout.get(struct_name, {})

    def pointer(self, b, struct_name: str, name: str, index: int = 0) -> int:
        off = self.layout[struct_name][name] + index * self.ps
        return struct.unpack_from(self.e + self.pf, self.d, b[4] + off)[0]

    def text(self, b, struct_name: str, name: str, length: int) -> str:
        off = b[4] + self.layout[struct_name][name]
        return _cstr(self.d[off : off + length])

    def floats(self, offset: int, n: int) -> tuple[float, ...]:
        return struct.unpack_from(self.e + f"{n}f", self.d, offset)

    def id_name(self, b) -> str:
        return self.text(b, "ID", "name", 66)[2:]  # the first two characters are the block's type code

    def image(self, im) -> tuple[str, str]:
        return self.id_name(im), self.text(im, "Image", "name", 1024).replace("\\", "/")


def read_materials(path: Path) -> list[BlendMaterial]:
    """Every material in the .blend: name, diffuse colour, first node colour and linked images."""
    try:
        return _read_materials(path)
    except BlendError:
        raise
    except (KeyError, IndexError, ValueError, struct.error, UnicodeDecodeError, OverflowError) as e:
        raise BlendError(
            f"{path.name}: could not read its materials ({type(e).__name__}: {e}); it may come from a Blender "
            "version this reader does not know"
        ) from e
    except OSError as e:
        raise BlendError(f"{path}: cannot be read: {e.strerror or e}") from e


def _read_materials(path: Path) -> list[BlendMaterial]:
    f = _Blend(path)
    out: list[BlendMaterial] = []
    for b in f.blocks:
        if b[0][:2] != b"MA" or f.struct_names.get(b[2]) != "Material":
            continue
        name = f.id_name(b)
        rgb = f.floats(b[4] + f.layout["Material"]["r"], 3)
        images: list[tuple[str, str]] = []

        def add(image: tuple[str, str]) -> None:
            if image not in images:
                images.append(image)

        if f.has("Material", "mtex"):  # Blender 2.7x texture slots
            for i in range(18):
                slot = f.block(f.pointer(b, "Material", "mtex", i), "MTex")
                tex = f.block(f.pointer(slot, "MTex", "tex"), "Tex") if slot else None
                im = f.block(f.pointer(tex, "Tex", "ima"), "Image") if tex else None
                if im:
                    add(f.image(im))
        node_rgb = None
        tree = f.block(f.pointer(b, "Material", "nodetree"), "bNodeTree") if f.has("Material", "nodetree") else None
        uses_nodes = not f.has("Material", "use_nodes") or f.d[b[4] + f.layout["Material"]["use_nodes"]] != 0
        if tree and uses_nodes:
            first = struct.unpack_from(f.e + f.pf, f.d, tree[4] + f.layout["bNodeTree"]["nodes"])[0]
            node = f.block(first, "bNode")
            while node:
                kind = f.text(node, "bNode", "idname", 64)
                if kind == "ShaderNodeTexImage":
                    im = f.block(f.pointer(node, "bNode", "id"), "Image")
                    if im:
                        add(f.image(im))
                if kind in DIFFUSE_NODES and node_rgb is None:
                    first_socket = struct.unpack_from(f.e + f.pf, f.d, node[4] + f.layout["bNode"]["inputs"])[0]
                    sock = f.block(first_socket, "bNodeSocket")
                    while sock:
                        value = f.by_ptr.get(f.pointer(sock, "bNodeSocket", "default_value"))
                        if value and value[5] >= 16 and f.text(sock, "bNodeSocket", "name", 64) in ("Color", "Base Color"):
                            node_rgb = tuple(round(x, 4) for x in f.floats(value[4], 3))
                            break
                        sock = f.block(f.pointer(sock, "bNodeSocket", "next"), "bNodeSocket")
                node = f.block(f.pointer(node, "bNode", "next"), "bNode")
        out.append(BlendMaterial(name, tuple(round(x, 4) for x in rgb), node_rgb, images))
    return out


# ------------------------------------------------------------------------------------------------
# Matching names


def lookup(by_name: dict[str, BlendMaterial], name: str) -> BlendMaterial | None:
    """The .blend material a glb material name stands for: the name itself, with `_` read as `.`,
    without a trailing `.001` or `_001`, and without trailing digits. `.` and `_` count as equal."""
    for key in (name, name.replace("_", "."), re.sub(r"[._]\d+$", "", name), re.sub(r"\d+$", "", name)):
        if key in by_name:
            return by_name[key]
        for other, material in by_name.items():
            if other.replace(".", "_") == key.replace(".", "_"):
                return material
    return None


# ------------------------------------------------------------------------------------------------
# PNG


def png_info(blob: bytes) -> tuple[int, int, int, int, int]:
    """(width, height, bit depth, colour type, interlace) of a PNG, or raises TextureError."""
    if not blob.startswith(SIGNATURE_PNG) or blob[12:16] != b"IHDR" or len(blob) < 33:
        raise TextureError("is not a PNG")
    w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", blob[16:29])
    return w, h, depth, ctype, interlace


def png_has_transparency(blob: bytes) -> bool | None:
    """Whether any pixel of the PNG is noticeably transparent; None when it cannot be told (16-bit
    or interlaced images with an alpha channel)."""
    w, h, depth, ctype, interlace = png_info(blob)
    chunks: dict[bytes, bytes] = {}
    idat = []
    pos = 8
    while pos + 8 <= len(blob):
        (n,) = struct.unpack_from(">I", blob, pos)
        kind = blob[pos + 4 : pos + 8]
        body = blob[pos + 8 : pos + 8 + n]
        if kind == b"IDAT":
            idat.append(body)
        else:
            chunks.setdefault(kind, body)
        pos += 12 + n
    if ctype == 3:  # palette: transparent when tRNS lists a low alpha
        return any(a < 250 for a in chunks.get(b"tRNS", b""))
    if ctype in (0, 2):
        return b"tRNS" in chunks  # one colour is keyed out
    if depth != 8 or interlace:
        return None
    channels = 4 if ctype == 6 else 2
    stride = w * channels
    raw = zlib.decompress(b"".join(idat))
    prev = bytearray(stride)
    pos = 0
    for _ in range(h):
        kind = raw[pos]
        row = bytearray(raw[pos + 1 : pos + 1 + stride])
        pos += 1 + stride
        if kind == 1:
            for i in range(channels, stride):
                row[i] = (row[i] + row[i - channels]) & 255
        elif kind == 2:
            for i in range(stride):
                row[i] = (row[i] + prev[i]) & 255
        elif kind == 3:
            for i in range(stride):
                left = row[i - channels] if i >= channels else 0
                row[i] = (row[i] + ((left + prev[i]) >> 1)) & 255
        elif kind == 4:
            for i in range(stride):
                a = row[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                row[i] = (row[i] + pred) & 255
        elif kind != 0:
            raise TextureError("is a damaged PNG")
        if min(row[channels - 1 :: channels]) < 250:
            return True
        prev = row
    return False


def load_texture(material: str, image_path: str, folders: list[Path]) -> tuple[Path, bytes] | None:
    """The PNG the .blend links, found by file name in `folders`; None when no folder has it. Raises
    TextureError for a file that is there but not usable."""
    base = image_path.replace("\\", "/").split("/")[-1]
    if not base:
        return None
    for folder in folders:
        if not folder.is_dir():
            continue
        for candidate in (folder / base, *sorted(folder.iterdir())):
            if candidate.name.lower() != base.lower() or not candidate.is_file():
                continue
            blob = candidate.read_bytes()
            if not blob.startswith(SIGNATURE_PNG):
                kind = "a JPEG" if blob.startswith(b"\xff\xd8\xff") else "not a PNG"
                raise TextureError(
                    f"material {material}: texture {candidate} is {kind}; the repository takes PNG only, so convert "
                    "it to PNG (at most 1024 px a side) and run this again"
                )
            return candidate, blob
    return None


# ------------------------------------------------------------------------------------------------
# Patching a .glb


def read_glb(path: Path) -> tuple[dict, bytearray]:
    raw = path.read_bytes()
    if len(raw) < 20 or raw[:4] != b"glTF":
        raise GlbError(f"{path.name} is not a binary glTF (.glb)")
    pos = 12
    doc = None
    blob = bytearray()
    while pos + 8 <= len(raw):
        n, kind = struct.unpack_from("<II", raw, pos)
        body = raw[pos + 8 : pos + 8 + n]
        if kind == 0x4E4F534A and doc is None:
            try:
                doc = json.loads(body)
            except ValueError as e:
                raise GlbError(f"{path.name}: its JSON part is damaged ({e})") from e
        elif kind == 0x004E4942 and not blob:
            blob = bytearray(body)
        pos += 8 + n
    if doc is None:
        raise GlbError(f"{path.name} has no JSON part")
    return doc, blob


def write_glb(path: Path, doc: dict, blob: bytearray) -> None:
    blob = bytearray(blob)
    blob += b"\0" * (-len(blob) % 4)
    doc["buffers"] = [{"byteLength": len(blob)}] if blob else []
    text = json.dumps(doc, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text
    if blob:
        body += struct.pack("<II", len(blob), 0x004E4942) + bytes(blob)
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body)


def _add_texture(doc: dict, blob: bytearray, source: Path, data: bytes, cache: dict) -> int:
    if source in cache:
        return cache[source]
    blob += b"\0" * (-len(blob) % 4)
    views = doc.setdefault("bufferViews", [])
    views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)})
    blob += data
    images = doc.setdefault("images", [])
    images.append({"bufferView": len(views) - 1, "mimeType": "image/png", "name": source.name})
    samplers = doc.setdefault("samplers", [])
    # Pixel-art style palettes read cleanly with nearest filtering.
    samplers.append({"magFilter": 9728, "minFilter": 9728})
    textures = doc.setdefault("textures", [])
    textures.append({"source": len(images) - 1, "sampler": len(samplers) - 1})
    cache[source] = len(textures) - 1
    return cache[source]


def _texture_refs(node, found: list[dict]) -> None:
    """Every `{"index": n, ...}` held under a key ending in `Texture` (base colour, normal, ...)."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key.endswith("Texture") and isinstance(value, dict) and isinstance(value.get("index"), int):
                found.append(value)
            _texture_refs(value, found)
    elif isinstance(node, list):
        for value in node:
            _texture_refs(value, found)


def _prune(doc: dict, blob: bytearray) -> bytearray:
    """Drops textures no material uses any more (with their images, samplers and bytes). The
    repository counts every texture in the file, used or not."""
    textures = doc.get("textures", [])
    refs: list[dict] = []
    _texture_refs(doc.get("materials", []), refs)
    used = sorted({r["index"] for r in refs if 0 <= r["index"] < len(textures)})
    if len(used) == len(textures):
        return blob
    new_index = {old: new for new, old in enumerate(used)}
    for r in refs:
        r["index"] = new_index.get(r["index"], 0)
    kept = [textures[i] for i in used]
    images = doc.get("images", [])
    samplers = doc.get("samplers", [])
    used_images = sorted({t["source"] for t in kept if isinstance(t.get("source"), int)})
    used_samplers = sorted({t["sampler"] for t in kept if isinstance(t.get("sampler"), int)})
    image_map = {old: new for new, old in enumerate(used_images)}
    sampler_map = {old: new for new, old in enumerate(used_samplers)}
    for t in kept:
        if "source" in t:
            t["source"] = image_map[t["source"]]
        if "sampler" in t:
            t["sampler"] = sampler_map[t["sampler"]]
    doc["textures"] = kept
    doc["images"] = [images[i] for i in used_images]
    if samplers:
        doc["samplers"] = [samplers[i] for i in used_samplers]
    for key in ("textures", "images", "samplers"):
        if not doc.get(key):
            doc.pop(key, None)
    # Drop the bytes of buffer views nothing refers to now.
    views = doc.get("bufferViews", [])
    holders = [a for a in doc.get("accessors", []) if isinstance(a.get("bufferView"), int)]
    holders += [i for i in doc.get("images", []) if isinstance(i.get("bufferView"), int)]
    needed = sorted({h["bufferView"] for h in holders})
    view_map = {old: new for new, old in enumerate(needed)}
    out = bytearray()
    new_views = []
    for old in needed:
        v = dict(views[old])
        out += b"\0" * (-len(out) % 4)
        start = v.get("byteOffset", 0)
        v["byteOffset"] = len(out)
        out += blob[start : start + v["byteLength"]]
        new_views.append(v)
    for h in holders:
        h["bufferView"] = view_map[h["bufferView"]]
    doc["bufferViews"] = new_views
    return out


@dataclass
class PatchReport:
    patched: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)  # glb materials with no .blend material
    unused: list[str] = field(default_factory=list)  # .blend materials no glb material took
    missing_textures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def patch(
    doc: dict,
    blob: bytearray,
    materials: list[BlendMaterial],
    folders: list[Path],
    name_map: dict[str, str] | None = None,
) -> tuple[bytearray, PatchReport]:
    """Patches `doc` in place (the new binary part is returned): each glb material that matches a
    .blend material takes its texture when one is linked and found, else its colour."""
    report = PatchReport()
    by_name = {m.name: m for m in materials}
    taken: set[str] = set()
    cache: dict = {}
    for gm in doc.get("materials", []):
        name = gm.get("name") or ""
        wanted = (name_map or {}).get(name)
        if wanted is not None:
            found = by_name.get(wanted)
            if found is None:
                raise GlbError(f"--map names `{wanted}`, which the .blend does not have (it has: {', '.join(sorted(by_name)) or 'no materials'})")
        else:
            found = lookup(by_name, name)
        if found is None:
            report.unmatched.append(name or "(unnamed)")
            continue
        taken.add(found.name)
        pbr = gm.setdefault("pbrMetallicRoughness", {})
        pbr["metallicFactor"] = 0.0
        pbr["roughnessFactor"] = 0.9
        texture = None
        for _, image_path in found.images:
            texture = load_texture(found.name, image_path, folders)
            if texture:
                break
        if texture is None:
            report.missing_textures += [f"{found.name}: {p.split('/')[-1]}" for _, p in found.images]
        if texture:
            source, data = texture
            w, h, *_ = png_info(data)
            if w > MAX_TEXTURE_PX or h > MAX_TEXTURE_PX:
                report.notes.append(f"{source.name} is {w} x {h} px; the repository takes at most {MAX_TEXTURE_PX}")
            pbr["baseColorFactor"] = [1.0, 1.0, 1.0, 1.0]
            pbr["baseColorTexture"] = {"index": _add_texture(doc, blob, source, data, cache)}
            transparent = png_has_transparency(data)
            if transparent is None:
                report.notes.append(f"{source.name}: cannot tell whether it has transparent pixels (16-bit or interlaced); alpha left as it was")
            elif transparent:
                gm["alphaMode"] = "MASK"
                gm["alphaCutoff"] = 0.5
                gm["doubleSided"] = True
            report.patched.append(f"{name or '(unnamed)'} <- {found.name} (texture {source.name})")
        else:
            pbr.pop("baseColorTexture", None)
            pbr["baseColorFactor"] = [*found.colour, 1.0]
            kind = "node colour" if found.node_rgb else "diffuse colour"
            report.patched.append(f"{name or '(unnamed)'} <- {found.name} ({kind} {' '.join(f'{c:g}' for c in found.colour)})")
    report.unused = sorted(set(by_name) - taken)
    blob = _prune(doc, blob)
    if len(doc.get("textures", [])) > MAX_TEXTURES:
        report.notes.append(f"the model now has {len(doc['textures'])} textures; the repository takes at most {MAX_TEXTURES}")
    return blob, report


# ------------------------------------------------------------------------------------------------
# Command line


def _hex(rgb: tuple[float, float, float]) -> str:
    """How a linear colour looks on screen, as sRGB hex."""
    def one(c: float) -> int:
        c = max(0.0, min(1.0, c))
        return round(255 * (12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055))

    return "#" + "".join(f"{one(c):02x}" for c in rgb)


def _list(path: Path, materials: list[BlendMaterial]) -> None:
    print(f"{path.name}: {len(materials)} material(s) (colours as they look on screen, sRGB hex of the stored linear values)")
    for m in materials:
        node = f", node colour {_hex(m.node_rgb)}" if m.node_rgb else ""
        print(f"  {m.name}: diffuse {_hex(m.rgb)}{node}")
        for image_name, image_path in m.images:
            print(f"      image {image_name} ({image_path})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--blend", type=Path, required=True, help="the .blend file to read the materials from")
    parser.add_argument("--glb", type=Path, help="the converted model to patch")
    parser.add_argument("--out", type=Path, help="write the patched model here (default: patch --glb in place)")
    parser.add_argument("--list", action="store_true", help="only print the materials found in the .blend")
    parser.add_argument("--textures", type=Path, action="append", default=[], help="a folder to look for textures in (repeatable; the .blend's own folder is always searched)")
    parser.add_argument("--map", action="append", default=[], metavar="GLB=BLEND", help="pair a glb material with a .blend material the names do not connect (repeatable)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    if not args.list and args.glb is None:
        print("give --glb <model.glb> to patch, or --list to only print the .blend's materials", file=sys.stderr)
        return 2
    name_map = {}
    for item in args.map:
        left, sep, right = item.partition("=")
        if not sep or not left or not right:
            print(f"--map {item!r} is not GLB=BLEND", file=sys.stderr)
            return 2
        name_map[left] = right
    try:
        materials = read_materials(args.blend)
        if args.list:
            _list(args.blend, materials)
            return 0
        if not args.glb.is_file():
            print(f"{args.glb} is not a file", file=sys.stderr)
            return 2
        doc, blob = read_glb(args.glb)
        folders = [args.blend.parent, *args.textures]
        blob, report = patch(doc, blob, materials, folders, name_map)
    except (BlendError, GlbError, TextureError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1
    out = args.out or args.glb
    out.parent.mkdir(parents=True, exist_ok=True)
    write_glb(out, doc, blob)
    for line in report.patched:
        print(f"patched {line}")
    for name in report.unmatched:
        print(f"UNMATCHED glb material `{name}`: no material of that name in the .blend (try --map {name}=<blend material>)")
    for name in report.unused:
        print(f"unused .blend material `{name}`")
    for line in report.missing_textures:
        print(f"MISSING texture {line} (not in the .blend's folder or --textures; colour used instead)")
    for line in report.notes:
        print(f"NOTE {line}")
    print(f"{len(report.patched)} patched, {len(report.unmatched)} unmatched -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
