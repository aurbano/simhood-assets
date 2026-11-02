"""The command line tools: exit codes, report JSON, output lines; the real Khronos validator."""

import json
import shutil
import struct

import pytest
from builders import Gltf, TestRepo, TOOLS, box, box_glb, manifest_toml, slot_toml, van_repo
from helpers import run
import validate_cli


def test_exit_codes_and_report_file(tmp_path, capsys):
    repo = van_repo(tmp_path)
    report_path = tmp_path / "out" / "report.json"
    assert validate_cli.main(["--repo", str(repo.root), "--no-gltf-validator", "--report", str(report_path)]) == 0
    report = json.loads(report_path.read_text())
    assert report["ok"] is True and report["version"] == 1 and report["errors"] == []
    repo.write("packs/mine/vehicles/van.toml", "nonsense")
    assert validate_cli.main(["--repo", str(repo.root), "--no-gltf-validator", "--report", str(report_path)]) == 1
    out = capsys.readouterr().out
    assert "ERROR packs/mine/vehicles/van.toml:" in out
    assert json.loads(report_path.read_text())["ok"] is False


def test_usage_problems_exit_2(tmp_path):
    assert validate_cli.main(["--repo", str(tmp_path / "missing")]) == 2
    assert validate_cli.main(["--pr", "--repo", str(tmp_path)]) == 2
    assert validate_cli.main(["--bogus"]) == 2
    assert validate_cli.main(["--base", str(tmp_path)]) == 2


def test_warnings_do_not_fail_and_print_warn_lines(tmp_path, capsys):
    repo = TestRepo(tmp_path / "r")
    repo.model("van", box_glb(6.6, 2.5, 2.0), manifest_toml("mine:van"))
    repo.write("packs/mine/vehicles/van.toml", manifest_toml("mine:van", length=6.6))
    assert validate_cli.main(["--repo", str(repo.root), "--no-gltf-validator"]) == 0
    assert "WARN packs/mine/vehicles/van.glb:" in capsys.readouterr().out


def test_pr_mode_through_the_cli(tmp_path):
    base = van_repo(tmp_path, "base")
    head = base.copy_to(tmp_path / "head")
    head.write("README.md", "x")
    args = ["--repo", str(head.root), "--pr", "--base", str(base.root), "--no-gltf-validator"]
    assert validate_cli.main(args + ["--pr-author", "stranger"]) == 1
    assert validate_cli.main(args + ["--pr-author", "owner"]) == 0


needs_node = pytest.mark.skipif(
    shutil.which("node") is None or not (TOOLS / "node_modules" / "gltf-validator").is_dir(),
    reason="needs node and the installed gltf-validator",
)


@needs_node
def test_the_khronos_validator_runs_first_and_its_errors_become_report_errors(tmp_path):
    repo = van_repo(tmp_path)
    ok = run(repo, gltf_validator=True)
    assert ok["errors"] == [], ok["errors"]
    g = Gltf()
    g.node(g.mesh(*box(5.4, 2.5, 2.0)))
    g.doc["accessors"][0]["max"] = [50, 50, 50]  # declared bounds that the data does not match
    repo.write("packs/mine/vehicles/van.glb", g.glb())
    report = run(repo, gltf_validator=True)
    errors = [e for e in report["errors"] if e["rule"] == "gltf.validator"]
    assert errors and "ACCESSOR_MAX_MISMATCH" in errors[0]["message"]
    # the measured size still comes from the decoded positions, never from the declared bounds
    assert report["models"][0]["size_m"] == {"length": 5.4, "width": 2.0, "height": 2.5}
    path = tmp_path / "junk.glb"
    path.write_bytes(b"not a glb at all")
    from assets_lib import khronos

    assert khronos.run([path])[path]["fatal"]
