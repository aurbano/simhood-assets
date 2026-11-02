"""Builders for the tests: small generated glTF files and throwaway assets repositories.

No binary blobs are checked in; every fixture is generated here.
"""

from __future__ import annotations

import json
import shutil
import struct
import zlib
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
REPO = TOOLS.parent

DOMAINS = ["vehicles", "building-parts", "industry", "farm", "construction", "streets", "people", "nature"]


# ---------------------------------------------------------------------------------------------
# glTF


def box(sx: float, sy: float, sz: float, base_y: float = 0.0):
    """A box standing on y = base_y, centred in x and z: (positions, indices), 12 triangles."""
    x, z = sx / 2, sz / 2
    y0, y1 = base_y, base_y + sy
    pos = [(-x, y0, -z), (x, y0, -z), (x, y1, -z), (-x, y1, -z), (-x, y0, z), (x, y0, z), (x, y1, z), (-x, y1, z)]
    idx = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 4, 7, 0, 7, 3, 1, 2, 6, 1, 6, 5, 3, 7, 6, 3, 6, 2, 0, 1, 5, 0, 5, 4]
    return pos, idx


def grid(n: int, size: float = 1.0):
    """A flat n x n grid of quads in x and z (y = 0): 2 n^2 triangles."""
    pos = [(i * size / n, 0.0, j * size / n) for j in range(n + 1) for i in range(n + 1)]
    idx = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            b, c, d = a + 1, a + n + 1, a + n + 2
            idx += [a, c, b, b, c, d]
    return pos, idx


def png(width: int, height: int, full: bool = False) -> bytes:
    """A PNG of that size. Only the header is real unless `full` (a valid image, small sizes only)."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    if full:
        rows = b"".join(b"\x00" + b"\x80\x40\x20" * width for _ in range(height))
        out += chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    return out


class Gltf:
    """Builds one glTF document and its binary buffer."""

    def __init__(self) -> None:
        self.doc: dict = {
            "asset": {"version": "2.0", "generator": "simhood-assets tests"},
            "scene": 0,
            "scenes": [{"nodes": []}],
            "nodes": [],
            "meshes": [],
            "accessors": [],
            "bufferViews": [],
            "buffers": [{"byteLength": 0}],
        }
        self.bin = bytearray()

    def view(self, data: bytes, **extra) -> int:
        while len(self.bin) % 4:
            self.bin.append(0)
        self.doc["bufferViews"].append({"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data), **extra})
        self.bin += data
        return len(self.doc["bufferViews"]) - 1

    def mesh(self, positions, indices=None, mode: int = 4, index_type: int = 5123, material: int | None = None, extra_attrs: dict | None = None) -> int:
        flat = [c for p in positions for c in p]
        lows = [min(p[i] for p in positions) for i in range(3)]
        highs = [max(p[i] for p in positions) for i in range(3)]
        view = self.view(struct.pack(f"<{len(flat)}f", *flat), target=34962)
        self.doc["accessors"].append(
            {"bufferView": view, "componentType": 5126, "count": len(positions), "type": "VEC3", "min": lows, "max": highs}
        )
        prim: dict = {"attributes": {"POSITION": len(self.doc["accessors"]) - 1}, "mode": mode}
        if material is not None:
            prim["material"] = material
        if indices is not None:
            fmt = {5121: "B", 5123: "H", 5125: "I"}[index_type]
            iview = self.view(struct.pack(f"<{len(indices)}{fmt}", *indices), target=34963)
            self.doc["accessors"].append({"bufferView": iview, "componentType": index_type, "count": len(indices), "type": "SCALAR"})
            prim["indices"] = len(self.doc["accessors"]) - 1
        self.doc["meshes"].append({"primitives": [prim]})
        return len(self.doc["meshes"]) - 1

    def node(self, mesh: int | None = None, root: bool = True, children: list[int] | None = None, **props) -> int:
        node = dict(props)
        if mesh is not None:
            node["mesh"] = mesh
        if children:
            node["children"] = children
        self.doc["nodes"].append(node)
        index = len(self.doc["nodes"]) - 1
        if root:
            self.doc["scenes"][0]["nodes"].append(index)
        return index

    def image(self, data: bytes, mime: str = "image/png") -> int:
        view = self.view(data)
        self.doc.setdefault("images", []).append({"bufferView": view, "mimeType": mime})
        self.doc.setdefault("textures", []).append({"source": len(self.doc["images"]) - 1})
        return len(self.doc["textures"]) - 1

    def glb(self, pad_to: int = 0) -> bytes:
        while len(self.bin) % 4:
            self.bin.append(0)
        blob = bytes(self.bin) + b"\x00" * max(0, pad_to - len(self.bin))
        doc = json.loads(json.dumps(self.doc))
        doc["buffers"][0]["byteLength"] = len(blob)
        return pack_glb(doc, blob)


def pack_glb(doc: dict, blob: bytes | None) -> bytes:
    text = json.dumps(doc, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text
    if blob is not None:
        blob = blob + b"\x00" * (-len(blob) % 4)
        body += struct.pack("<II", len(blob), 0x004E4942) + blob
    return struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body


def box_glb(sx: float, sy: float, sz: float) -> bytes:
    g = Gltf()
    g.node(g.mesh(*box(sx, sy, sz)))
    return g.glb()


# ---------------------------------------------------------------------------------------------
# Repositories

KINDS = """\
[classes]
prop = 1500
vehicle = 3000
large = 6000

[[kind]]
id = "van"
class = "vehicle"
name = "Van"

[[kind]]
id = "street.bench"
class = "prop"
name = "Bench"

[[kind]]
id = "site.crane"
class = "large"
name = "Crane"

[[kind]]
id = "person.walking"
class = "prop"
name = "Walker"

[[kind]]
id = "car"
class = "vehicle"
drawn = true
name = "Car"
"""

PACK = 'id = "mine:base"\nversion = "1.0.0"\nlicence = "CC0-1.0"\nai_generated = "no"\nauthor = "A. Maker"\nsource = "https://example.org/mine"\n'


def slot_toml(sid: str, kind: str = "van", status: str = "have", size=(5.4, 2.0, 2.5), **extra) -> str:
    lines = [
        "[[slot]]",
        f'id = "{sid}"',
        f'kind = "{kind}"',
        f'name = "{sid.split(".", 1)[1].replace("-", " ").capitalize()}"',
        f"size = {{ length = {size[0]}, width = {size[1]}, height = {size[2]} }}",
        'style = "Plain."',
        'priority = "high"',
        f'status = "{status}"',
    ]
    for k, v in extra.items():
        lines.append(f"{k} = {json.dumps(v)}")
    return "\n".join(lines) + "\n"


def manifest_toml(mid: str, kind: str = "van", slot: str | None = "vehicles.panel-van", length: float | None = 5.4, extra: str = "", provenance: bool = True) -> str:
    text = f'id = "{mid}"\nkind = "{kind}"\n'
    if length is not None:
        text += f"length = {length}\n"
    if slot:
        text += f'slot = "{slot}"\n'
    if provenance:
        text += 'licence_evidence = "licences/page.html"\nsource = "https://example.org/van"\n'
    return text + extra


class TestRepo:
    """A throwaway assets repository: a catalogue, one pack and its files."""

    __test__ = False

    def __init__(self, root: Path, slots: dict[str, str] | None = None, pack: str = "mine", pack_toml: str = PACK):
        self.root = root
        self.pack = pack
        root.mkdir(parents=True, exist_ok=True)
        self.write("catalogue/kinds.toml", KINDS)
        for d in DOMAINS:
            self.write(f"catalogue/{d}.toml", "")
        for domain, text in (slots or {"vehicles": slot_toml("vehicles.panel-van")}).items():
            self.write(f"catalogue/{domain}.toml", text)
        self.write("CODEOWNERS", "* @owner\n")
        self.write(f"packs/{pack}/pack.toml", pack_toml)
        self.write(f"packs/{pack}/licences/page.html", "<html>licence</html>")

    def write(self, rel: str, content: str | bytes) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def model(self, stem: str, glb: bytes | None, manifest: str, folder: str = "vehicles") -> None:
        base = f"packs/{self.pack}/{folder}/{stem}"
        if glb is not None:
            self.write(f"{base}.glb", glb)
        self.write(f"{base}.toml", manifest)

    def copy_to(self, dest: Path) -> "TestRepo":
        shutil.copytree(self.root, dest)
        clone = TestRepo.__new__(TestRepo)
        clone.root, clone.pack = dest, self.pack
        return clone


def van_repo(tmp_path: Path, name: str = "repo", **kw) -> TestRepo:
    """A repository with one valid van filling `vehicles.panel-van`."""
    repo = TestRepo(tmp_path / name, **kw)
    repo.model("van", box_glb(5.4, 2.5, 2.0), manifest_toml("mine:van"))
    return repo
