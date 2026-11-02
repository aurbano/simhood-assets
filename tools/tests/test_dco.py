"""The sign-off check (ACON-008)."""

import json

import dco as dco_cli
from assets_lib import dco
from fake_github import FakeGitHub


def commit(sha, message, email="dev@example.com", parents=1):
    return {"sha": sha, "parents": [{"sha": f"p{i}"} for i in range(parents)],
            "commit": {"message": message, "author": {"name": "Dev", "email": email}}}


SIGNED = "Add a van\n\nSigned-off-by: Dev <dev@example.com>"


def test_ACON_008_an_unsigned_second_commit_fails_naming_it_and_the_fix():
    result = dco.check_commits([commit("a" * 40, SIGNED), commit("b" * 40, "Set the tint key")])
    assert not result["ok"]
    assert [f["sha"] for f in result["failures"]] == ["b" * 40]
    assert "bbbbbbb" in result["description"] and "git commit --amend -s" in result["description"]
    assert "bbbbbbb" in result["lines"][0] and "git commit --amend -s" in result["lines"][0]
    assert "aaaaaaa" not in result["lines"][0]


def test_ACON_008_a_merge_commit_is_exempt():
    result = dco.check_commits([commit("a" * 40, SIGNED), commit("m" * 40, "Merge branch main", parents=2)])
    assert result["ok"] and result["checked"] == 1 and result["commits"] == 2


def test_ACON_008_the_email_matches_ignoring_case():
    ok = dco.check_commits([commit("a" * 40, "x\n\nSigned-off-by: Dev <DEV@Example.COM>", email="dev@example.com")])
    assert ok["ok"]
    lower_key = dco.check_commits([commit("a" * 40, "x\n\nsigned-off-by: Dev <dev@example.com>")])
    assert lower_key["ok"]


def test_ACON_008_a_sign_off_for_someone_else_fails():
    result = dco.check_commits([commit("a" * 40, "x\n\nSigned-off-by: Other <other@example.com>")])
    assert not result["ok"] and "<other@example.com>" in result["failures"][0]["reason"]


def test_ACON_008_more_than_250_commits_are_refused():
    gh = FakeGitHub()
    gh.add_pull(7, "c" * 40, commits=251)
    result = dco.check_pull_request(gh, gh.repo, 7)
    assert not result["ok"] and result["refused"] and "250" in result["description"]
    assert not any("/commits" in p for _, p in gh.requests)


def test_ACON_008_the_cli_reads_the_commits_and_writes_the_result(tmp_path, capsys):
    gh = FakeGitHub()
    gh.add_pull(7, "c" * 40, commits=2)
    gh.pull_commits[7] = [commit("a" * 40, SIGNED), commit("b" * 40, "no sign-off")]
    out = tmp_path / "out" / "dco.json"
    code = dco_cli.main(["--repo", gh.repo, "--pr", "7", "--out", str(out)], gh=gh)
    assert code == 1
    assert json.loads(out.read_text())["failures"][0]["sha"] == "b" * 40
    assert "git commit --amend -s" in capsys.readouterr().out
    gh.pull_commits[7] = [commit("a" * 40, SIGNED)]
    assert dco_cli.main(["--repo", gh.repo, "--pr", "7"], gh=gh) == 0
