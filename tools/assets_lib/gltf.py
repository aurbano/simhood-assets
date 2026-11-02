"""glTF reader and the measuring profile of design D5.

Reads a `.glb` (or, in the grandfathered seed packs, a `.gltf` with the `.bin` buffers and `.png`
textures it names by relative URI inside its pack) and reports triangles, bounds, textures and the
facts the file rules need. Anything outside the profile is refused, never guessed: a refused model
has no triangle count and no bounds.

Stdlib only.
"""

from __future__ import annotations

import base64
import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

MAX_NODES = 256
MAX_DEPTH = 32
MAX_MESHES = 128
MAX_VERTICES = 200_000
MAX_TEXTURES = 2
MAX_TEXTURE_PX = 1024

ALLOWED_EXTENSIONS = (
    "KHR_materials_unlit",
    "KHR_texture_transform",
    "KHR_materials_emissive_strength",
    "GODOT_single_root",
)

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

_COMPONENT_BYTES = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
_TYPE_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
_INDEX_COMPONENTS = (5121, 5123, 5125)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass
class Issue:
    """One thing wrong with a model file: a rule id and a plain-words message."""

    rule: str
    message: str


class _Stop(Exception):
    """Measuring cannot go on; the reason is already in the issue list or in `message`."""

    def __init__(self, message: str | None = None, rule: str = "gltf.profile"):
        super().__init__(message or "")
        self.message = message
        self.rule = rule


@dataclass
class ModelData:
    """What the reader found in one model file."""

    issues: list[Issue] = field(default_factory=list)
    measured: bool = False
    triangles: int | None = None
    vertices: int | None = None
    bounds_min: tuple[float, float, float] | None = None
    bounds_max: tuple[float, float, float] | None = None
    textures: int = 0
    animated: bool = False
    skinned: bool = False
    extensions: list[str] = field(default_factory=list)
    # Colours the materials in use declare, as shown by the client (sRGB hex).
    materials: list[dict] = field(default_factory=list)
    # Pack-relative paths of external files the model reads (buffers, textures).
    resources: list[str] = field(default_factory=list)


def read_model(path: Path, pack_root: Path, graph_ok: bool = False) -> ModelData:
    """Reads and measures the model at `path`, a file inside `pack_root`.

    `graph_ok` allows a `.gltf` to read relative `.bin` and `.png` files inside the pack; a `.glb`
    never may. Never raises on a bad file: the problems are in `ModelData.issues`.
    """
    data = ModelData()
    try:
        raw = path.read_bytes()
    except OSError as e:
        data.issues.append(Issue("gltf.invalid", f"cannot be read: {e.strerror or e}"))
        return data
    try:
        _read(path, raw, pack_root, graph_ok, data)
    except _Stop as stop:
        # A disallowed extension is reported once; measuring failing because of it adds nothing.
        explained = any(i.rule == "gltf.extension" for i in data.issues)
        if stop.message and not (explained and stop.rule == "gltf.profile"):
            data.issues.append(Issue(stop.rule, stop.message))
        data.measured = False
        data.triangles = data.vertices = None
        data.bounds_min = data.bounds_max = None
    return data


# ------------------------------------------------------------------------------------------------
# Container


def _split_glb(raw: bytes) -> tuple[dict, bytes | None]:
    if len(raw) < 20:
        raise _Stop("is too short to be a GLB", "gltf.invalid")
    magic, version, length = struct.unpack_from("<III", raw, 0)
    if magic != GLB_MAGIC:
        raise _Stop("is not a GLB (bad magic)", "gltf.invalid")
    if version != 2:
        raise _Stop(f"is GLB version {version}, not 2", "gltf.invalid")
    if length != len(raw):
        raise _Stop(f"GLB header says {length} bytes but the file has {len(raw)}", "gltf.invalid")
    offset = 12
    doc_bytes: bytes | None = None
    binary: bytes | None = None
    first = True
    while offset < len(raw):
        if offset + 8 > len(raw):
            raise _Stop("has a truncated GLB chunk header", "gltf.invalid")
        clen, ctype = struct.unpack_from("<II", raw, offset)
        offset += 8
        if offset + clen > len(raw):
            raise _Stop("has a GLB chunk running past the end of the file", "gltf.invalid")
        body = raw[offset : offset + clen]
        offset += clen
        if first:
            if ctype != CHUNK_JSON:
                raise _Stop("GLB's first chunk is not JSON", "gltf.invalid")
            doc_bytes = body
            first = False
        elif ctype == CHUNK_BIN and binary is None:
            binary = body
        # Any other chunk is ignored, as the spec asks of a reader.
    if doc_bytes is None:
        raise _Stop("GLB has no JSON chunk", "gltf.invalid")
    try:
        doc = json.loads(doc_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise _Stop(f"GLB JSON chunk is not valid JSON ({e})", "gltf.invalid") from e
    return doc, binary


def _parse_gltf_json(raw: bytes) -> dict:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise _Stop(f"is not valid glTF JSON ({e})", "gltf.invalid") from e


# ------------------------------------------------------------------------------------------------
# Resources


def _resolve(uri: str, base_dir: Path, pack_root: Path) -> tuple[Path, str]:
    """A relative resource inside the pack, or a refusal."""
    decoded = unquote(uri)
    parts = decoded.replace("\\", "/").split("/")
    if (
        not decoded
        or ":" in decoded
        or decoded.startswith(("/", "\\"))
        or any(p in ("", "..") for p in parts)
    ):
        raise _Stop(f"uri `{uri}` is not a plain relative path inside the pack", "gltf.external-uri")
    target = base_dir.joinpath(*parts)
    try:
        rel = target.resolve().relative_to(pack_root.resolve())
    except ValueError:
        raise _Stop(f"uri `{uri}` leaves the pack", "gltf.external-uri") from None
    if target.is_symlink() or not target.is_file():
        raise _Stop(f"uri `{uri}` names no regular file in the pack", "gltf.external-uri")
    return target, rel.as_posix()


def _data_uri(uri: str) -> bytes:
    head, _, payload = uri.partition(",")
    if not head.endswith(";base64"):
        raise _Stop("has a data uri that is not base64", "gltf.invalid")
    try:
        return base64.b64decode(payload, validate=True)
    except ValueError as e:
        raise _Stop(f"has a bad base64 data uri ({e})", "gltf.invalid") from e


def _external(
    uri: str, what: str, is_glb: bool, graph_ok: bool, base_dir: Path, pack_root: Path, data: ModelData
) -> bytes:
    """The bytes behind an external uri, only where the grandfathered graph allows them."""
    if is_glb or not graph_ok:
        data.issues.append(
            Issue(
                "gltf.external-uri",
                f"{what} has an external uri `{uri}`"
                + ("; a .glb must embed its data" if is_glb else "; only the packs kaykit-city and kaykit-forest may reference files"),
            )
        )
        raise _Stop()
    try:
        target, rel = _resolve(uri, base_dir, pack_root)
    except _Stop as stop:
        data.issues.append(Issue(stop.rule, f"{what}: {stop.message}"))
        raise _Stop() from None
    if rel not in data.resources:
        data.resources.append(rel)
    return target.read_bytes()


def _load_buffers(
    doc: dict, glb_bin: bytes | None, is_glb: bool, graph_ok: bool, base_dir: Path, pack_root: Path, data: ModelData
) -> list[bytes]:
    buffers = doc.get("buffers", [])
    if not isinstance(buffers, list):
        raise _Stop("`buffers` is not a list")
    out: list[bytes] = []
    blocked = False
    for i, buf in enumerate(buffers):
        if not isinstance(buf, dict) or not _is_int(buf.get("byteLength")) or buf["byteLength"] < 1:
            raise _Stop(f"buffer {i} has no valid byteLength")
        uri = buf.get("uri")
        try:
            if uri is None:
                if not (is_glb and i == 0 and glb_bin is not None):
                    raise _Stop(f"buffer {i} has no data")
                blob = glb_bin
            elif not isinstance(uri, str):
                raise _Stop(f"buffer {i} uri is not a string")
            elif uri.startswith("data:"):
                blob = _data_uri(uri)
            else:
                blob = _external(uri, f"buffer {i}", is_glb, graph_ok, base_dir, pack_root, data)
        except _Stop as stop:
            if stop.message:
                raise
            blocked = True
            out.append(b"")
            continue
        if len(blob) < buf["byteLength"]:
            raise _Stop(f"buffer {i} holds {len(blob)} bytes, fewer than its byteLength {buf['byteLength']}")
        out.append(blob)
    if blocked:
        raise _Stop()
    return out


def _slice_view(doc: dict, buffers: list[bytes], index: int) -> bytes:
    views = doc.get("bufferViews", [])
    if not _is_int(index) or not 0 <= index < len(views) or not isinstance(views[index], dict):
        raise _Stop(f"bufferView {index} does not exist")
    view = views[index]
    start, length, buf = view.get("byteOffset", 0), view.get("byteLength"), view.get("buffer")
    if not (_is_int(start) and _is_int(length) and _is_int(buf)) or start < 0 or length < 0:
        raise _Stop(f"bufferView {index} has bad offsets")
    if not 0 <= buf < len(buffers):
        raise _Stop(f"bufferView {index} names a buffer that does not exist")
    declared = doc["buffers"][buf]["byteLength"]
    if start + length > declared:
        raise _Stop(f"bufferView {index} runs past the end of its buffer")
    return buffers[buf][start : start + length]


# ------------------------------------------------------------------------------------------------
# Textures


def _check_textures(
    doc: dict, buffers: list[bytes] | None, is_glb: bool, graph_ok: bool, base_dir: Path, pack_root: Path, data: ModelData
) -> None:
    textures = doc.get("textures", [])
    images = doc.get("images", [])
    if not isinstance(textures, list) or not isinstance(images, list):
        return
    data.textures = len(textures)
    if len(textures) > MAX_TEXTURES:
        data.issues.append(
            Issue("gltf.texture", f"has {len(textures)} textures, at most {MAX_TEXTURES} are allowed")
        )
    seen: set[int] = set()
    for tex in textures:
        if not isinstance(tex, dict) or not _is_int(tex.get("source")):
            continue
        src = tex["source"]
        if src in seen or not 0 <= src < len(images) or not isinstance(images[src], dict):
            continue
        seen.add(src)
        image = images[src]
        label = f"texture image {src}"
        try:
            uri = image.get("uri")
            if isinstance(uri, str):
                if uri.startswith("data:"):
                    blob = _data_uri(uri)
                else:
                    blob = _external(uri, label, is_glb, graph_ok, base_dir, pack_root, data)
            elif "bufferView" in image and buffers is not None:
                blob = _slice_view(doc, buffers, image["bufferView"])
            else:
                continue
        except _Stop as stop:
            if stop.message:
                data.issues.append(Issue("gltf.texture", f"{label}: {stop.message}"))
            continue
        fmt = _image_format(blob)
        if fmt != "PNG":
            data.issues.append(Issue("gltf.texture", f"{label} is {fmt}; only PNG textures are allowed"))
            continue
        width, height = _png_size(blob)
        if width is None:
            data.issues.append(Issue("gltf.texture", f"{label} is a damaged PNG"))
        elif width > MAX_TEXTURE_PX or height > MAX_TEXTURE_PX:
            data.issues.append(
                Issue("gltf.texture", f"{label} is {width} x {height} px, at most {MAX_TEXTURE_PX} a side")
            )


def _image_format(blob: bytes) -> str:
    if blob.startswith(_PNG_SIGNATURE):
        return "PNG"
    if blob.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "WebP"
    return "not a recognised image"


def _png_size(blob: bytes) -> tuple[int | None, int | None]:
    if len(blob) < 24 or blob[12:16] != b"IHDR":
        return None, None
    width, height = struct.unpack(">II", blob[16:24])
    return width, height


# ------------------------------------------------------------------------------------------------
# Reading


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _read(path: Path, raw: bytes, pack_root: Path, graph_ok: bool, data: ModelData) -> None:
    is_glb = raw[:4] == b"glTF"
    if is_glb:
        doc, glb_bin = _split_glb(raw)
    else:
        doc, glb_bin = _parse_gltf_json(raw), None
    if not isinstance(doc, dict):
        raise _Stop("is not a glTF document", "gltf.invalid")
    asset = doc.get("asset")
    version = asset.get("version") if isinstance(asset, dict) else None
    if version != "2.0":
        raise _Stop(f"is glTF version {version!r}, not 2.0", "gltf.invalid")

    _check_extensions(doc, data)
    data.animated = bool(doc.get("animations"))
    data.skinned = bool(doc.get("skins"))
    base_dir = path.parent

    # A non-empty extensionsRequired or a disallowed extension stops measuring after the facts that
    # need no decoding are in.
    required_allowed = [e for e in _str_list(doc.get("extensionsRequired")) if e in ALLOWED_EXTENSIONS]

    buffers: list[bytes] | None
    try:
        buffers = _load_buffers(doc, glb_bin, is_glb, graph_ok, base_dir, pack_root, data)
    except _Stop as stop:
        buffers = None
        pending = stop
    else:
        pending = None
    _check_textures(doc, buffers, is_glb, graph_ok, base_dir, pack_root, data)
    if pending is not None:
        raise pending
    if required_allowed:
        # Fails the file, but an allowed extension never changes the geometry, so it is still measured.
        data.issues.append(
            Issue("gltf.required-extension", f"requires extension {', '.join(required_allowed)}; no extension may be required")
        )
    assert buffers is not None
    _measure(doc, buffers, data)


def _str_list(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _check_extensions(doc: dict, data: ModelData) -> None:
    used = _str_list(doc.get("extensionsUsed"))
    for name in _str_list(doc.get("extensionsRequired")):
        if name not in used:
            used.append(name)
    data.extensions = sorted(set(used))
    for name in data.extensions:
        if name not in ALLOWED_EXTENSIONS:
            required = name in _str_list(doc.get("extensionsRequired"))
            data.issues.append(
                Issue(
                    "gltf.extension",
                    f"uses extension {name}{' (and requires it)' if required else ''}; only "
                    + ", ".join(ALLOWED_EXTENSIONS)
                    + " are allowed",
                )
            )


# ------------------------------------------------------------------------------------------------
# Measuring


def _list_of(doc: dict, key: str) -> list:
    value = doc.get(key, [])
    if not isinstance(value, list):
        raise _Stop(f"`{key}` is not a list")
    return value


def _accessor_layout(doc: dict, index: int, what: str) -> tuple[dict, int, int, int, int]:
    """(accessor, bufferView index, first byte in buffer, stride, element size) for a dense accessor."""
    accessors = doc["accessors"]
    if not _is_int(index) or not 0 <= index < len(accessors):
        raise _Stop(f"{what} names accessor {index}, which does not exist")
    acc = accessors[index]
    if not isinstance(acc, dict):
        raise _Stop(f"accessor {index} is not an object")
    if "sparse" in acc:
        raise _Stop(f"accessor {index} is sparse, which the measuring profile refuses")
    ctype, atype, count = acc.get("componentType"), acc.get("type"), acc.get("count")
    if ctype not in _COMPONENT_BYTES or atype not in _TYPE_COMPONENTS or not _is_int(count) or count < 1:
        raise _Stop(f"accessor {index} has a bad componentType, type or count")
    size = _COMPONENT_BYTES[ctype] * _TYPE_COMPONENTS[atype]
    view_index = acc.get("bufferView")
    if view_index is None:
        raise _Stop(f"accessor {index} has no bufferView, which the measuring profile refuses")
    views = doc.get("bufferViews", [])
    if not _is_int(view_index) or not 0 <= view_index < len(views) or not isinstance(views[view_index], dict):
        raise _Stop(f"accessor {index} names a bufferView that does not exist")
    view = views[view_index]
    stride = view.get("byteStride", size)
    if not _is_int(stride) or stride < size or stride > 252:
        raise _Stop(f"bufferView {view_index} has a bad byteStride")
    offset = acc.get("byteOffset", 0)
    if not _is_int(offset) or offset < 0:
        raise _Stop(f"accessor {index} has a bad byteOffset")
    view_len, view_off, buf_index = view.get("byteLength"), view.get("byteOffset", 0), view.get("buffer")
    if not (_is_int(view_len) and _is_int(view_off) and _is_int(buf_index)) or view_len < 0 or view_off < 0:
        raise _Stop(f"bufferView {view_index} has bad offsets")
    if not 0 <= buf_index < len(doc.get("buffers", [])):
        raise _Stop(f"bufferView {view_index} names a buffer that does not exist")
    if view_off + view_len > doc["buffers"][buf_index]["byteLength"]:
        raise _Stop(f"bufferView {view_index} runs past the end of its buffer")
    if offset + stride * (count - 1) + size > view_len:
        raise _Stop(f"accessor {index} runs outside its buffer view")
    first = view.get("byteOffset", 0) + offset
    return acc, view_index, first, stride, size


def _decode_positions(doc: dict, buffers: list[bytes], index: int) -> list[tuple[float, float, float]]:
    acc, view_index, first, stride, _ = _accessor_layout(doc, index, "POSITION")
    if acc["componentType"] != 5126 or acc["type"] != "VEC3" or acc.get("normalized"):
        raise _Stop(f"POSITION accessor {index} is not plain float32 VEC3 (quantised positions are refused)")
    blob = buffers[doc["bufferViews"][view_index]["buffer"]]
    count = acc["count"]
    if stride == 12:
        out = list(struct.iter_unpack("<fff", blob[first : first + 12 * count]))
    else:
        out = [struct.unpack_from("<fff", blob, first + i * stride) for i in range(count)]
    for x, y, z in out:
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            raise _Stop(f"POSITION accessor {index} holds a non-finite value")
    return out


def _local_matrix(node: dict, index: int) -> list[list[float]]:
    """The node's local transform as 3 rows of [r0 r1 r2 t]."""
    if "matrix" in node:
        m = node["matrix"]
        if not (isinstance(m, list) and len(m) == 16 and all(_is_num(v) for v in m)):
            raise _Stop(f"node {index} has a bad matrix")
        if (m[3], m[7], m[11], m[15]) != (0, 0, 0, 1):
            raise _Stop(f"node {index} has a projective matrix")
        return [[float(m[c * 4 + r]) for c in range(3)] + [float(m[12 + r])] for r in range(3)]
    t = node.get("translation", [0, 0, 0])
    q = node.get("rotation", [0, 0, 0, 1])
    s = node.get("scale", [1, 1, 1])
    if not (
        isinstance(t, list) and len(t) == 3 and all(_is_num(v) for v in t)
        and isinstance(q, list) and len(q) == 4 and all(_is_num(v) for v in q)
        and isinstance(s, list) and len(s) == 3 and all(_is_num(v) for v in s)
    ):
        raise _Stop(f"node {index} has a bad translation, rotation or scale")
    x, y, z, w = (float(v) for v in q)
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm == 0:
        raise _Stop(f"node {index} has a zero rotation")
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    rot = [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]
    return [[rot[r][c] * float(s[c]) for c in range(3)] + [float(t[r])] for r in range(3)]


def _compose(parent: list[list[float]], local: list[list[float]]) -> list[list[float]]:
    out = []
    for r in range(3):
        row = [sum(parent[r][k] * local[k][c] for k in range(3)) for c in range(3)]
        row.append(sum(parent[r][k] * local[k][3] for k in range(3)) + parent[r][3])
        out.append(row)
    return out


_IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]


def _axis_map(m: list[list[float]]) -> list[tuple[int, float]] | None:
    """For a transform that sends each axis to one axis (a turn by a right angle, a scale, a
    mirror), the source axis and factor of every output axis; otherwise None."""
    out: list[tuple[int, float]] = []
    used: set[int] = set()
    for r in range(3):
        nz = [c for c in range(3) if abs(m[r][c]) > 1e-12]
        if len(nz) != 1 or nz[0] in used:
            return None
        used.add(nz[0])
        out.append((nz[0], m[r][nz[0]]))
    return out


def _measure(doc: dict, buffers: list[bytes], data: ModelData) -> None:
    nodes = _list_of(doc, "nodes")
    meshes = _list_of(doc, "meshes")
    accessors = _list_of(doc, "accessors")
    _list_of(doc, "bufferViews")
    if len(nodes) > MAX_NODES:
        raise _Stop(f"has {len(nodes)} nodes, more than the {MAX_NODES} the profile allows")
    if len(meshes) > MAX_MESHES:
        raise _Stop(f"has {len(meshes)} meshes, more than the {MAX_MESHES} the profile allows")

    # Scene
    scenes = _list_of(doc, "scenes")
    scene_index = doc.get("scene")
    if scene_index is None:
        if len(scenes) == 0:
            raise _Stop("has no scene")
        if len(scenes) > 1:
            raise _Stop(f"has {len(scenes)} scenes and no default `scene`")
        scene_index = 0
    if not _is_int(scene_index) or not 0 <= scene_index < len(scenes) or not isinstance(scenes[scene_index], dict):
        raise _Stop(f"default scene {scene_index!r} does not exist")
    roots = scenes[scene_index].get("nodes", [])
    if not isinstance(roots, list) or not all(_is_int(r) and 0 <= r < len(nodes) for r in roots):
        raise _Stop("the scene names a node that does not exist")

    # Structure that is refused wherever it sits in the file
    if any(isinstance(a, dict) and "sparse" in a for a in accessors):
        raise _Stop("has a sparse accessor, which the measuring profile refuses")
    total_vertices = 0
    mesh_info: list[list[tuple[int, int, int | None]]] = []  # per mesh: (mode, position accessor, indices accessor)
    for mi, mesh in enumerate(meshes):
        prims = mesh.get("primitives") if isinstance(mesh, dict) else None
        if not isinstance(prims, list) or not prims:
            raise _Stop(f"mesh {mi} has no primitives")
        if mesh.get("weights"):
            raise _Stop(f"mesh {mi} has morph target weights, which the measuring profile refuses")
        entry = []
        for pi, prim in enumerate(prims):
            if not isinstance(prim, dict):
                raise _Stop(f"mesh {mi} primitive {pi} is not an object")
            if prim.get("targets"):
                raise _Stop(f"mesh {mi} has morph targets, which the measuring profile refuses")
            mode = prim.get("mode", 4)
            if mode in (0, 1, 2, 3):
                kind = "points" if mode == 0 else "lines"
                raise _Stop(f"mesh {mi} draws {kind}; only triangles, strips and fans are measured")
            if mode not in (4, 5, 6):
                raise _Stop(f"mesh {mi} has an unknown primitive mode {mode!r}")
            attrs = prim.get("attributes")
            pos = attrs.get("POSITION") if isinstance(attrs, dict) else None
            if pos is None:
                raise _Stop(f"mesh {mi} primitive {pi} has no POSITION")
            acc = _accessor_layout(doc, pos, f"mesh {mi}")[0]
            total_vertices += acc["count"]
            entry.append((mode, pos, prim.get("indices")))
        mesh_info.append(entry)
    if total_vertices > MAX_VERTICES:
        raise _Stop(f"has {total_vertices} vertices, more than the {MAX_VERTICES} the profile allows")
    for index in range(len(accessors)):
        acc = accessors[index]
        if isinstance(acc, dict) and acc.get("bufferView") is not None:
            _accessor_layout(doc, index, "the file")

    # Triangles and positions per mesh
    mesh_tris: dict[int, int] = {}
    mesh_pos: dict[int, list[list[tuple[float, float, float]]]] = {}

    def mesh_data(mi: int) -> None:
        if mi in mesh_tris:
            return
        tris = 0
        pos_sets = []
        for mode, pos, idx in mesh_info[mi]:
            positions = _decode_positions(doc, buffers, pos)
            n = len(positions)
            if idx is not None:
                iacc = _accessor_layout(doc, idx, f"mesh {mi}")[0]
                if iacc["componentType"] not in _INDEX_COMPONENTS or iacc["type"] != "SCALAR":
                    raise _Stop(f"mesh {mi} has indices that are not unsigned SCALAR")
                n = iacc["count"]
            tris += n // 3 if mode == 4 else max(n - 2, 0)
            pos_sets.append(positions)
        mesh_tris[mi] = tris
        mesh_pos[mi] = pos_sets

    # Walk the scene
    triangles = 0
    vertices = 0
    low = [math.inf] * 3
    high = [-math.inf] * 3
    seen: set[int] = set()
    used_materials: list[int] = []
    stack = [(r, _IDENTITY, 1) for r in reversed(roots)]
    while stack:
        ni, parent, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise _Stop(f"nests nodes more than {MAX_DEPTH} deep")
        if ni in seen:
            raise _Stop(f"node {ni} is reached twice, so the scene is not a tree")
        seen.add(ni)
        node = nodes[ni]
        if not isinstance(node, dict):
            raise _Stop(f"node {ni} is not an object")
        world = _compose(parent, _local_matrix(node, ni))
        mi = node.get("mesh")
        if mi is not None:
            if not _is_int(mi) or not 0 <= mi < len(meshes):
                raise _Stop(f"node {ni} names mesh {mi!r}, which does not exist")
            mesh_data(mi)
            triangles += mesh_tris[mi]
            for positions in mesh_pos[mi]:
                vertices += len(positions)
                _grow(low, high, positions, world)
            for prim in meshes[mi]["primitives"]:
                m = prim.get("material")
                if _is_int(m) and m not in used_materials:
                    used_materials.append(m)
        children = node.get("children", [])
        if not isinstance(children, list) or not all(_is_int(c) and 0 <= c < len(nodes) for c in children):
            raise _Stop(f"node {ni} names a child that does not exist")
        for child in reversed(children):
            stack.append((child, world, depth + 1))

    if triangles == 0 or low[0] == math.inf:
        raise _Stop("draws no triangles")
    data.triangles = triangles
    data.vertices = vertices
    data.bounds_min = (low[0], low[1], low[2])
    data.bounds_max = (high[0], high[1], high[2])
    data.measured = True
    data.materials = _materials(doc, used_materials)


def _grow(low: list[float], high: list[float], positions: list[tuple[float, float, float]], m: list[list[float]]) -> None:
    amap = _axis_map(m)
    if amap is not None:
        # Exact for a right-angle turn: the box of the points is the turned box of their box.
        src_low = [min(p[a] for p in positions) for a in range(3)]
        src_high = [max(p[a] for p in positions) for a in range(3)]
        for r, (c, k) in enumerate(amap):
            a, b = k * src_low[c] + m[r][3], k * src_high[c] + m[r][3]
            low[r] = min(low[r], a, b)
            high[r] = max(high[r], a, b)
        return
    (a0, a1, a2, t0), (b0, b1, b2, t1), (c0, c1, c2, t2) = m
    for x, y, z in positions:
        px = a0 * x + a1 * y + a2 * z + t0
        py = b0 * x + b1 * y + b2 * z + t1
        pz = c0 * x + c1 * y + c2 * z + t2
        if px < low[0]:
            low[0] = px
        if px > high[0]:
            high[0] = px
        if py < low[1]:
            low[1] = py
        if py > high[1]:
            high[1] = py
        if pz < low[2]:
            low[2] = pz
        if pz > high[2]:
            high[2] = pz


def _srgb_byte(linear: float) -> int:
    v = min(max(linear, 0.0), 1.0)
    s = 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
    return round(s * 255)


def _materials(doc: dict, used: list[int]) -> list[dict]:
    """Base colours of the materials in use, as sRGB hex (glTF stores them in linear light)."""
    out = []
    materials = doc.get("materials", [])
    if not isinstance(materials, list):
        return out
    for mi in used:
        if not 0 <= mi < len(materials) or not isinstance(materials[mi], dict):
            continue
        material = materials[mi]
        pbr = material.get("pbrMetallicRoughness")
        factor = pbr.get("baseColorFactor") if isinstance(pbr, dict) else None
        if not (isinstance(factor, list) and len(factor) == 4 and all(_is_num(v) for v in factor)):
            factor = [1.0, 1.0, 1.0, 1.0]
        hex_colour = "#" + "".join(f"{_srgb_byte(float(v)):02x}" for v in factor[:3])
        name = material.get("name")
        textured = isinstance(pbr, dict) and "baseColorTexture" in pbr
        out.append({"name": name if isinstance(name, str) else None, "hex": hex_colour, "textured": textured})
    return out
