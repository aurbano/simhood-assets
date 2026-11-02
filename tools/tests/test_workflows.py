"""The workflows' scripts glued together, and rules every workflow file must keep."""

import json
import re
from pathlib import Path

import pack_pictures
import publish as publish_cli
from assets_lib import catalogue, comment, verdict
from fake_github import FakeGitHub
from pngs import png

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
HEAD = "a1b2c3d4" + "0" * 32


def test_every_third_party_action_is_pinned_to_a_full_commit_sha_with_its_version():
    assert {w.name for w in WORKFLOWS} == {"check.yml", "render.yml", "preview.yml", "cleanup.yml", "main.yml"}
    for w in WORKFLOWS:
        for line in w.read_text().splitlines():
            if m := re.search(r"\buses:\s*(\S+)", line):
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", m.group(1)), f"{w.name}: {line.strip()}"
                assert re.search(r"# v\d+\.\d+\.\d+$", line), f"{w.name}: no version comment: {line.strip()}"


def test_the_trusted_workflows_never_run_anything_from_the_pull_request():
    for name in ("check.yml", "preview.yml", "cleanup.yml"):
        text = (REPO_ROOT / ".github" / "workflows" / name).read_text()
        runs = re.findall(r"^\s+run:.*(?:\n\s{10,}.*)*", text, flags=re.M)
        for run in runs:
            for line in run.splitlines():
                # `pr/` appears only as data given to the base tools
                assert not re.search(r"(^|\s)(uv run|python3?|node|pnpm|bash|sh|\./)\s*(\./)?pr/", line), f"{name}: {line}"
                assert "--dir pr" not in line and "-C pr" not in line, f"{name}: {line}"
    check = (REPO_ROOT / ".github" / "workflows" / "check.yml").read_text()
    assert "persist-credentials: false" in check and "path: pr" in check
    assert re.search(r"permissions:\n  contents: read\n  pull-requests: write\n  statuses: write\n", check)


def test_pack_pictures_lays_the_renderers_output_out_flat(tmp_path):
    renders = tmp_path / "renders"
    (renders / "mine").mkdir(parents=True)
    for name in ("mine__van.png", "mine__van.tinted.png", "mine__van.before.png"):
        (renders / "mine" / name).write_bytes(png())
    entry = {"image": "mine/mine__van.png", "tinted": "mine/mine__van.tinted.png", "before": "mine/mine__van.before.png", "triangles": 3}
    (renders / "index.json").write_text(json.dumps({"version": 1, "models": {"mine:van": entry}, "examples": {}}))
    dest = tmp_path / "upload"
    assert pack_pictures.main(["--renders", str(renders), "--dest", str(dest), "--pr", "5", "--sha", HEAD]) == 0
    assert sorted(p.name for p in dest.iterdir()) == ["index.json", "meta.json", "mine~mine__van.before.png", "mine~mine__van.png", "mine~mine__van.tinted.png"]
    assert json.loads((dest / "index.json").read_text())["models"]["mine:van"]["image"] == "mine~mine__van.png"
    # no renders at all still uploads an (empty) index
    assert pack_pictures.main(["--renders", str(tmp_path / "none"), "--dest", str(tmp_path / "u2"), "--pr", "5", "--sha", HEAD]) == 0
    assert json.loads((tmp_path / "u2" / "index.json").read_text())["models"] == {}


def test_the_verdict_command_writes_the_comment_from_the_check_output(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    report = {"version": 1, "mode": "pr", "ok": True, "errors": [], "warnings": [], "waived": [], "examples": [],
              "models": [{"id": "mine:van", "slot": "vehicles.panel-van", "change": "added", "triangles": 10, "budget": 3000,
                          "size_m": {"length": 5.4, "width": 2.0, "height": 2.5}, "licence": "CC0-1.0", "ai_generated": "no"}],
              "slots": [{"id": "vehicles.panel-van", "status": "missing", "models": ["mine:van"], "wanted_more": 0}]}
    (out / "report.json").write_text(json.dumps(report))
    (out / "dco.json").write_text(json.dumps({"ok": True, "description": "1 commit signed off", "failures": [], "lines": []}))
    gh = FakeGitHub()
    code = publish_cli.main(["verdict", "--repo", gh.repo, "--pr", "5", "--head-sha", HEAD, "--dir", str(out), "--url", "https://run", "--base", str(REPO_ROOT)], gh=gh)
    assert code == 0
    section = comment.get_section(gh.comments[0]["body"], "verdict")
    assert "`a1b2c3d`" in section and "mine:van" in section
    assert "catalogue/vehicles.toml" in section  # the real catalogue says which file lists the slot
    code = publish_cli.main(["statuses", "--repo", gh.repo, "--sha", HEAD, "--dir", str(out), "--url", "https://run"], gh=gh)
    assert code == 0 and {s["context"]: s["state"] for s in gh.statuses} == {"assets/validate": "success", "assets/dco": "success"}


def test_a_missing_check_output_still_produces_a_comment_and_error_statuses(tmp_path):
    gh = FakeGitHub()
    assert publish_cli.main(["verdict", "--repo", gh.repo, "--pr", "5", "--head-sha", HEAD, "--dir", str(tmp_path), "--url", "https://run"], gh=gh) == 0
    assert "Did not finish" in comment.get_section(gh.comments[0]["body"], "verdict")
    assert publish_cli.main(["statuses", "--repo", gh.repo, "--sha", HEAD, "--dir", str(tmp_path), "--url", "https://run"], gh=gh) == 0
    assert {s["state"] for s in gh.statuses} == {"error"}


def test_the_verdict_reads_slot_sizes_from_the_catalogue():
    slots = catalogue.load(REPO_ROOT).slots
    sid = "vehicles.panel-van"
    assert sid in slots
    report = {"ok": True, "errors": [], "warnings": [], "slots": [{"id": sid, "status": "missing"}],
              "models": [{"id": "x:y", "slot": sid, "change": "added", "triangles": 1, "budget": 2, "size_m": {"length": 1, "width": 1, "height": 1}}]}
    text = verdict.verdict_section(report, None, HEAD, "https://run", slots)
    size = slots[sid].size
    assert f"({size['length']:g} × {size['width']:g} × {size['height']:g})" in text
