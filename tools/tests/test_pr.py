"""Pull request mode: owner-only scope (ACON-005), append-only credit (ACON-015), affected models."""

import shutil

from builders import TestRepo, box_glb, manifest_toml, slot_toml, van_repo
from helpers import row, rules, run, where


def pr(tmp_path, base: TestRepo, head: TestRepo, author="stranger"):
    return run(head, pr=True, base=base.root, pr_author=author)


def pair(tmp_path, slots=None, with_van=True):
    base = TestRepo(tmp_path / "base", slots=slots or {"vehicles": slot_toml("vehicles.panel-van", status="missing")})
    if with_van:
        base.model("van", box_glb(5.4, 2.5, 2.0), manifest_toml("mine:van", slot="vehicles.panel-van"))
    return base, base.copy_to(tmp_path / "head")


def test_ACON_005_a_contributor_may_set_only_their_own_slots_status(tmp_path):
    base = TestRepo(tmp_path / "base", slots={"vehicles": slot_toml("vehicles.panel-van", status="missing")})
    head = base.copy_to(tmp_path / "head")
    head.model("van", box_glb(5.4, 2.5, 2.0), manifest_toml("mine:van"))
    head.write("catalogue/vehicles.toml", slot_toml("vehicles.panel-van", status="have", size=(6.0, 2.0, 2.5)))
    report = pr(tmp_path, base, head)
    owner_only = where(report, "pr.owner-only")
    assert len(report["errors"]) == 1 and len(owner_only) == 1
    assert "size" in owner_only[0]["message"] and "owner-only" in owner_only[0]["message"]
    assert not where(report, "pr.status")
    # without the size change the status change alone is accepted
    head.write("catalogue/vehicles.toml", slot_toml("vehicles.panel-van", status="have"))
    assert pr(tmp_path, base, head)["errors"] == []
    # the owner may change anything
    head.write("catalogue/vehicles.toml", slot_toml("vehicles.panel-van", status="have", size=(5.6, 2.0, 2.5)))
    assert pr(tmp_path, base, head, author="Owner")["errors"] == []


def test_ACON_005_other_owner_only_paths_and_status_transitions_fail(tmp_path):
    base, head = pair(tmp_path, slots={"vehicles": slot_toml("vehicles.panel-van", status="have"), "streets": slot_toml("streets.bench", kind="street.bench", status="missing")})
    head.write("tools/x.py", "print()")
    head.write("README.md", "changed")
    head.write("catalogue/waivers.toml", "")
    head.write("catalogue/streets.toml", slot_toml("streets.bench", kind="street.bench", status="have"))  # no model names it
    head.write("catalogue/vehicles.toml", slot_toml("vehicles.panel-van", status="stand-in"))  # have -> stand-in
    report = pr(tmp_path, base, head)
    paths = {(e["path"], e["rule"]) for e in report["errors"]}
    assert ("tools/x.py", "pr.owner-only") in paths and ("README.md", "pr.owner-only") in paths
    assert ("catalogue/waivers.toml", "pr.owner-only") in paths
    assert ("catalogue/vehicles.toml", "pr.status") in paths and ("catalogue/streets.toml", "pr.status") in paths


def test_ACON_005_an_owner_only_comment_edit_in_the_catalogue_fails(tmp_path):
    base, head = pair(tmp_path, slots={"vehicles": slot_toml("vehicles.panel-van", status="have")})
    head.write("catalogue/vehicles.toml", "# a comment\n" + slot_toml("vehicles.panel-van", status="have"))
    assert rules(pr(tmp_path, base, head)) == ["pr.owner-only"]


def test_ACON_015_an_edit_keeps_earlier_credit(tmp_path):
    base, head = pair(tmp_path, slots={"vehicles": slot_toml("vehicles.panel-van", status="have")})
    base.write("packs/mine/vehicles/van.toml", manifest_toml("mine:van", extra='contributors = ["a"]\nchanges = ["scaled"]\n'))
    head = base.copy_to(tmp_path / "head2")
    head.write("packs/mine/vehicles/van.toml", manifest_toml("mine:van", extra='contributors = ["b"]\nchanges = ["scaled"]\n'))
    report = pr(tmp_path, base, head)
    errors = where(report, "append-only.contributors")
    assert len(errors) == 1 and "`a`" in errors[0]["message"] and len(report["errors"]) == 1
    head.write("packs/mine/vehicles/van.toml", manifest_toml("mine:van", extra='contributors = ["a", "b"]\nchanges = ["scaled", "recoloured body"]\n'))
    assert pr(tmp_path, base, head)["errors"] == []
    head.write("packs/mine/vehicles/van.toml", manifest_toml("mine:van", extra='contributors = ["a"]\nchanges = ["recoloured body"]\n'))
    assert rules(pr(tmp_path, base, head)) == ["append-only.changes"]
    head.write("packs/mine/vehicles/van.toml", manifest_toml("mine:van", extra='contributors = ["a"]\nchanges = ["scaled"]\nauthor = "Somebody Else"\n'))
    assert rules(pr(tmp_path, base, head)) == ["append-only.author"]


def test_the_report_lists_added_changed_deleted_and_renamed_models(tmp_path):
    base, _ = pair(tmp_path, slots={"vehicles": slot_toml("vehicles.panel-van", status="have", variants=3)})
    base.model("old", box_glb(5.4, 2.4, 2.0), manifest_toml("mine:old"))
    base.model("gone", box_glb(5.4, 2.6, 2.0), manifest_toml("mine:gone"))
    base.model("same", box_glb(5.4, 2.7, 2.0), manifest_toml("mine:same"))
    head = base.copy_to(tmp_path / "head2")
    (head.root / "packs/mine/vehicles/gone.glb").unlink()
    (head.root / "packs/mine/vehicles/gone.toml").unlink()
    shutil.move(head.root / "packs/mine/vehicles/old.glb", head.root / "packs/mine/vehicles/renamed.glb")
    (head.root / "packs/mine/vehicles/old.toml").unlink()
    head.write("packs/mine/vehicles/renamed.toml", manifest_toml("mine:renamed"))
    head.model("fresh", box_glb(5.4, 2.8, 2.0), manifest_toml("mine:fresh"))
    head.write("packs/mine/vehicles/van.toml", manifest_toml("mine:van", extra='changes = ["x"]\n'))
    report = pr(tmp_path, base, head)
    changes = {m["id"]: m["change"] for m in report["models"]}
    assert changes == {"mine:fresh": "added", "mine:gone": "deleted", "mine:renamed": "renamed", "mine:van": "changed"}
    assert row(report, "mine:renamed")["renamed_from"] == "packs/mine/vehicles/old.toml"
    assert report["mode"] == "pr"


def test_a_changed_pack_toml_affects_every_model_in_the_pack(tmp_path):
    base, head = pair(tmp_path, slots={"vehicles": slot_toml("vehicles.panel-van", status="have")})
    head.write("packs/mine/pack.toml", head.root.joinpath("packs/mine/pack.toml").read_text() + "# touched\n")
    report = pr(tmp_path, base, head)
    assert [(m["id"], m["change"]) for m in report["models"]] == [("mine:van", "changed")]


def test_a_pr_that_touches_nothing_reports_no_models(tmp_path):
    base, head = pair(tmp_path, slots={"vehicles": slot_toml("vehicles.panel-van", status="have")})
    report = pr(tmp_path, base, head)
    assert report["errors"] == [] and report["models"] == []
    assert report["slots"][0]["models"] == ["mine:van"]


def test_deleting_a_slots_last_model_without_resetting_it_fails(tmp_path):
    base, head = pair(tmp_path, slots={"vehicles": slot_toml("vehicles.panel-van", status="have")})
    (head.root / "packs/mine/vehicles/van.glb").unlink()
    (head.root / "packs/mine/vehicles/van.toml").unlink()
    assert "slot.status" in rules(pr(tmp_path, base, head, author="owner"))


def test_a_pr_adding_over_20_mib_fails(tmp_path):
    base, head = pair(tmp_path, slots={"vehicles": slot_toml("vehicles.panel-van", status="have")})
    for i in range(6):
        head.write(f"packs/mine/licences/big{i}.pdf", b"\0" * (4 * 1024 * 1024))
    assert "files.pr-size" in rules(pr(tmp_path, base, head))
