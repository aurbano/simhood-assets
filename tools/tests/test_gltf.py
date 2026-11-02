"""The glTF reader and the measuring profile (design D5)."""

import math
import struct

import pytest
from builders import Gltf, box, grid, pack_glb, png
from assets_lib import gltf, normalise


def read(tmp_path, glb: bytes, name="m.glb", **kw):
    path = tmp_path / name
    path.write_bytes(glb)
    return gltf.read_model(path, tmp_path, **kw)


def refusals(data):
    return [i.message for i in data.issues if i.rule == "gltf.profile"]


def test_triangles_are_counted_per_node_instance(tmp_path):
    g = Gltf()
    m = g.mesh(*grid(20))
    g.node(m)
    g.node(m, translation=[1, 0, 0])
    data = read(tmp_path, g.glb())
    assert data.measured and data.triangles == 1600


def test_lists_strips_and_fans_are_counted_as_n_over_3_and_n_minus_2(tmp_path):
    pos = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0.5, 2, 0), (2, 2, 0)]
    for mode, idx, want in ((4, list(range(6)), 2), (5, list(range(6)), 4), (6, list(range(6)), 4)):
        g = Gltf()
        g.node(g.mesh(pos, idx, mode=mode))
        assert read(tmp_path, g.glb()).triangles == want
    g = Gltf()
    g.node(g.mesh(pos, None))  # no indices: vertex count / 3
    assert read(tmp_path, g.glb()).triangles == 2


@pytest.mark.parametrize("mode,what", [(0, "points"), (1, "lines"), (2, "lines"), (3, "lines")])
def test_points_and_lines_are_refused(tmp_path, mode, what):
    g = Gltf()
    g.node(g.mesh([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [0, 1, 2], mode=mode))
    data = read(tmp_path, g.glb())
    assert not data.measured and data.triangles is None
    assert any(what in m for m in refusals(data))


def test_bounds_come_from_decoded_positions_not_declared_min_max(tmp_path):
    g = Gltf()
    g.node(g.mesh(*box(2, 1, 3)))
    g.doc["accessors"][0]["min"] = [-50, -50, -50]
    g.doc["accessors"][0]["max"] = [50, 50, 50]
    data = read(tmp_path, g.glb())
    ext = [b - a for a, b in zip(data.bounds_min, data.bounds_max)]
    assert ext == pytest.approx([2, 1, 3])


def test_world_transforms_compose_from_the_roots_trs_and_matrix(tmp_path):
    g = Gltf()
    m = g.mesh(*box(1, 1, 1))
    child = g.node(m, root=False, translation=[0, 2, 0], scale=[2, 2, 2])
    g.node(None, children=[child], translation=[10, 0, 0])
    data = read(tmp_path, g.glb())
    assert data.bounds_min == pytest.approx((9, 2, -1)) and data.bounds_max == pytest.approx((11, 4, 1))
    h = Gltf()
    # column-major matrix: scale 3 on x, translate (0, 5, 0)
    h.node(h.mesh(*box(1, 1, 1)), matrix=[3, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 5, 0, 1])
    data = read(tmp_path, h.glb())
    assert data.bounds_min == pytest.approx((-1.5, 5, -0.5)) and data.bounds_max == pytest.approx((1.5, 6, 0.5))


def test_a_quarter_turn_swaps_extents_and_a_general_turn_is_measured_per_vertex(tmp_path):
    s = math.sqrt(0.5)
    g = Gltf()
    g.node(g.mesh(*box(4, 1, 2)), rotation=[0, s, 0, s])  # 90 degrees about Y
    data = read(tmp_path, g.glb())
    ext = [b - a for a, b in zip(data.bounds_min, data.bounds_max)]
    assert ext == pytest.approx([2, 1, 4])
    h = Gltf()
    c, sn = math.cos(math.pi / 8), math.sin(math.pi / 8)  # 45 degrees about Y
    h.node(h.mesh(*box(2, 1, 2)), rotation=[0, sn, 0, c])
    data = read(tmp_path, h.glb())
    ext = [b - a for a, b in zip(data.bounds_min, data.bounds_max)]
    assert ext == pytest.approx([2 * math.sqrt(2), 1, 2 * math.sqrt(2)])


def test_only_the_default_scene_is_measured_and_several_scenes_need_a_default(tmp_path):
    g = Gltf()
    a = g.mesh(*box(1, 1, 1))
    b = g.mesh(*grid(10))
    n0, n1 = g.node(a, root=False), g.node(b, root=False)
    g.doc["scenes"] = [{"nodes": [n0]}, {"nodes": [n1]}]
    g.doc["scene"] = 0
    assert read(tmp_path, g.glb()).triangles == 12
    del g.doc["scene"]
    data = read(tmp_path, g.glb())
    assert not data.measured and any("scenes" in m for m in refusals(data))


def test_nodes_outside_the_scene_are_not_counted(tmp_path):
    g = Gltf()
    g.node(g.mesh(*box(1, 1, 1)))
    g.node(g.mesh(*grid(10)), root=False)
    assert read(tmp_path, g.glb()).triangles == 12


def _one(g: Gltf, tmp_path):
    return read(tmp_path, g.glb())


def test_sparse_accessors_morph_targets_and_required_extensions_are_refused(tmp_path):
    g = Gltf()
    g.node(g.mesh(*box(1, 1, 1)))
    g.doc["accessors"][0]["sparse"] = {"count": 1}
    assert any("sparse" in m for m in refusals(_one(g, tmp_path)))

    g = Gltf()
    g.node(g.mesh(*box(1, 1, 1)))
    g.doc["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": 0}]
    assert any("morph" in m for m in refusals(_one(g, tmp_path)))

    g = Gltf()
    g.node(g.mesh(*box(1, 1, 1)))
    g.doc["extensionsUsed"] = ["KHR_texture_transform"]
    g.doc["extensionsRequired"] = ["KHR_texture_transform"]
    data = _one(g, tmp_path)
    assert [i.rule for i in data.issues] == ["gltf.required-extension"]
    assert data.measured  # an allowed extension never changes the geometry


def test_non_float_and_quantised_positions_are_refused(tmp_path):
    g = Gltf()
    g.node(g.mesh(*box(1, 1, 1)))
    g.doc["accessors"][0]["componentType"] = 5123
    assert any("float32" in m for m in refusals(_one(g, tmp_path)))
    g = Gltf()
    g.node(g.mesh(*box(1, 1, 1)))
    g.doc["accessors"][0]["normalized"] = True
    assert refusals(_one(g, tmp_path))


def test_limits_on_nodes_depth_meshes_and_vertices(tmp_path):
    g = Gltf()
    m = g.mesh(*box(1, 1, 1))
    for _ in range(257):
        g.node(m)
    assert any("257 nodes" in x for x in refusals(_one(g, tmp_path)))

    g = Gltf()
    m = g.mesh(*box(1, 1, 1))
    child = g.node(m, root=False)
    for _ in range(gltf.MAX_DEPTH):
        child = g.node(None, root=False, children=[child])
    g.doc["scenes"][0]["nodes"] = [child]
    assert any("deep" in x for x in refusals(_one(g, tmp_path)))

    g = Gltf()
    for _ in range(gltf.MAX_MESHES + 1):
        g.node(g.mesh(*box(1, 1, 1)))
    assert any("meshes" in x for x in refusals(_one(g, tmp_path)))

    g = Gltf()
    n = 100_001
    g.node(g.mesh([(i * 1e-3, 0.0, 0.0) for i in range(n)] * 2, None))
    assert any("vertices" in x for x in refusals(_one(g, tmp_path)))


def test_an_accessor_outside_its_buffer_view_is_refused(tmp_path):
    g = Gltf()
    g.node(g.mesh(*box(1, 1, 1)))
    g.doc["accessors"][0]["count"] = 9
    assert any("outside its buffer view" in x for x in refusals(_one(g, tmp_path)))
    g = Gltf()
    g.node(g.mesh(*box(1, 1, 1)))
    g.doc["bufferViews"][0]["byteLength"] = 10**6
    assert refusals(_one(g, tmp_path))


def test_a_cycle_or_a_shared_node_is_refused(tmp_path):
    g = Gltf()
    m = g.mesh(*box(1, 1, 1))
    a = g.node(m, root=True)
    b = g.node(m, root=False, children=[a])
    g.doc["nodes"][a]["children"] = [b]
    assert refusals(_one(g, tmp_path))


def test_textures_png_only_at_most_1024_and_two(tmp_path):
    g = Gltf()
    g.node(g.mesh(*box(1, 1, 1)))
    g.image(png(2048, 16))
    g.image(png(8, 8, full=True))
    g.image(b"\xff\xd8\xff\xe0jpeg", "image/jpeg")
    data = _one(g, tmp_path)
    messages = [i.message for i in data.issues if i.rule == "gltf.texture"]
    assert data.textures == 3
    assert any("3 textures" in m for m in messages)
    assert any("2048 x 16" in m for m in messages)
    assert any("JPEG" in m for m in messages)
    assert data.measured


def test_extensions_are_limited_to_the_allowlist(tmp_path):
    g = Gltf()
    g.node(g.mesh(*box(1, 1, 1)))
    g.doc["extensionsUsed"] = ["KHR_materials_unlit", "GODOT_single_root", "KHR_draco_mesh_compression"]
    data = _one(g, tmp_path)
    ext = [i for i in data.issues if i.rule == "gltf.extension"]
    assert len(ext) == 1 and "KHR_draco_mesh_compression" in ext[0].message


def test_an_external_uri_is_refused_in_a_glb_and_not_followed(tmp_path):
    g = Gltf()
    g.node(g.mesh(*box(1, 1, 1)))
    g.doc["buffers"].append({"byteLength": 4, "uri": "other.bin"})
    data = _one(g, tmp_path)
    assert [i.rule for i in data.issues] == ["gltf.external-uri"]
    assert data.triangles is None


def test_the_grandfathered_graph_reads_relative_files_inside_the_pack_only(tmp_path):
    g = Gltf()
    g.node(g.mesh(*box(1, 1, 1)))
    doc = g.doc
    blob = bytes(g.bin)
    (tmp_path / "m.bin").write_bytes(blob)
    doc["buffers"] = [{"byteLength": len(blob), "uri": "m.bin"}]
    import json

    (tmp_path / "m.gltf").write_text(json.dumps(doc))
    data = gltf.read_model(tmp_path / "m.gltf", tmp_path, graph_ok=True)
    assert data.measured and data.triangles == 12 and data.resources == ["m.bin"]
    assert [i.rule for i in gltf.read_model(tmp_path / "m.gltf", tmp_path, graph_ok=False).issues] == ["gltf.external-uri"]
    doc["buffers"][0]["uri"] = "../m.bin"
    (tmp_path / "m.gltf").write_text(json.dumps(doc))
    assert gltf.read_model(tmp_path / "m.gltf", tmp_path, graph_ok=True).issues[0].rule == "gltf.external-uri"


def test_material_base_colours_are_reported_as_srgb_hex(tmp_path):
    g = Gltf()
    g.doc["materials"] = [{"name": "paint", "pbrMetallicRoughness": {"baseColorFactor": [1.0, 0.2158605, 0.0, 1]}}]
    g.node(g.mesh(*box(1, 1, 1), material=0))
    data = _one(g, tmp_path)
    assert data.materials == [{"name": "paint", "hex": "#ff8000", "textured": False}]


def test_a_damaged_container_is_reported_not_raised(tmp_path):
    for blob in (b"glTF", b"glTF" + b"\x02\x00\x00\x00" + b"\xff" * 20, b"not gltf"):
        data = read(tmp_path, blob)
        assert data.issues and not data.measured
    data = read(tmp_path, pack_glb({"asset": {"version": "1.0"}}, None))
    assert data.issues[0].rule == "gltf.invalid"


def test_normalisation_follows_the_client():
    b0, b1 = (0, 0, 0), (2.0, 1.0, 4.0)
    s = normalise.normalise(b0, b1, "+x", "length", 4.0)
    assert (s.factor, s.length, s.width, s.height) == (2.0, 4.0, 8.0, 2.0)
    s = normalise.normalise(b0, b1, "+z", "length", 4.0)  # the z extent becomes the length
    assert (s.factor, s.length, s.width) == (1.0, 4.0, 2.0)
    s = normalise.normalise(b0, b1, "-x", "height", 3.0)
    assert (s.factor, s.height) == (3.0, 3.0)
    s = normalise.normalise(b0, b1, "-z", "scale", 0.5)
    assert (s.factor, s.length, s.width, s.height) == (0.5, 2.0, 1.0, 0.5)
    assert isinstance(normalise.normalise((0, 0, 0), (0, 1, 1), "+x", "length", 4.0), str)


def test_a_flat_axis_is_not_compared_with_the_slot():
    s = normalise.Scaled(1.0, 4.5, 0.0, 4.0)
    assert normalise.compare(s, {"length": 4.5, "width": 0.1, "height": 4.0}, None) == []
    s = normalise.Scaled(1.0, 4.5, 1.0, 4.0)
    assert [x[0] for x in normalise.compare(s, {"length": 4.5, "width": 0.1, "height": 4.0}, None)] == ["footprint short side"]
