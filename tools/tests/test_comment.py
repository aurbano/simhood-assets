"""The pull request comment: marked sections, the author check, escaping, the verdict text."""

import pytest
from assets_lib import comment, verdict
from fake_github import FakeGitHub

BOT = comment.BOT_LOGIN


def test_replacing_one_section_leaves_the_other_untouched():
    body = comment.replace_section(None, "verdict", "VERDICT ONE")
    body = comment.replace_section(body, "pictures", "PICTURES ONE")
    again = comment.replace_section(body, "verdict", "VERDICT TWO")
    assert comment.get_section(again, "verdict") == "VERDICT TWO"
    assert comment.get_section(again, "pictures") == "PICTURES ONE"
    # every byte outside the replaced section is the same
    head, _, rest = body.partition(comment.start("verdict"))
    _, _, tail = rest.partition(comment.end("verdict"))
    assert again == head + comment.start("verdict") + "\nVERDICT TWO\n" + comment.end("verdict") + tail
    assert again.count(comment.MARKER) == 1


def test_a_section_missing_from_an_edited_comment_is_appended():
    body = f"{comment.MARKER}\nsomeone edited me\n"
    out = comment.replace_section(body, "pictures", "P")
    assert out.startswith(body.rstrip("\n")) and comment.get_section(out, "pictures") == "P"


def test_content_cannot_carry_markers():
    with pytest.raises(ValueError):
        comment.replace_section(None, "verdict", f"x {comment.end('pictures')} y")


def test_md_neutralises_formatting_mentions_html_and_markers():
    nasty = "[click](https://evil.example) <!-- verdict:end --> @owner **bold** <img src=x> `code`\n\n# heading"
    out = comment.md(nasty)
    assert "<" not in out and ">" not in out and "[" not in out and "@" not in out and "`" not in out and "*" not in out
    assert "://" not in out and "\n" not in out
    assert "verdict:end" in out and "<!--" not in out
    assert comment.md("x" * 1000, 50).endswith("…") and len(comment.md("x" * 1000, 50)) == 50


def test_upsert_creates_one_comment_and_each_workflow_edits_only_its_section():
    gh = FakeGitHub()
    comment.upsert(gh, gh.repo, 5, "verdict", "V1")
    comment.upsert(gh, gh.repo, 5, "pictures", "P1")
    comment.upsert(gh, gh.repo, 5, "verdict", "V2")
    assert len(gh.comments) == 1
    body = gh.comments[0]["body"]
    assert comment.get_section(body, "verdict") == "V2" and comment.get_section(body, "pictures") == "P1"


def test_a_comment_by_someone_else_quoting_the_marker_is_never_edited():
    gh = FakeGitHub()
    gh.add_comment(f"{comment.MARKER}\nforged", login="mallory")
    comment.upsert(gh, gh.repo, 5, "verdict", "V1")
    assert len(gh.comments) == 2
    assert gh.comments[0]["body"].endswith("forged")
    assert comment.find_comment(gh.comments)["id"] == gh.comments[1]["id"]


def test_a_write_that_another_run_overwrote_is_written_again(monkeypatch):
    gh = FakeGitHub()
    comment.upsert(gh, gh.repo, 5, "verdict", "V1")
    real = gh.request
    state = {"clobbered": False}

    def request(method, path, body=None):
        out = real(method, path, body)
        if method == "PATCH" and not state["clobbered"]:  # a concurrent writer restores an old body
            state["clobbered"] = True
            gh.comments[0]["body"] = comment.replace_section(gh.comments[0]["body"], "pictures", "STALE")
        return out

    gh.request = request
    monkeypatch.setattr(comment.time, "sleep", lambda s: None)
    comment.upsert(gh, gh.repo, 5, "pictures", "P2")
    assert comment.get_section(gh.comments[0]["body"], "pictures") == "P2"


# -- the verdict text


def report(ok=True):
    model = {
        "id": "mine:van", "slot": "vehicles.panel-van", "change": "added", "triangles": 1200, "budget": 3000,
        "size_m": {"length": 5.4, "width": 2.0, "height": 2.5}, "licence": "CC0-1.0", "ai_generated": "no",
    }
    errors = [] if ok else [{"path": "packs/mine/van.toml", "model": "mine:van", "rule": "x", "message": "bad <b>tint</b> @owner"}]
    return {"version": 1, "mode": "pr", "ok": ok, "errors": errors, "warnings": [{"path": "p", "model": "mine:van", "rule": "w", "message": "careful"}],
            "waived": [], "models": [model], "slots": [{"id": "vehicles.panel-van", "status": "missing", "models": ["mine:van"], "wanted_more": 0}], "examples": []}


def test_the_verdict_names_the_commit_the_models_and_the_status_line_to_change():
    text = verdict.verdict_section(report(), {"ok": True, "description": "1 commit signed off", "lines": []}, "a" * 40, "https://run")
    assert "`aaaaaaa`" in text and "mine:van" in text and "1,200 of 3,000" in text
    assert 'status = "missing"' in text and 'status = "have"' in text
    assert "Passed" in text


def test_the_verdict_escapes_text_from_the_pull_request_and_lists_sign_off_fixes():
    dco = {"ok": False, "description": "bbbbbbb not signed off: run git commit --amend -s", "failures": [{"sha": "b" * 40, "subject": "Set tint", "reason": "has no sign-off"}], "lines": []}
    text = verdict.verdict_section(report(ok=False), dco, "a" * 40, "https://run")
    assert "<b>" not in text and "@owner" not in text and "Needs changes" in text
    assert "git commit --amend -s" in text


def test_statuses_reflect_the_results():
    assert verdict.validate_status(report())[0] == "success"
    state, desc = verdict.validate_status(report(ok=False))
    assert state == "failure" and len(desc) <= 140
    assert verdict.validate_status(None)[0] == "error" and verdict.dco_status(None)[0] == "error"
