"""Owner-only waivers: a waiver excuses findings only while the model's file hash matches."""

from builders import Gltf, TestRepo, box, manifest_toml, png, slot_toml
from assets_lib import repo as repomod
from helpers import row, rules, run, where


def three_textures(extra_bytes: int = 0) -> bytes:
    g = Gltf()
    g.node(g.mesh(*box(5.4, 2.5, 2.0)))
    for _ in range(3):
        g.image(png(8, 8, full=True))
    return g.glb(pad_to=extra_bytes)


def waived_repo(tmp_path, glb: bytes, waiver_hash: str | None = None, model="mine:van"):
    repo = TestRepo(tmp_path / "r")
    repo.model("van", glb, manifest_toml("mine:van"))
    digest = waiver_hash or repomod.sha256(repo.root / "packs/mine/vehicles/van.glb")
    repo.write(
        "catalogue/waivers.toml",
        f'[[waiver]]\nmodel = "{model}"\nfile_sha256 = "{digest}"\nrules = ["gltf.texture"]\nreason = "Imported early."\n',
    )
    return repo


def test_a_waiver_moves_matching_findings_to_waived(tmp_path):
    bare = TestRepo(tmp_path / "bare")
    bare.model("van", three_textures(), manifest_toml("mine:van"))
    assert rules(run(bare)) == ["gltf.texture"]
    report = run(waived_repo(tmp_path, three_textures()))
    assert report["errors"] == [] and report["ok"]
    assert [w["rule"] for w in report["waived"]] == ["gltf.texture"]
    assert report["waived"][0]["reason"] == "Imported early." and report["waived"][0]["model"] == "mine:van"
    assert row(report, "mine:van")["textures"] == 3


def test_a_changed_file_revives_the_error_and_the_waiver_goes_stale(tmp_path):
    repo = waived_repo(tmp_path, three_textures())
    repo.write("packs/mine/vehicles/van.glb", three_textures(extra_bytes=4096))
    report = run(repo)
    assert sorted(rules(report)) == ["gltf.texture", "waiver.stale"]
    assert report["waived"] == []
    assert "changed" in where(report, "waiver.stale")[0]["message"]


def test_a_waiver_for_a_model_that_is_gone_is_an_error(tmp_path):
    repo = waived_repo(tmp_path, three_textures(), model="mine:ghost")
    report = run(repo)
    stale = where(report, "waiver.stale")
    assert len(stale) == 1 and "mine:ghost" in stale[0]["message"] and stale[0]["path"] == "catalogue/waivers.toml"
    assert "gltf.texture" in rules(report)


def test_a_waiver_for_a_rule_the_model_no_longer_breaks_warns(tmp_path):
    g = Gltf()
    g.node(g.mesh(*box(5.4, 2.5, 2.0)))
    report = run(waived_repo(tmp_path, g.glb()))
    assert report["errors"] == []
    assert [w["rule"] for w in report["warnings"]] == ["waiver.unused"]


def test_a_broken_waivers_file_is_an_error(tmp_path):
    repo = waived_repo(tmp_path, three_textures())
    repo.write("catalogue/waivers.toml", '[[waiver]]\nmodel = "mine:van"\nrules = []\n')
    assert "waiver.schema" in rules(run(repo))
