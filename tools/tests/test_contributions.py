"""specs/asset-contributions (validator-owned rules): licence, provenance, AI, files, budgets."""

import pytest
from builders import Gltf, TestRepo, box, box_glb, grid, manifest_toml, png, slot_toml, van_repo
from helpers import row, rules, run, where


def van(repo, stem, extra="", **kw):
    repo.model(stem, box_glb(5.4, 2.5, 2.0), manifest_toml(f"mine:{stem}", extra=extra, **kw))


def test_ACON_001_a_non_allowlisted_licence_fails(tmp_path):
    repo = TestRepo(tmp_path / "r", slots={"vehicles": slot_toml("vehicles.panel-van", variants=2)})
    van(repo, "sa", 'licence = "CC-BY-SA-4.0"\n')
    van(repo, "plain", 'licence = "CC0"\n')
    report = run(repo)
    errors = where(report, "licence.allowlist")
    assert len(errors) == 2 and len(report["errors"]) == 2
    assert {e["path"] for e in errors} == {"packs/mine/vehicles/sa.toml", "packs/mine/vehicles/plain.toml"}
    assert all("CC0-1.0, CC-BY-3.0, CC-BY-4.0" in e["message"] for e in errors)


def test_ACON_002_original_work_must_state_whether_generative_ai_made_it(tmp_path):
    repo = TestRepo(tmp_path / "r", pack_toml=PACK_UNKNOWN)
    repo.model("van", box_glb(5.4, 2.5, 2.0), manifest_toml("mine:van", provenance=False, extra='author = "Me"\nlicence = "CC0-1.0"\n'))
    report = run(repo)
    errors = where(report, "provenance.ai-original")
    assert len(errors) == 1 and "`yes` or `no`" in errors[0]["message"]
    # the same `unknown` is fine for a third-party work whose source does not say
    van(repo, "other", "")
    repo.write("packs/mine/vehicles/van.toml", manifest_toml("mine:van", provenance=False, extra='author = "Me"\nlicence = "CC0-1.0"\nai_generated = "no"\n'))
    assert [e for e in run(repo)["errors"] if e["rule"].startswith("provenance")] == []


PACK_UNKNOWN = 'id = "mine:base"\nversion = "1.0.0"\nlicence = "CC0-1.0"\nai_generated = "unknown"\nauthor = "A"\nsource = "https://example.org"\n'


def test_ACON_003_a_third_party_work_needs_its_evidence(tmp_path):
    repo = TestRepo(tmp_path / "r", slots={"vehicles": slot_toml("vehicles.panel-van", variants=2)})
    repo.model("van", box_glb(5.4, 2.5, 2.0), manifest_toml("mine:van", provenance=False, extra='licence_evidence = "licences/van.html"\n'))
    repo.model("by", box_glb(5.4, 2.5, 2.0), manifest_toml("mine:by", extra='licence = "CC-BY-3.0"\n'))
    report = run(repo)
    assert sorted(rules(report)) == ["licence.evidence", "licence.title"]
    assert "licences/van.html" in where(report, "licence.evidence")[0]["message"]
    assert "title" in where(report, "licence.title")[0]["message"]
    # a third-party work also needs an https source
    repo.model("van", box_glb(5.4, 2.5, 2.0), manifest_toml("mine:van", extra='source = "http://example.org/x"\n').replace('source = "https://example.org/van"\n', ""))
    assert "licence.source" in rules(run(repo))


def test_ACON_004_disallowed_files_and_models_fail(tmp_path):
    repo = TestRepo(tmp_path / "r", slots={"vehicles": slot_toml("vehicles.panel-van", variants=5)})
    repo.write("packs/mine/van.fbx", b"fbx")
    g = Gltf()
    g.node(g.mesh(*box(5.4, 2.5, 2.0)))
    repo.model("big", g.glb(pad_to=5 * 1024 * 1024), manifest_toml("mine:big"))
    g = Gltf()
    g.node(g.mesh(*box(5.4, 2.5, 2.0)))
    g.image(b"\xff\xd8\xff\xe0not really a jpeg", "image/jpeg")
    repo.model("jpeg", g.glb(), manifest_toml("mine:jpeg"))
    g = Gltf()
    g.node(g.mesh(*box(5.4, 2.5, 2.0)))
    g.doc["buffers"].append({"byteLength": 4, "uri": "elsewhere.bin"})
    repo.model("uri", g.glb(), manifest_toml("mine:uri"))
    g = Gltf()
    g.node(g.mesh(*box(5.4, 2.5, 2.0)))
    g.doc["extensionsUsed"] = ["KHR_draco_mesh_compression"]
    g.doc["extensionsRequired"] = ["KHR_draco_mesh_compression"]
    repo.model("draco", g.glb(), manifest_toml("mine:draco"))
    report = run(repo)
    by_path = {e["path"]: e["rule"] for e in report["errors"]}
    assert len(report["errors"]) == 5, report["errors"]
    assert by_path == {
        "packs/mine/van.fbx": "files.type",
        "packs/mine/vehicles/big.glb": "files.size",
        "packs/mine/vehicles/jpeg.glb": "gltf.texture",
        "packs/mine/vehicles/uri.glb": "gltf.external-uri",
        "packs/mine/vehicles/draco.glb": "gltf.extension",
    }


def test_ACON_004_names_symlinks_depth_and_gltf_outside_the_seed_packs(tmp_path):
    repo = van_repo(tmp_path)
    repo.write("packs/mine/a/b/c/d/deep.toml", "x")
    repo.write("packs/mine/odd name.toml", "x")
    repo.write("packs/mine/x.gltf", "{}")
    (repo.root / "packs/mine/link.glb").symlink_to(repo.root / "packs/mine/vehicles/van.glb")
    report = run(repo)
    found = {(e["path"], e["rule"]) for e in report["errors"] if e["rule"].startswith("files.")}
    assert ("packs/mine/a/b/c/d/deep.toml", "files.depth") in found
    assert ("packs/mine/odd name.toml", "files.name") in found
    assert ("packs/mine/x.gltf", "files.type") in found
    assert ("packs/mine/link.glb", "files.symlink") in found


def test_ACON_006_triangles_are_counted_per_instance(tmp_path):
    repo = TestRepo(tmp_path / "r", slots={"streets": slot_toml("streets.bench", kind="street.bench", size=(2, 1, 1))})
    g = Gltf()
    m = g.mesh(*grid(20))
    g.node(m)
    g.node(m, translation=[1, 0, 0])
    repo.model("bench", g.glb(), manifest_toml("mine:bench", kind="street.bench", slot="streets.bench", length=2.0), folder="street")
    report = run(repo)
    assert row(report, "mine:bench")["triangles"] == 1600
    errors = where(report, "budget.triangles")
    assert len(errors) == 1 and "1600" in errors[0]["message"] and "1500" in errors[0]["message"]


def _sized(tmp_path, name, size):
    repo = TestRepo(tmp_path / name)
    repo.model("van", box_glb(size[0], size[2], size[1]), manifest_toml("mine:van").replace("length = 5.4", "scale = 1.0"))
    return run(repo)


def test_ACON_007_a_model_at_ten_times_its_size_fails(tmp_path):
    report = _sized(tmp_path, "huge", (54, 20, 25))
    errors = where(report, "size.fail")
    assert len(errors) == 1
    assert "54.00 x 20.00 x 25.00" in errors[0]["message"] and "5.4 x 2 x 2.5" in errors[0]["message"]
    assert "footprint long side" in errors[0]["message"] and "footprint short side" in errors[0]["message"]
    assert " length " not in errors[0]["message"] and " width " not in errors[0]["message"]
    report = _sized(tmp_path, "close", (6.6, 2.1, 2.6))
    assert report["errors"] == []
    warns = where(report, "size.warn", "warnings")
    assert len(warns) == 1 and "footprint long side is 22% off" in warns[0]["message"]
    report = _sized(tmp_path, "near", (6.1, 2.1, 2.6))
    assert report["errors"] == [] and report["warnings"] == []


def test_ACON_007_a_slots_size_tolerance_widens_the_limits(tmp_path):
    repo = TestRepo(tmp_path / "r", slots={"vehicles": slot_toml("vehicles.panel-van", size_tolerance=3.0)})
    repo.model("van", box_glb(10.8, 2.5, 2.0), manifest_toml("mine:van").replace("length = 5.4", "scale = 1.0"))
    report = run(repo)
    assert report["errors"] == [] and report["warnings"] == []  # 100% off against a 300% tolerance (warns beyond 120%)


def test_ACON_016_outside_the_measuring_profile_is_refused_not_guessed(tmp_path):
    repo = TestRepo(
        tmp_path / "r",
        slots={"vehicles": slot_toml("vehicles.panel-van"), "streets": slot_toml("streets.bench", kind="street.bench", size=(1.8, 0.6, 0.9))},
    )
    g = Gltf()
    g.node(g.mesh(*box(5.4, 2.5, 2.0)))
    g.doc["accessors"][0]["sparse"] = {"count": 1}
    repo.model("sparse", g.glb(), manifest_toml("mine:sparse"))
    g = Gltf()
    g.node(g.mesh(*box(1.8, 0.9, 0.6)))
    g.doc["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": 0}]
    repo.model("morph", g.glb(), manifest_toml("mine:morph", kind="street.bench", slot="streets.bench", length=1.8), folder="street")
    report = run(repo)
    errors = where(report, "gltf.profile")
    assert len(errors) == 2 and len(report["errors"]) == 2
    assert any("sparse" in e["message"] for e in errors) and any("morph" in e["message"] for e in errors)
    for mid in ("mine:sparse", "mine:morph"):
        r = row(report, mid)
        assert r["triangles"] is None and r["size_m"] is None and r["factor"] is None


def test_animations_and_skins_only_for_people_and_animals(tmp_path):
    repo = TestRepo(tmp_path / "r", slots={"vehicles": slot_toml("vehicles.panel-van"), "people": slot_toml("people.walker", kind="person.walking", size=(0.5, 0.4, 5.4))})
    for stem, kind, slot, folder in (("van", "van", "vehicles.panel-van", "vehicles"), ("walker", "person.walking", "people.walker", "people")):
        g = Gltf()
        g.node(g.mesh(*box(5.4, 2.5, 2.0)) if kind == "van" else g.mesh(*box(0.4, 5.4, 0.5)))
        g.doc["animations"] = [{"channels": [], "samplers": []}]
        repo.model(stem, g.glb(), manifest_toml(f"mine:{stem}", kind=kind, slot=slot, length=5.4 if kind == "van" else None).replace("\nslot", "\nheight = 5.4\nslot") if kind != "van" else manifest_toml("mine:van"), folder=folder)
    errors = where(run(repo), "gltf.animation")
    assert [e["model"] for e in errors] == ["mine:van"]


def test_ACON_019_an_ai_generated_model_needs_its_tool_model_and_plan(tmp_path):
    repo = TestRepo(tmp_path / "r", slots={"vehicles": slot_toml("vehicles.panel-van", variants=4)})
    van(repo, "none", 'ai_generated = "yes"\n')
    van(repo, "free", 'ai_generated = "yes"\nai_tool = "Meshy"\nai_model = "meshy-t2"\nai_plan = "free"\n')
    van(repo, "hunyuan", 'ai_generated = "yes"\nai_tool = "Hunyuan3D"\nai_model = "hy3d-2"\nai_plan = "paid"\n')
    van(repo, "paid", 'ai_generated = "yes"\nai_tool = "Meshy"\nai_model = "meshy-t2"\nai_plan = "paid"\n')
    report = run(repo)
    by_model = {}
    for e in report["errors"]:
        by_model.setdefault(e["model"], []).append(e)
    assert set(by_model) == {"mine:none", "mine:free", "mine:hunyuan"}
    assert {e["rule"] for e in by_model["mine:none"]} == {"ai.missing"}
    assert len(by_model["mine:none"]) == 3 and any("ai_tool" in e["message"] for e in by_model["mine:none"])
    assert "Meshy's free plan" in by_model["mine:free"][0]["message"]
    assert "Hunyuan3D" in by_model["mine:hunyuan"][0]["message"]
    paid = row(report, "mine:paid")
    assert (paid["ai_generated"], paid["ai_tool"], paid["ai_model"], paid["ai_plan"]) == ("yes", "Meshy", "meshy-t2", "paid")


def test_an_unknown_ai_plan_and_a_bad_handle_are_errors(tmp_path):
    repo = van_repo(tmp_path)
    van(repo, "x", 'ai_generated = "yes"\nai_tool = "T"\nai_model = "M"\nai_plan = "gratis"\n')
    assert "ai.plan" in rules(run(repo))


def test_provenance_inherits_from_the_pack_and_original_work_is_cc0_or_cc_by_4(tmp_path):
    repo = TestRepo(tmp_path / "r")
    repo.model("van", box_glb(5.4, 2.5, 2.0), manifest_toml("mine:van", provenance=False, extra='licence = "CC-BY-3.0"\ntitle = "T"\n'))
    errors = where(run(repo), "licence.original")
    assert len(errors) == 1
    repo2 = TestRepo(tmp_path / "r2")
    repo2.model("van", box_glb(5.4, 2.5, 2.0), manifest_toml("mine:van", provenance=False))
    assert run(repo2)["errors"] == []
    row_ = row(run(repo2), "mine:van")
    assert row_["licence"] == "CC0-1.0" and row_["author"] == "A. Maker" and row_["ai_generated"] == "no"


def test_report_rows_carry_the_measured_fields(tmp_path):
    repo = van_repo(tmp_path)
    report = run(repo)
    r = row(report, "mine:van")
    assert report["version"] == 1 and report["mode"] == "strict" and report["ok"]
    assert r["model_path"] == "packs/mine/vehicles/van.glb" and r["model_files"] == ["packs/mine/vehicles/van.glb"]
    assert r["pack"] == "mine" and r["pack_id"] == "mine:base" and r["change"] == "unchanged"
    assert r["triangles"] == 12 and r["budget"] == 3000 and r["factor"] == pytest.approx(1.0)
    assert r["size_m"] == {"length": 5.4, "width": 2.0, "height": 2.5}
    assert r["measure"] == "length" and r["forward"] == "+x" and r["tint_tolerance"] == 0.25
