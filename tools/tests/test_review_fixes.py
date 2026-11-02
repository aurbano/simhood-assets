"""Fixes from the contributor walkthrough: tree listing, PR-mode waivers, origin, knock-on, wording."""

import shutil
import subprocess

import pytest
from builders import Gltf, TestRepo, box, box_glb, manifest_toml, slot_toml, van_repo
from assets_lib import repo as repomod
from assets_lib import verdict
from helpers import row, rules, run, where
import validate_cli
from test_waivers import three_textures

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.org", *args], check=True, capture_output=True)


# 1. Listing ---------------------------------------------------------------------------------


def test_listing_skips_dot_git_files_and_tool_output_when_walking(tmp_path):
    repo = van_repo(tmp_path)
    repo.write("out/index.json", "{}")
    repo.write("tools/node_modules/x/y.js", "")
    (repo.root / ".git").write_text("gitdir: /elsewhere\n")  # a worktree's .git is a file
    files = repomod.list_files(repo.root)
    assert not any(f == ".git" or f.startswith(("out/", "tools/node_modules/")) for f in files)
    assert "packs/mine/vehicles/van.glb" in files


@needs_git
def test_listing_in_a_git_work_tree_uses_git_and_skips_ignored_files(tmp_path):
    repo = van_repo(tmp_path)
    repo.write(".gitignore", "out/\ntools/out/\nnode_modules/\n")
    subprocess.run(["git", "init", "-q", str(repo.root)], check=True)
    git(repo.root, "add", "-A")
    git(repo.root, "commit", "-q", "-m", "x")
    repo.write("out/index.json", "{}")
    repo.write("tools/out/a.png", "")
    repo.write("tools/node_modules/p/q.js", "")
    repo.write("packs/mine/new.txt", "untracked, not ignored")
    (repo.root / "CODEOWNERS").unlink()  # tracked but deleted in the work tree
    files = repomod.list_files(repo.root)
    assert "packs/mine/new.txt" in files and "packs/mine/vehicles/van.glb" in files
    assert "CODEOWNERS" not in files
    assert not any(f == ".git" or f.startswith((".git/", "out/", "tools/out/", "tools/node_modules/")) for f in files)


@needs_git
def test_a_linked_worktree_base_and_ignored_output_are_not_owner_only_changes(tmp_path):
    base = van_repo(tmp_path, "base")
    base.write(".gitignore", "out/\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(base.root)], check=True)
    git(base.root, "add", "-A")
    git(base.root, "commit", "-q", "-m", "x")
    git(base.root, "worktree", "add", "-q", "-b", "feature", str(tmp_path / "head"))
    head = tmp_path / "head"
    assert (head / ".git").is_file()
    (head / "out").mkdir()
    (head / "out" / "index.json").write_text("{}")
    report = run(head, pr=True, base=base.root, pr_author="stranger")
    assert report["errors"] == [], report["errors"]


# 2. Waivers in PR mode ----------------------------------------------------------------------


def _waiver_pair(tmp_path):
    g = Gltf()
    g.node(g.mesh(*box(5.4, 2.5, 2.0)))
    base = TestRepo(tmp_path / "base", slots={"vehicles": slot_toml("vehicles.panel-van", variants=2)})
    base.model("van", g.glb(), manifest_toml("mine:van"))
    base.model("other", box_glb(5.4, 2.5, 2.0), manifest_toml("mine:other"))
    digest = repomod.sha256(base.root / "packs/mine/vehicles/van.glb")
    base.write("catalogue/waivers.toml", f'[[waiver]]\nmodel = "mine:van"\nfile_sha256 = "{digest}"\nrules = ["gltf.texture"]\nreason = "Early."\n')
    return base, base.copy_to(tmp_path / "head")


def test_waivers_are_checked_only_for_affected_models_in_pr_mode(tmp_path):
    base, head = _waiver_pair(tmp_path)
    head.write("packs/mine/vehicles/other.toml", manifest_toml("mine:other", extra='changes = ["x"]\n'))
    pr = run(head, pr=True, base=base.root, pr_author="owner")
    assert [w for w in pr["warnings"] if w["rule"] == "waiver.unused"] == []
    strict = run(head)
    assert [w["rule"] for w in strict["warnings"]] == ["waiver.unused"]
    # touching the waived model checks its waiver again
    head.write("packs/mine/vehicles/van.toml", manifest_toml("mine:van", extra='changes = ["x"]\n'))
    pr = run(head, pr=True, base=base.root, pr_author="owner")
    assert [w["rule"] for w in pr["warnings"]] == ["waiver.unused"]


# 3. Origin ----------------------------------------------------------------------------------


def test_every_row_says_whether_the_model_is_third_party_or_original(tmp_path):
    repo = TestRepo(tmp_path / "r", slots={"vehicles": slot_toml("vehicles.panel-van", variants=2)})
    repo.model("third", box_glb(5.4, 2.5, 2.0), manifest_toml("mine:third"))
    repo.model("own", box_glb(5.4, 2.5, 2.0), manifest_toml("mine:own", provenance=False))
    report = run(repo)
    assert row(report, "mine:third")["origin"] == "third-party (licence evidence: licences/page.html)"
    assert row(report, "mine:own")["origin"] == "original (no licence_evidence)"


def test_pr_mode_prints_each_affected_models_origin(tmp_path, capsys):
    base = van_repo(tmp_path, "base")
    head = base.copy_to(tmp_path / "head")
    head.write("packs/mine/vehicles/van.toml", manifest_toml("mine:van", provenance=False, extra='changes = ["x"]\n'))
    validate_cli.main(["--repo", str(head.root), "--pr", "--base", str(base.root), "--pr-author", "owner", "--no-gltf-validator"])
    out = capsys.readouterr().out
    assert "MODEL packs/mine/vehicles/van.toml: mine:van (changed) origin: original (no licence_evidence)" in out


def test_the_verdict_table_shows_origin_per_model():
    report = {
        "errors": [], "warnings": [], "ok": True, "slots": [],
        "models": [
            {"id": "a:b", "change": "added", "origin": "third-party (licence evidence: licences/p.html)", "slot": "s.x", "triangles": 1, "budget": 2, "size_m": None},
            {"id": "a:c", "change": "added", "origin": "original (no licence_evidence)", "slot": "s.y", "triangles": 1, "budget": 2, "size_m": None},
        ],
    }
    text = verdict.verdict_section(report, {"ok": True, "description": "ok", "failures": []}, "a" * 40, "https://example.org/run")
    import html

    assert "| Origin |" in text
    text = html.unescape(text)  # the comment writer escapes markup characters as entities
    assert "third-party (licence evidence: licences/p.html)" in text and "original (no licence_evidence)" in text


# 4. Knock-on --------------------------------------------------------------------------------


def _have_slot_nobody_names(tmp_path, name, manifest_slot):
    repo = TestRepo(tmp_path / name, slots={"vehicles": slot_toml("vehicles.panel-van", status="have")})
    repo.model("van", box_glb(5.4, 2.5, 2.0), manifest_toml("mine:van", slot=manifest_slot))
    return run(repo)


def test_a_slot_status_error_that_may_follow_from_a_manifest_error_says_so(tmp_path):
    knock = " (this may follow from an error above)"
    typo = _have_slot_nobody_names(tmp_path, "typo", "vehicles.panel-vann")
    assert "slot.unknown" in rules(typo)
    assert where(typo, "slot.status")[0]["message"].endswith(knock)
    broken = TestRepo(tmp_path / "broken", slots={"vehicles": slot_toml("vehicles.panel-van", status="have")})
    broken.write("packs/mine/vehicles/van.toml", "nonsense")
    broken.write("packs/mine/vehicles/van.glb", box_glb(1, 1, 1))
    report = run(broken)
    assert "manifest.invalid" in rules(report) and where(report, "slot.status")[0]["message"].endswith(knock)
    plain = TestRepo(tmp_path / "plain", slots={"vehicles": slot_toml("vehicles.panel-van", status="have")})
    message = where(run(plain), "slot.status")[0]["message"]
    assert "may follow" not in message and message.endswith("add a model)")


# 5. Wording ---------------------------------------------------------------------------------


def test_size_findings_say_footprint_long_and_short_side(tmp_path):
    repo = TestRepo(tmp_path / "r")
    repo.model("van", box_glb(9.0, 2.5, 3.5), manifest_toml("mine:van").replace("length = 5.4", "scale = 1.0"))  # long side 9 vs 5.4, short side 3.5 vs 2.0
    message = where(run(repo), "size.fail")[0]["message"]
    assert "footprint long side is 67% off" in message and "footprint short side is 75% off" in message
    assert "9.00 x 3.50 x 2.50 m (footprint long side x footprint short side x height)" in message
