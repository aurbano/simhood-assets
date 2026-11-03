"""tools/new_model.py: the manifest starter."""

import tomllib

import pytest
from builders import Gltf, TestRepo, box, box_glb, grid, slot_toml
from helpers import run

import new_model
from assets_lib import manifests

BENCH_SLOT = slot_toml("streets.bench", kind="street.bench", status="missing", size=(1.8, 0.6, 0.9))
VAN_SLOT = slot_toml("vehicles.panel-van", tint="body paint")


def repo_with(tmp_path, glb: bytes, stem="cargo", folder="vehicles", pack_toml=True) -> tuple[TestRepo, object]:
    repo = TestRepo(tmp_path / "repo", slots={"vehicles": VAN_SLOT, "streets": BENCH_SLOT})
    path = repo.write(f"packs/mine/{folder}/{stem}.glb", glb)
    if not pack_toml:
        (repo.root / "packs/mine/pack.toml").unlink()
    return repo, path


def lines_of(path, slot, repo=None):
    out: list[str] = []
    code = new_model.run(path, slot, repo, out=out.append)
    return code, "\n".join(out)


def test_a_vehicle_gets_length_and_a_forward_along_its_long_axis(tmp_path):
    repo, path = repo_with(tmp_path, box_glb(2.0, 2.5, 5.4))  # long along z
    code, text = lines_of(path, "vehicles.panel-van")
    assert code == 0
    doc = tomllib.loads(path.with_suffix(".toml").read_text())
    assert doc["id"] == "mine:cargo" and doc["slot"] == "vehicles.panel-van" and doc["kind"] == "van"
    assert doc["length"] == 5.4 and "height" not in doc  # the slot's own length
    assert doc["forward"] == "+z"
    assert doc["changes"] == []
    assert "licence" not in doc and "author" not in doc  # placeholders stay comments
    # the printed check: scaled size against the slot's, triangles against the budget
    assert "model units: 2.000 x 2.500 x 5.400" in text
    assert "triangles: 12 of 3000 ok" in text
    assert "height: 2.50 m against the slot's 2.50 m" in text and "FAIL" not in text


def test_a_prop_gets_height(tmp_path):
    repo, path = repo_with(tmp_path, box_glb(1.8, 0.9, 0.6), stem="Park Bench", folder="street")
    code, text = lines_of(path, "streets.bench")
    assert code == 0
    doc = tomllib.loads(path.with_suffix(".toml").read_text())
    assert doc["id"] == "mine:park-bench" and doc["kind"] == "street.bench"
    assert doc["height"] == 0.9 and "length" not in doc and doc["forward"] == "+x"


def test_the_manifest_is_valid_as_written_and_after_the_placeholders_are_filled(tmp_path):
    repo, path = repo_with(tmp_path, box_glb(2.0, 2.5, 5.4))
    assert lines_of(path, "vehicles.panel-van")[0] == 0
    manifest = path.with_suffix(".toml")
    # licence, author and the AI statement come from pack.toml, so an original work is complete as written
    assert run(repo)["errors"] == []
    manifest.write_text(
        manifest.read_text() + 'licence_evidence = "licences/page.html"\nsource = "https://example.org/van"\ntint_key = "#d9d9d9"\n'
    )
    manifests.parse_manifest(manifest.read_text())
    report = run(repo)
    assert report["errors"] == [], report["errors"]


def test_a_wrong_size_is_visible_straight_away(tmp_path):
    repo, path = repo_with(tmp_path, box_glb(2.0, 1.0, 5.4))  # 1 m tall against the slot's 2.5 m
    code, text = lines_of(path, "vehicles.panel-van")
    assert code == 0
    assert "height: 1.00 m against the slot's 2.50 m (150% off) FAIL" in text
    assert "try the other `forward` values" in text


def test_a_flat_model_cannot_take_a_height_key(tmp_path, capsys):
    g = Gltf()
    g.node(g.mesh(*grid(3)))  # no height at all
    repo, path = repo_with(tmp_path, g.glb(), stem="bench", folder="street")
    assert new_model.main([str(path), "--slot", "streets.bench"]) == 1
    assert "no extent along its height axis" in capsys.readouterr().err


def test_triangles_over_budget_are_flagged(tmp_path):
    g = Gltf()
    g.node(g.mesh(*grid(30)))  # 1800 flat triangles, a prop's budget is 1500
    g.node(g.mesh(*box(1.8, 0.9, 0.6)))  # and some height
    repo, path = repo_with(tmp_path, g.glb(), stem="bench", folder="street")
    _, text = lines_of(path, "streets.bench")
    assert "triangles: 1812 of 1500 OVER BUDGET" in text


def test_a_tinted_slot_lists_the_models_colours(tmp_path):
    g = Gltf()
    g.doc["materials"] = [{"name": "Body", "pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1]}}]
    g.node(g.mesh(*box(2, 2.5, 5.4), material=0))
    repo, path = repo_with(tmp_path, g.glb())
    lines_of(path, "vehicles.panel-van")
    text = path.with_suffix(".toml").read_text()
    assert "# This slot is tinted (body paint)" in text and "#ff0000" in text and '# tint_key = "#rrggbb"' in text


def test_it_never_overwrites_a_manifest(tmp_path, capsys):
    repo, path = repo_with(tmp_path, box_glb(2.0, 2.5, 5.4))
    manifest = path.with_suffix(".toml")
    manifest.write_text("mine already\n")
    assert new_model.main([str(path), "--slot", "vehicles.panel-van"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert manifest.read_text() == "mine already\n"


def test_unknown_slot_suggests_close_ones(tmp_path, capsys):
    repo, path = repo_with(tmp_path, box_glb(2.0, 2.5, 5.4))
    assert new_model.main([str(path), "--slot", "vehicles.panel-vann"]) == 1
    err = capsys.readouterr().err
    assert "no slot `vehicles.panel-vann`" in err and "vehicles.panel-van" in err
    assert not path.with_suffix(".toml").exists()


def test_a_pack_without_pack_toml_prints_what_to_create(tmp_path):
    repo, path = repo_with(tmp_path, box_glb(2.0, 2.5, 5.4), pack_toml=False)
    code, text = lines_of(path, "vehicles.panel-van")
    assert code == 0
    assert "mine has no pack.toml; create" in text and 'id = "mine:base"' in text
    assert tomllib.loads(path.with_suffix(".toml").read_text())["id"] == "mine:cargo"


def test_models_that_cannot_be_measured_or_are_not_in_a_pack_are_refused(tmp_path, capsys):
    repo, path = repo_with(tmp_path, b"junk, not a model")
    assert new_model.main([str(path), "--slot", "vehicles.panel-van"]) == 1
    assert "cannot measure" in capsys.readouterr().err and not path.with_suffix(".toml").exists()
    stray = tmp_path / "stray.glb"
    stray.write_bytes(box_glb(1, 1, 1))
    assert new_model.main([str(stray), "--slot", "vehicles.panel-van", "--repo", str(repo.root)]) == 1
    assert "not inside a pack" in capsys.readouterr().err
    assert new_model.main([str(tmp_path / "missing.glb"), "--slot", "vehicles.panel-van"]) == 1
    assert new_model.main(["--slot"]) == 2


def test_a_square_footprint_far_off_says_forward_is_not_the_cause(tmp_path):
    repo, path = repo_with(tmp_path, box_glb(2.0, 2.0, 2.0))
    _, text = lines_of(path, "vehicles.panel-van")
    assert "footprint short side" in text and "FAIL" in text
    assert "nearly square, so `forward` is not the cause" in text
