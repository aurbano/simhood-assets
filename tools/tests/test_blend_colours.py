"""tools/blend_colours.py: reading materials out of a .blend, and patching a .glb with them."""

import gzip
import struct
import sys
import zlib
from pathlib import Path

import pytest
from builders import Gltf, box, png
from assets_lib import gltf

import blend_colours as bc

DATA = Path(__file__).resolve().parent / "data"
HAY = DATA / "hay_bale.blend"


def model(materials: list[dict], with_texture_png: bytes | None = None) -> tuple[bytes, Gltf]:
    """A glb with one box per material (so every material is in use)."""
    g = Gltf()
    g.doc["materials"] = materials
    for i in range(len(materials)):
        g.node(g.mesh(*box(1, 1, 1), material=i))
    return g.glb(), g


def write_glb(path: Path, materials: list[dict]) -> Path:
    path.write_bytes(model(materials)[0])
    return path


def patched(path: Path) -> dict:
    return bc.read_glb(path)[0]


def blend_material(name: str, rgb=(0.5, 0.25, 0.125), node=None, images=()) -> bc.BlendMaterial:
    return bc.BlendMaterial(name, rgb, node, list(images))


def rgba_png(width: int, height: int, alpha_at, filters=(0,)) -> bytes:
    """An 8-bit RGBA PNG; `alpha_at(x, y)` gives each pixel's alpha, rows use the given filter types in turn."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    rows = []
    prev = bytearray(width * 4)
    for y in range(height):
        raw = bytearray()
        for x in range(width):
            raw += bytes((x * 10 % 256, y * 20 % 256, 90, alpha_at(x, y)))
        kind = filters[y % len(filters)]
        out = bytearray(len(raw))
        for i in range(len(raw)):
            left = raw[i - 4] if i >= 4 else 0
            up = prev[i]
            upleft = prev[i - 4] if i >= 4 else 0
            if kind == 0:
                pred = 0
            elif kind == 1:
                pred = left
            elif kind == 2:
                pred = up
            elif kind == 3:
                pred = (left + up) >> 1
            else:
                p = left + up - upleft
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upleft)
                pred = left if pa <= pb and pa <= pc else (up if pb <= pc else upleft)
            out[i] = (raw[i] - pred) & 255
        rows.append(bytes([kind]) + bytes(out))
        prev = raw
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b"")


# ---------------------------------------------------------------------------------------------
# The .blend reader


def test_reads_the_materials_of_a_real_blend():
    mats = bc.read_materials(HAY)
    assert [m.name for m in mats] == ["hay_material"]
    m = mats[0]
    assert m.rgb == pytest.approx((0.8, 0.8, 0.8), abs=1e-3)
    assert m.node_rgb == pytest.approx((0.8, 0.8, 0.8), abs=1e-3)
    assert m.images == [("hay.jpg", "//textures/hay.jpg")]


def test_list_prints_the_materials(capsys):
    assert bc.main(["--blend", str(HAY), "--list"]) == 0
    out = capsys.readouterr().out
    assert "hay_material" in out and "hay.jpg" in out and "1 material(s)" in out


def test_files_that_are_not_readable_blends_say_why(tmp_path, monkeypatch):
    text = tmp_path / "a.blend"
    text.write_bytes(b"hello world, not a blend at all")
    with pytest.raises(bc.BlendError, match="not a .blend file"):
        bc.read_materials(text)
    newer = tmp_path / "new.blend"
    newer.write_bytes(b"BLENDER17-01v0405" + b"\0" * 64)
    with pytest.raises(bc.BlendError, match="Blender 4.2"):
        bc.read_materials(newer)
    cut = tmp_path / "cut.blend"
    cut.write_bytes(b"BLENDER-v279" + b"\0" * 8)
    with pytest.raises(bc.BlendError):
        bc.read_materials(cut)
    zst = tmp_path / "zst.blend"
    zst.write_bytes(b"\x28\xb5\x2f\xfd" + b"\0" * 32)
    monkeypatch.setitem(sys.modules, "compression", None)  # a Python without the zstd module
    with pytest.raises(bc.BlendError, match="zstd-compressed.*Python 3.14"):
        bc.read_materials(zst)
    assert bc.main(["--blend", str(zst), "--list"]) == 1


def test_a_plain_blend_reads_like_its_gzip_copy(tmp_path):
    plain = tmp_path / "plain.blend"
    plain.write_bytes(gzip.decompress(HAY.read_bytes()))
    assert [(m.name, m.images) for m in bc.read_materials(plain)] == [(m.name, m.images) for m in bc.read_materials(HAY)]


# ---------------------------------------------------------------------------------------------
# Matching names


def test_names_match_as_the_staging_script_matched_them():
    by_name = {n: blend_material(n) for n in ("Body", "Glass.001", "Wheel_2", "Trim")}
    assert bc.lookup(by_name, "Body").name == "Body"
    assert bc.lookup(by_name, "Body.001").name == "Body"  # a trailing number is dropped
    assert bc.lookup(by_name, "Glass_001").name == "Glass.001"  # `_` and `.` are the same
    assert bc.lookup(by_name, "Wheel.2").name == "Wheel_2"
    assert bc.lookup(by_name, "Trim3").name == "Trim"  # trailing digits dropped
    assert bc.lookup(by_name, "Roof") is None


# ---------------------------------------------------------------------------------------------
# Patching a glb


def test_a_colour_is_set_from_the_node_colour_before_the_diffuse_colour(tmp_path):
    path = write_glb(tmp_path / "m.glb", [{"name": "Body"}, {"name": "Glass"}])
    out = tmp_path / "patched.glb"
    mats = [blend_material("Body", (0.8, 0.8, 0.8), node=(0.1, 0.2, 0.3)), blend_material("Glass", (0.5, 0.25, 0.125))]
    doc, blob = bc.read_glb(path)
    blob, report = bc.patch(doc, blob, mats, [tmp_path])
    bc.write_glb(out, doc, blob)
    got = patched(out)["materials"]
    assert got[0]["pbrMetallicRoughness"]["baseColorFactor"] == [0.1, 0.2, 0.3, 1.0]
    assert got[1]["pbrMetallicRoughness"]["baseColorFactor"] == [0.5, 0.25, 0.125, 1.0]
    assert got[0]["pbrMetallicRoughness"]["metallicFactor"] == 0.0
    assert report.unmatched == [] and len(report.patched) == 2
    # the validator's own reader still measures it and reports the new colours
    data = gltf.read_model(out, tmp_path)
    assert data.measured and data.triangles == 24 and not data.issues
    assert data.materials[0]["hex"] != data.materials[1]["hex"]


def test_unmatched_materials_are_reported_and_left_alone(tmp_path):
    path = write_glb(tmp_path / "m.glb", [{"name": "Body", "pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1]}}, {"name": "Mystery"}])
    doc, blob = bc.read_glb(path)
    _, report = bc.patch(doc, blob, [blend_material("Body"), blend_material("Spare")], [tmp_path])
    assert report.unmatched == ["Mystery"]
    assert report.unused == ["Spare"]
    assert "pbrMetallicRoughness" not in doc["materials"][1]


def test_map_pairs_names_the_matching_cannot(tmp_path):
    path = write_glb(tmp_path / "m.glb", [{"name": "Material.001"}])
    doc, blob = bc.read_glb(path)
    _, report = bc.patch(doc, blob, [blend_material("Red", (1, 0, 0))], [tmp_path], {"Material.001": "Red"})
    assert doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] == [1, 0, 0, 1.0]
    assert report.unmatched == []
    with pytest.raises(bc.GlbError, match="Nope"):
        bc.patch(doc, blob, [blend_material("Red")], [tmp_path], {"Material.001": "Nope"})


def test_a_png_is_embedded_as_it_is_and_the_old_texture_goes(tmp_path):
    g = Gltf()
    old = g.image(png(4, 4, full=True))
    g.doc["materials"] = [{"name": "Body", "pbrMetallicRoughness": {"baseColorTexture": {"index": old}}}, {"name": "Trim"}]
    for i in range(2):
        g.node(g.mesh(*box(1, 1, 1), material=i))
    path = tmp_path / "m.glb"
    path.write_bytes(g.glb())
    new = png(8, 8, full=True)
    (tmp_path / "tex").mkdir()
    (tmp_path / "tex" / "Hay.PNG").write_bytes(new)  # found by name, whatever the case
    doc, blob = bc.read_glb(path)
    blob, report = bc.patch(doc, blob, [blend_material("Body", images=[("hay", "//tex/hay.png")]), blend_material("Trim", (1, 1, 1))], [tmp_path / "tex"])
    out = tmp_path / "out.glb"
    bc.write_glb(out, doc, blob)
    result, binary = bc.read_glb(out)
    assert len(result["textures"]) == 1 and len(result["images"]) == 1  # the 4 x 4 one is pruned
    view = result["bufferViews"][result["images"][0]["bufferView"]]
    assert binary[view["byteOffset"] : view["byteOffset"] + view["byteLength"]] == new
    assert result["images"][0]["mimeType"] == "image/png"
    assert result["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] == {"index": 0}
    assert "alphaMode" not in result["materials"][0]
    data = gltf.read_model(out, tmp_path)
    assert data.measured and data.triangles == 24 and data.textures == 1 and not data.issues
    assert result["buffers"][0]["byteLength"] == len(binary)


def test_pruning_when_a_colour_replaces_the_only_texture(tmp_path):
    g = Gltf()
    old = g.image(png(4, 4, full=True))
    g.doc["materials"] = [{"name": "Body", "pbrMetallicRoughness": {"baseColorTexture": {"index": old}}}]
    g.node(g.mesh(*box(1, 1, 1), material=0))
    path = tmp_path / "m.glb"
    path.write_bytes(g.glb())
    before = len(g.glb())
    doc, blob = bc.read_glb(path)
    blob, _ = bc.patch(doc, blob, [blend_material("Body", (0.2, 0.4, 0.6))], [tmp_path])
    bc.write_glb(path, doc, blob)
    result, binary = bc.read_glb(path)
    assert "textures" not in result and "images" not in result
    assert len(path.read_bytes()) < before
    data = gltf.read_model(path, tmp_path)
    assert data.measured and data.textures == 0 and not data.issues


def test_alpha_in_a_texture_makes_the_material_a_cut_out(tmp_path):
    for filters in ((0,), (1, 2, 3, 4)):
        see_through = tmp_path / "leaf.png"
        see_through.write_bytes(rgba_png(6, 8, lambda x, y: 0 if (x, y) == (5, 7) else 255, filters))
        path = write_glb(tmp_path / "m.glb", [{"name": "Leaf"}])
        doc, blob = bc.read_glb(path)
        bc.patch(doc, blob, [blend_material("Leaf", images=[("leaf", "//leaf.png")])], [tmp_path])
        m = doc["materials"][0]
        assert (m["alphaMode"], m["alphaCutoff"], m["doubleSided"]) == ("MASK", 0.5, True), filters
        see_through.write_bytes(rgba_png(6, 8, lambda x, y: 255, filters))
        doc, blob = bc.read_glb(path)
        bc.patch(doc, blob, [blend_material("Leaf", images=[("leaf", "//leaf.png")])], [tmp_path])
        assert "alphaMode" not in doc["materials"][0], filters


def test_other_image_types_are_refused_with_a_clear_message(tmp_path, capsys):
    path = write_glb(tmp_path / "m.glb", [{"name": "hay_material"}])
    before = path.read_bytes()
    (tmp_path / "hay.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\0" * 20)
    code = bc.main(["--blend", str(HAY), "--glb", str(path), "--textures", str(tmp_path)])
    assert code == 1
    err = capsys.readouterr().err
    assert "is a JPEG" in err and "PNG only" in err and "hay.jpg" in err
    assert path.read_bytes() == before  # nothing was written


def test_a_missing_texture_falls_back_to_the_colour_and_says_so(tmp_path, capsys):
    path = write_glb(tmp_path / "m.glb", [{"name": "hay_material"}])
    out = tmp_path / "out.glb"
    assert bc.main(["--blend", str(HAY), "--glb", str(path), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "MISSING texture hay_material: hay.jpg" in text and "1 patched, 0 unmatched" in text
    factor = patched(out)["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"]
    assert factor == pytest.approx([0.8, 0.8, 0.8, 1.0], abs=1e-3)
    assert patched(path)["materials"][0] == {"name": "hay_material"}  # --out leaves the input alone


def test_without_out_the_glb_is_patched_in_place_and_unmatched_names_print(tmp_path, capsys):
    path = write_glb(tmp_path / "m.glb", [{"name": "hay_material"}, {"name": "Stray"}])
    assert bc.main(["--blend", str(HAY), "--glb", str(path)]) == 0
    text = capsys.readouterr().out
    assert "UNMATCHED glb material `Stray`" in text and "--map Stray=" in text
    assert "baseColorFactor" in patched(path)["materials"][0]["pbrMetallicRoughness"]


def test_usage_errors(tmp_path, capsys):
    assert bc.main(["--blend", str(HAY)]) == 2  # neither --glb nor --list
    assert bc.main(["--blend", str(HAY), "--glb", str(tmp_path / "none.glb")]) == 2
    assert bc.main(["--blend", str(HAY), "--glb", str(tmp_path / "x.glb"), "--map", "nonsense"]) == 2
    bad = tmp_path / "bad.glb"
    bad.write_bytes(b"not a glb at all, really")
    assert bc.main(["--blend", str(HAY), "--glb", str(bad)]) == 1
    assert bc.main(["--bogus"]) == 2


def test_png_alpha_probe_handles_other_png_kinds():
    assert bc.png_has_transparency(png(4, 4, full=True)) is False  # RGB, no alpha channel
    with pytest.raises(bc.TextureError):
        bc.png_info(b"GIF89a" + b"\0" * 40)
    header = struct.pack(">IIBBBBB", 2, 2, 16, 6, 0, 0, 0)
    sixteen = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + header + b"\0\0\0\0"
    assert bc.png_has_transparency(sixteen) is None  # cannot tell, says so
