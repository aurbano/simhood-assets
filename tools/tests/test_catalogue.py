"""specs/asset-catalogue: ACAT-001 to ACAT-007."""

from pathlib import Path

import pytest
from builders import REPO, TestRepo, box_glb, manifest_toml, slot_toml, van_repo, Gltf
from helpers import row, rules, run, where


def test_ACAT_001_a_slot_with_an_unknown_kind_is_refused(tmp_path):
    repo = TestRepo(tmp_path / "r", slots={"vehicles": slot_toml("vehicles.hovercraft", kind="hovercraft", status="missing")})
    errors = where(run(repo), "catalogue.kind")
    assert len(errors) == 1
    e = errors[0]
    assert e["path"] == "catalogue/vehicles.toml" and "vehicles.hovercraft" in e["message"] and "hovercraft" in e["message"]


def test_ACAT_002_slot_ids_are_unique_and_carry_their_domain(tmp_path):
    text = slot_toml("vehicles.van", status="missing") * 2 + slot_toml("streets.bench", kind="street.bench", status="missing")
    report = run(TestRepo(tmp_path / "r", slots={"vehicles": text}))
    assert rules(report) == ["catalogue.domain", "catalogue.duplicate"]
    assert "listed twice" in where(report, "catalogue.duplicate")[0]["message"]
    assert "streets.bench" in where(report, "catalogue.domain")[0]["message"]


def test_ACAT_003_a_shipped_model_must_map_to_a_slot(tmp_path):
    repo = van_repo(tmp_path)
    repo.model("none", box_glb(5.4, 2.5, 2.0), manifest_toml("mine:none", slot=None))
    repo.model("ghost", box_glb(5.4, 2.5, 2.0), manifest_toml("mine:ghost", slot="vehicles.ghost"))
    repo.model("kind", box_glb(5.4, 2.5, 2.0), manifest_toml("mine:kind", kind="car"))
    report = run(repo)
    assert sorted(rules(report)) == ["slot.kind", "slot.missing", "slot.unknown"]
    assert {e["path"] for e in report["errors"]} == {
        "packs/mine/vehicles/none.toml", "packs/mine/vehicles/ghost.toml", "packs/mine/vehicles/kind.toml",
    }


def test_ACAT_004_statuses_agree_with_what_ships(tmp_path):
    slots = slot_toml("vehicles.panel-van", status="missing") + slot_toml("vehicles.empty", status="have")
    repo = TestRepo(tmp_path / "r", slots={"vehicles": slots})
    repo.model("van", box_glb(5.4, 2.5, 2.0), manifest_toml("mine:van"))
    report = run(repo)
    assert rules(report) == ["slot.status", "slot.status"]
    messages = " | ".join(e["message"] for e in report["errors"])
    assert "vehicles.empty" in messages and "`have`" in messages and "no manifest names it" in messages
    assert "vehicles.panel-van" in messages and "`missing`" in messages and "packs/mine/vehicles/van.toml" in messages


def test_ACAT_005_the_seeded_catalogue_matches_the_shipped_packs():
    """Strict mode over this repository passes; every model names a slot; some slot is missing."""
    from assets_lib import checker

    report = checker.validate(REPO)
    assert report["errors"] == [], report["errors"][:5]
    assert len(report["models"]) == 115
    assert all(m["slot"] for m in report["models"])
    assert any(s["status"] == "missing" for s in report["slots"])


def test_ACAT_006_a_slot_override_replaces_the_class_budget(tmp_path):
    slots = (
        slot_toml("construction.big-crane", kind="site.crane", size=(2, 1, 1), max_triangles=7500)
        + slot_toml("construction.plain-crane", kind="site.crane", size=(2, 1, 1))
    )
    repo = TestRepo(tmp_path / "r", slots={"construction": slots})
    for stem, slot, tris in (("big", "construction.big-crane", 7100), ("plain", "construction.plain-crane", 6100)):
        g = Gltf()
        corners = [(0, 0, 0), (2, 0, 0), (2, 1, 1)]
        g.node(g.mesh(corners * tris, None))  # `tris` identical triangles spanning 2 x 1 x 1
        repo.model(stem, g.glb(), manifest_toml(f"mine:{stem}", kind="site.crane", slot=slot, length=2.0), folder="construction")
    report = run(repo)
    big, plain = row(report, "mine:big"), row(report, "mine:plain")
    assert big["budget"] == 7500 and plain["budget"] == 6000
    over = where(report, "budget.triangles")
    assert len(over) == 1 and over[0]["model"] == "mine:plain"
    assert str(plain["triangles"]) in over[0]["message"] and "6000" in over[0]["message"]
    assert big["triangles"] == 7100 and plain["triangles"] == 6100


def test_ACAT_007_unfilled_variants_are_reported(tmp_path):
    repo = TestRepo(tmp_path / "r", slots={"vehicles": slot_toml("vehicles.panel-van", variants=3)})
    repo.model("van", box_glb(5.4, 2.5, 2.0), manifest_toml("mine:van"))
    report = run(repo)
    assert report["ok"]
    assert report["slots"] == [{"id": "vehicles.panel-van", "status": "have", "models": ["mine:van"], "wanted_more": 2}]


def test_the_catalogue_schema_names_every_missing_or_bad_field(tmp_path):
    bad = (
        '[[slot]]\nid = "vehicles.x"\nkind = "van"\nname = "X"\nsize = { length = 1, width = 0, height = 1 }\n'
        'style = ""\npriority = "urgent"\nstatus = "gone"\nvariants = 0\ncolour = 1\n'
    )
    report = run(TestRepo(tmp_path / "r", slots={"vehicles": bad}))
    messages = " ".join(e["message"] for e in report["errors"])
    for needle in ("size", "style", "priority", "status", "variants", "colour"):
        assert needle in messages
    assert run(van_repo(tmp_path, "ok"))["errors"] == []


def test_a_missing_domain_file_and_a_bad_kinds_file_are_errors(tmp_path):
    repo = TestRepo(tmp_path / "r")
    (repo.root / "catalogue" / "people.toml").unlink()
    repo.write("catalogue/kinds.toml", 'classes = { prop = 1500, vehicle = 3000 }\n[[kind]]\nid = "x"\nclass = "huge"\nname = "X"\n')
    report = run(repo)
    assert {"catalogue.missing", "catalogue.schema"} <= set(rules(report))


def test_flat_pieces_are_not_compared_on_their_flat_axis(tmp_path):
    slots = slot_toml("building-parts.door", kind="street.bench", size=(4.5, 0.1, 4.0))
    repo = TestRepo(tmp_path / "r", slots={"building-parts": slots})
    repo.model("door", box_glb(4.5, 4.0, 0.0), manifest_toml("mine:door", kind="street.bench", slot="building-parts.door", length=4.5), folder="parts")
    report = run(repo)
    assert report["errors"] == [] and report["warnings"] == []
    assert row(report, "mine:door")["size_m"]["width"] == 0.0
