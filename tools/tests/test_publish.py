"""The preview publisher: what it binds and what it accepts from a render artifact (ACON-018)."""

import json
import subprocess

import publish as publish_cli
import pytest
from assets_lib import comment, pictures, previews, publisher
from fake_github import FakeGitHub
from pngs import index, make_zip, png

HEAD = "a1b2c3d4" + "0" * 32
RENDER = ".github/workflows/render.yml"
CHECK = ".github/workflows/check.yml"
REPO = "aurbano/simhood-assets"


def git(remote, *args):
    return subprocess.run(["git", "--git-dir", str(remote), *args], capture_output=True, text=True, check=True).stdout.strip()


def tree(remote):
    return set(git(remote, "ls-tree", "-r", "--name-only", "previews").splitlines())


def verdict_zip(models=("mine:van",), pr=5, head=HEAD):
    report = {"version": 1, "mode": "pr", "ok": True, "errors": [], "warnings": [], "waived": [], "slots": [], "examples": [],
              "models": [{"id": m, "change": "added"} for m in models] + [{"id": "gone:old", "change": "deleted"}]}
    return make_zip({"report.json": json.dumps(report).encode(), "meta.json": json.dumps({"pr": pr, "head_sha": head}).encode()})


def good_index(**extra):
    return index({"mine:van": {"image": "mine~mine__van.png", "tinted": "mine~mine__van.tinted.png", "before": None, "triangles": 1200,
                               "tint_coverage": 0.4, "tint_coverage_review": 0.1,
                               "colours": [{"hex": "#d9d9d9", "share": 0.41, "source": "material"}], "error": None}, **extra})


def good_files(**extra):
    return {"index.json": good_index(), "meta.json": b'{"pr": 99, "head_sha": "ffff"}',
            "mine~mine__van.png": png(), "mine~mine__van.tinted.png": png(), **extra}


@pytest.fixture
def remote(tmp_path):
    path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True)
    return path


def world(files, *, with_verdict=True, conclusion="success", pictures_artifact=True):
    gh = FakeGitHub(REPO)
    gh.add_pull(5, HEAD)
    gh.add_run(100, event="pull_request", path=RENDER, head_sha=HEAD, conclusion=conclusion)
    if pictures_artifact:
        gh.add_artifact(1, "pictures", 100, make_zip(files) if isinstance(files, dict) else files)
    if with_verdict:
        gh.add_run(200, event="pull_request_target", path=CHECK, head_sha="b" * 40, head_repo=REPO, owner="aurbano")
        gh.add_artifact(2, "verdict", 200, verdict_zip())
    return gh


def publish(gh, remote, **kw):
    return publisher.publish_pictures(gh, REPO, 100, remote=str(remote), token=None, log=lambda m: None, **kw)


# -- the happy path


def test_ACON_009_a_valid_render_is_published_under_names_the_publisher_chose(remote):
    gh = world(good_files())
    r = publish(gh, remote)
    assert r.pr == 5 and r.published == 2 and r.refused == 0
    assert tree(remote) == {"README.md", "pr-5/a1b2c3d/1.png", "pr-5/a1b2c3d/2.png"}
    url = f"https://raw.githubusercontent.com/{REPO}/{r.commit}/pr-5/a1b2c3d/1.png"
    assert url in r.section and "tint key covers 40%" in r.section and "#d9d9d9" in r.section
    assert git(remote, "rev-parse", "previews") == r.commit


def test_the_cli_writes_the_section_and_the_workflow_outputs(remote, tmp_path, monkeypatch):
    gh = world(good_files())
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    code = publish_cli.main(["pictures", "--repo", REPO, "--run-id", "100", "--remote", str(remote), "--section-out", str(tmp_path / "s" / "p.md")], gh=gh)
    assert code == 0
    assert "pr=5" in out.read_text() and "Pictures for commit" in (tmp_path / "s" / "p.md").read_text()
    code = publish_cli.main(["comment", "--repo", REPO, "--pr", "5", "--section", "pictures", "--file", str(tmp_path / "s" / "p.md")], gh=gh)
    assert code == 0 and "Pictures for commit" in comment.get_section(gh.comments[0]["body"], "pictures")


# -- ACON-018: forged artifacts


def test_ACON_018_a_forged_artifact_publishes_nothing_forged_and_the_comment_says_what_was_refused(remote):
    files = {
        "index.json": index({
            "mine:van": {"image": "mine~mine__van.png", "tinted": "fake.png", "before": "../evil.png"},
            "evil:model": {"image": "other.png"},
        }),
        "mine~mine__van.png": png(),
        "fake.png": b"this is not a png at all, only named like one" * 3,
        "../evil.png": png(),
        "sub/dir.png": png(),
        "big.png": png(2048, 2048),
        "other.png": png(),
        "script.sh": b"#!/bin/sh\nexit 1\n",
    }
    gh = world(make_zip(files))
    r = publish(gh, remote)
    assert tree(remote) == {"README.md", "pr-5/a1b2c3d/1.png"}  # only the valid van picture
    assert r.published == 1
    for what in ("fake.png", "evil.png", "sub", "big.png", "evil:model", "script.sh", "other.png"):
        assert what in r.section.replace("&#58;", ":"), what
    assert "refused" in r.section
    assert "../evil.png" not in tree(remote)


def test_ACON_018_nothing_is_published_when_every_input_is_forged(remote):
    files = {"index.json": index({"evil:model": {"image": "a.png"}, "mine:van": {"image": "b.png"}}),
             "a.png": png(), "b.png": b"GIF89a"}
    gh = world(make_zip(files))
    r = publish(gh, remote)
    assert r.published == 0 and tree(remote) == {"README.md"}
    assert "No pictures were published" in r.section and "b.png" in r.section and "evil" in r.section


def test_ACON_018_too_many_entries_refuse_the_whole_artifact(remote):
    files = {f"p{i}.png": png() for i in range(64)} | {"index.json": index({})}
    r = publish(world(make_zip(files)), remote)
    assert r.published == 0 and "65 entries" in r.section
    assert git(remote, "branch", "--list", "previews") == ""


def test_ACON_018_the_limits_one_by_one():
    ok = png()
    assert pictures.png_problem(ok) is None
    assert pictures.png_problem(b"\x89PNG\r\n\x1a\n" + b"\0" * 5) is not None
    assert "signature" in pictures.png_problem(b"x" * 100)
    assert pictures.png_problem(png(1025, 8)) and pictures.png_problem(png(8, 1025))
    assert pictures.png_problem(png(1024, 1024)) is None
    assert pictures.png_problem(png(pad=pictures.MAX_PNG)) is not None  # over 2 MB
    assert pictures.png_problem(png(0, 8)) is not None


def test_ACON_018_entry_names_and_kinds():
    files = {"a.png": png(), "../b.png": png(), "/c.png": png(), "d/e.png": png(), "f\\g.png": png(), ".h.png": png(), "i.exe": b"MZ",
             "link.png": png(), "dir/": b"", "index.json": index({})}
    u = pictures.unpack(make_zip(files, symlinks=("link.png",)))
    assert set(u.pngs) == {"a.png"}
    assert {n for n, _ in u.refused} == {"../b.png", "/c.png", "d/e.png", "f\\g.png", ".h.png", "i.exe", "link.png", "dir/"}
    assert dict(u.refused)["link.png"] == "is a symbolic link"


def test_ACON_018_the_expanded_size_is_bounded():
    files = {f"p{i}.png": png(pad=pictures.MAX_PNG - 200) for i in range(21)}
    u = pictures.unpack(make_zip(files))
    assert u.fatal and "MB" in u.fatal and not u.pngs
    assert pictures.unpack(b"not a zip").fatal


def test_a_model_outside_the_verdicts_list_is_refused_even_with_a_valid_picture():
    u = pictures.unpack(make_zip({"index.json": index({"other:model": {"image": "a.png"}}), "a.png": png()}))
    sel = pictures.select(u, ["mine:van"])
    assert not sel.pictures and any("other:model" == n for n, _ in sel.refused)


# -- binding to the run, the pull request and the verdict


def test_a_run_that_is_not_a_pull_request_render_is_ignored(remote):
    gh = world(good_files())
    gh.runs[100]["event"] = "push"
    assert publish(gh, remote).pr is None
    gh = world(good_files())
    gh.runs[100]["path"] = ".github/workflows/evil.yml"
    assert publish(gh, remote).pr is None
    assert git(remote, "branch", "--list", "previews") == ""


def test_the_pull_request_comes_from_the_api_never_from_the_artifact(remote):
    gh = world(good_files(**{"meta.json": b'{"pr": 99, "head_sha": "x"}'}))
    gh.add_pull(99, "9" * 40, owner="someone-else", branch="other")
    r = publish(gh, remote)
    assert r.pr == 5 and "pr-99" not in "".join(tree(remote))


def test_a_pull_request_whose_head_moved_gets_nothing(remote):
    gh = world(good_files())
    gh.pulls[5]["head"]["sha"] = "f" * 40
    assert publish(gh, remote).pr is None


def test_the_head_repository_must_match_too(remote):
    gh = world(good_files())
    gh.pulls[5]["head"]["repo"]["full_name"] = "attacker/simhood-assets"
    assert publish(gh, remote).pr is None


def test_a_pull_request_closed_or_moved_during_the_publish_is_not_published(remote, monkeypatch):
    gh = world(good_files())
    real = pictures.select

    def select_then_close(*a, **k):
        gh.pulls[5]["state"] = "closed"
        return real(*a, **k)

    monkeypatch.setattr(pictures, "select", select_then_close)
    assert publish(gh, remote).pr is None
    assert git(remote, "branch", "--list", "previews") == ""


def test_a_verdict_artifact_from_a_pull_request_run_is_not_trusted(remote):
    gh = world(good_files(), with_verdict=False)
    gh.add_run(201, event="pull_request", path=CHECK, head_sha=HEAD, head_repo="contributor/simhood-assets", owner="contributor")
    gh.add_artifact(3, "verdict", 201, verdict_zip(models=("mine:van",)))
    r = publish(gh, remote)
    assert r.published == 0 and "verdict" in r.section
    assert git(remote, "branch", "--list", "previews") == ""


def test_a_verdict_for_another_head_or_pull_request_is_not_used(remote):
    gh = world(good_files(), with_verdict=False)
    gh.add_run(200, event="pull_request_target", path=CHECK, head_sha="b" * 40, head_repo=REPO, owner="aurbano")
    gh.add_artifact(2, "verdict", 200, verdict_zip(head="e" * 40))
    gh.add_artifact(4, "verdict", 200, verdict_zip(pr=6))
    assert publish(gh, remote).published == 0


def test_the_publisher_waits_for_the_verdict_to_arrive(remote):
    gh = world(good_files(), with_verdict=False)
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        gh.add_run(200, event="pull_request_target", path=CHECK, head_sha="b" * 40, head_repo=REPO, owner="aurbano")
        gh.add_artifact(2, "verdict", 200, verdict_zip())

    r = publish(gh, remote, wait_verdict=60, sleep=sleep)
    assert slept == [10] and r.published == 2


def test_a_failed_cancelled_or_missing_render_is_written_into_the_section(remote):
    r = publish(world(good_files(), conclusion="failure"), remote)
    assert r.pr == 5 and "failure" in r.section and r.published == 0
    r = publish(world(good_files(), conclusion="cancelled"), remote)
    assert "cancelled" in r.section
    r = publish(world(None, pictures_artifact=False), remote)
    assert "no `pictures` artifact" in r.section.replace("&#96;", "`")
    assert git(remote, "branch", "--list", "previews") == ""


def test_text_from_the_index_is_escaped_in_the_section(remote):
    files = {"index.json": index({"mine:van": {"image": "a.png", "error": "<script>alert(1)</script> [x](http://evil) @owner", "triangles": "many",
                                               "colours": [{"hex": "<b>", "share": 1}]}}),
             "a.png": png()}
    r = publish(world(make_zip(files)), remote)
    assert "<script>" not in r.section and "http://evil" not in r.section and "@owner" not in r.section and "<b>" not in r.section


def test_pictures_of_several_pull_requests_live_side_by_side_until_one_closes(remote):
    gh = world(good_files())
    publish(gh, remote)
    gh.add_pull(6, "c" * 40, owner="other", branch="b6")
    previews.rebuild(str(remote), lambda: {5, 6}, replace=(6, {"ccccccc/1.png": png()}))
    assert tree(remote) == {"README.md", "pr-5/a1b2c3d/1.png", "pr-5/a1b2c3d/2.png", "pr-6/ccccccc/1.png"}
    gh.pulls[5]["state"] = "closed"
    publisher.cleanup(gh, REPO, 5, remote=str(remote), token=None)
    assert tree(remote) == {"README.md", "pr-6/ccccccc/1.png"}
    assert git(remote, "rev-list", "--count", "previews") == "1"


def test_the_final_statuses_go_to_the_head_commit():
    gh = FakeGitHub(REPO)
    report = {"ok": True, "errors": [], "warnings": [], "models": [{}]}
    publisher.finish_statuses(gh, REPO, HEAD, report, {"ok": False, "description": "bbbbbbb not signed off: run git commit --amend -s"}, "https://run")
    by_ctx = {s["context"]: s for s in gh.statuses}
    assert by_ctx["assets/validate"]["state"] == "success" and by_ctx["assets/dco"]["state"] == "failure"
    assert all(s["sha"] == HEAD and s["target_url"] == "https://run" for s in gh.statuses)
    publisher.finish_statuses(gh, REPO, HEAD, None, None, "https://run")
    assert {s["state"] for s in gh.statuses[2:]} == {"error"}
