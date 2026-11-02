"""The client reader's refusal table (`bindings/sim-gdext/src/assets.rs` tests), ported as
validator tests: whatever the reader refuses, the validator refuses too."""

import pytest
from builders import TestRepo, box_glb, manifest_toml, slot_toml
from assets_lib import manifests
from helpers import rules, run, where

CAR = 'id = "test:car"\nkind = "car"\n'
BAD_MANIFESTS = [
    (CAR + "lengths = 4.0\n", "a near-miss key"),
    (CAR + "length = 4.0\ncolour = 1\n", "an unknown key"),
    (CAR + "length = 0.0\n", "a zero length"),
    (CAR + "length = 61.0\n", "an overlong model"),
    (CAR, "no size"),
    (CAR + "length = 4.0\nscale = 2.0\n", "two sizes"),
    (CAR + "scale = -1.0\n", "a negative scale"),
    (CAR + "length = 4.0\nweight = 0\n", "a zero weight"),
    (CAR + 'length = 4.0\nforward = "up"\n', "a bad forward"),
    (CAR + "length = 4.0\ntint_tolerance = 0.0\n", "a zero tolerance"),
    ('id = "car"\nkind = "car"\nlength = 4.0\n', "an id without a namespace"),
    ('id = "Test:Car"\nkind = "car"\nlength = 4.0\n', "an uppercase id"),
    (CAR + 'length = 4.0\ntint_key = "ff8000"\n', "a tint key without #"),
    (CAR + 'length = 4.0\ntint_key = "#ff80"\n', "a short tint key"),
    (CAR + 'length = 4.0\ntint_key = "#ff800g"\n', "a non-hex tint key"),
    (CAR + 'length = 4.0\ntint_key = "#ff80000"\n', "a long tint key"),
]
BAD_PACKS = [
    'id = "test:p"\nversion = "1"\nlicence = "CC0-1.0"\n',
    'id = "test:p"\nversion = "1"\nai_generated = "no"\n',
    'id = "test:p"\nversion = "1"\nlicence = " "\nai_generated = "no"\n',
    'id = "test:p"\nversion = "1"\nlicence = "x"\nai_generated = "maybe"\n',
    'id = "p"\nversion = "1"\nlicence = "x"\nai_generated = "no"\n',
]


@pytest.mark.parametrize("text,why", BAD_MANIFESTS)
def test_reader_table_bad_manifests_are_refused(text, why, tmp_path):
    with pytest.raises(manifests.ManifestError):
        manifests.parse_manifest(text)
    repo = TestRepo(tmp_path / "r", slots={"vehicles": slot_toml("vehicles.panel-van", kind="car", status="missing")})
    repo.model("car", box_glb(4, 1.5, 1.8), text)
    assert "manifest.invalid" in rules(run(repo)), why


@pytest.mark.parametrize("kind", ["bike", "tree"])
def test_reader_table_unknown_kinds_and_bare_families_are_refused(kind, tmp_path):
    repo = TestRepo(tmp_path / "r", slots={"vehicles": slot_toml("vehicles.panel-van", kind="car")})
    repo.model("x", box_glb(4, 1.5, 1.8), manifest_toml("test:x", kind=kind, slot="vehicles.panel-van", length=4.0))
    errors = run(repo)["errors"]
    assert any(e["rule"] in ("slot.kind", "manifest.kind") and kind in e["message"] for e in errors)


@pytest.mark.parametrize("text", BAD_PACKS)
def test_reader_table_bad_packs_are_refused(text, tmp_path):
    with pytest.raises(manifests.ManifestError):
        manifests.parse_pack(text)
    repo = TestRepo(tmp_path / "r", pack_toml=text)
    assert "pack.invalid" in rules(run(repo))


def test_reader_table_a_minimal_manifest_takes_the_defaults():
    m = manifests.parse_manifest('id = "test:car"\nkind = "car"\nlength = 4.0\n')
    assert (m.measure, m.measure_value, m.forward, m.weight, m.tint_key, m.flat_shading) == ("length", 4.0, "+x", 1, None, False)
    m = manifests.parse_manifest('id = "test:oak"\nkind = "tree.broadleaf"\nheight = 8.0\n')
    assert (m.measure, m.measure_value) == ("height", 8.0)
    m = manifests.parse_manifest('id = "test:rock"\nkind = "cover.pebbles"\nscale = 0.6\ntint_key = "#FF8000"\n')
    assert (m.measure, m.tint_key) == ("scale", "#ff8000")


def test_reader_table_the_provenance_keys_are_accepted():
    m = manifests.parse_manifest(
        'id = "test:car"\nkind = "car"\nlength = 4.0\nslot = "vehicles.car"\ntitle = "Sedan"\nattribution = "Sedan by A. Maker"\n'
        'licence_evidence = "licences/sedan.html"\nchanges = ["scaled"]\ncontributors = ["someone"]\n'
        'ai_tool = "t"\nai_model = "m"\nai_plan = "paid"\n'
    )
    assert (m.slot, m.title, m.changes, m.contributors) == ("vehicles.car", "Sedan", ["scaled"], ["someone"])


def test_reader_table_a_folder_skips_bad_models_and_names_each_problem(tmp_path):
    repo = TestRepo(tmp_path / "r", slots={"vehicles": slot_toml("vehicles.panel-van", kind="car", variants=5)})
    repo.model("a", box_glb(4, 1.5, 1.8), manifest_toml("test:a", kind="car", length=4.0))
    repo.model("c", box_glb(4, 1.5, 1.8), manifest_toml("test:a", kind="car", length=4.0))  # same id
    repo.model("d", None, manifest_toml("test:d", kind="car", length=4.0))  # no model beside it
    repo.model("e", box_glb(4, 1.5, 1.8), 'id = "test:e"\nkind = "car"\n')  # no size
    repo.model("b", box_glb(4, 1.5, 1.8), manifest_toml("test:b", kind="car", length=4.0))
    report = run(repo)
    assert where(report, "manifest.duplicate-id")[0]["path"] == "packs/mine/vehicles/c.toml"
    assert where(report, "manifest.model-missing")[0]["path"] == "packs/mine/vehicles/d.toml"
    assert "exactly one" in where(report, "manifest.invalid")[0]["message"]
    assert sorted(m["id"] for m in report["models"]) == ["test:a", "test:a", "test:b"]


def test_a_model_with_no_manifest_is_an_error(tmp_path):
    repo = TestRepo(tmp_path / "r")
    repo.write("packs/mine/vehicles/stray.glb", box_glb(1, 1, 1))
    assert where(run(repo), "manifest.missing")[0]["path"] == "packs/mine/vehicles/stray.glb"
