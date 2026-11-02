"""Credits (ACON-011, design D8) and the board data."""

import json

from builders import TestRepo, box_glb, manifest_toml, slot_toml, van_repo
from assets_lib import board, credits
from helpers import run


def bus_stop_repo(tmp_path):
    repo = TestRepo(
        tmp_path / "r",
        slots={"streets": slot_toml("streets.bus-stop-sign", kind="street.bench", size=(0.4, 0.1, 2.5)) + slot_toml("streets.bench", kind="street.bench", size=(1, 1, 1), status="missing")},
        pack_toml='id = "poly:base"\nversion = "1"\nlicence = "CC0-1.0"\nai_generated = "unknown"\nauthor = "Various"\nsource = "https://poly.pizza"\n',
    )
    repo.write("packs/mine/licences/page.html", "<p>CC BY 3.0</p>")
    repo.model(
        "bus-stop-sign", box_glb(0.4, 2.5, 0.1),
        manifest_toml(
            "mine:bus-stop-sign", kind="street.bench", slot="streets.bus-stop-sign", length=0.4, provenance=False,
            extra='licence_evidence = "licences/page.html"\nlicence = "CC-BY-3.0"\ntitle = "Bus stop sign"\nauthor = "dook"\nsource = "https://poly.pizza/m/ECdbKwxoLa"\n'
                  'changes = ["scaled"]\ncontributors = ["someone"]\n',
        ),
        folder="street",
    )
    return repo


def test_ACON_011_a_cc_by_model_is_credited_in_full(tmp_path):
    repo = bus_stop_repo(tmp_path)
    data = credits.collect(repo.root)
    (entry,) = data["cc_by"]
    assert entry["title"] == "Bus stop sign" and entry["author"] == "dook"
    assert entry["source"] == "https://poly.pizza/m/ECdbKwxoLa"
    assert (entry["licence_name"], entry["licence_url"]) == ("CC BY 3.0", "https://creativecommons.org/licenses/by/3.0/")
    assert entry["changes"] == ["scaled"] and entry["models"] == ["mine:bus-stop-sign"]
    assert entry["evidence"] == "mine/licences/page.html"
    assert data["contributors"] == [{"handle": "someone", "models": ["mine:bus-stop-sign"]}]
    md = credits.to_markdown(data)
    line = next(l for l in md.splitlines() if "Bus stop sign" in l)
    for needle in ('"Bus stop sign" by dook', "<https://poly.pizza/m/ECdbKwxoLa>", "[CC BY 3.0](https://creativecommons.org/licenses/by/3.0/)", "Changes: scaled"):
        assert needle in line
    assert "- @someone: `mine:bus-stop-sign`" in md and "by someone" not in md


def test_cc_by_4_credits_point_to_the_evidence_and_attribution_is_verbatim(tmp_path):
    repo = bus_stop_repo(tmp_path)
    repo.write(
        "packs/mine/street/bus-stop-sign.toml",
        manifest_toml(
            "mine:bus-stop-sign", kind="street.bench", slot="streets.bus-stop-sign", length=0.4, provenance=False,
            extra='licence_evidence = "licences/page.html"\nlicence = "CC-BY-4.0"\ntitle = "Sign"\nauthor = "dook"\nattribution = "Sign by Dook Studio, used with thanks"\n',
        ),
    )
    md = credits.to_markdown(credits.collect(repo.root))
    assert "### CC BY 4.0" in md and "Sign by Dook Studio, used with thanks" in md
    assert "kept in `mine/licences/page.html`" in md


def test_cc0_models_are_listed_under_their_author_and_ai_models_name_their_tool(tmp_path):
    repo = van_repo(tmp_path)
    repo.write("packs/mine/vehicles/van.toml", manifest_toml("mine:van", extra='ai_generated = "yes"\nai_tool = "Meshy"\nai_model = "meshy-t2"\nai_plan = "paid"\n'))
    data = credits.collect(repo.root)
    assert data["cc0"][0]["author"] == "A. Maker" and data["cc0"][0]["models"][0]["id"] == "mine:van"
    assert data["ai_generated"] == [{"id": "mine:van", "tool": "Meshy", "model": "meshy-t2"}]
    assert "AI-generated with Meshy (meshy-t2)" in credits.to_markdown(data)


def test_board_data_merges_catalogue_report_and_index(tmp_path, capsys):
    repo = van_repo(tmp_path)
    repo.write("catalogue/vehicles.toml", slot_toml("vehicles.panel-van", variants=3) + slot_toml("vehicles.other", status="missing"))
    report = run(repo)
    index = {"version": 1, "models": {"mine:van": {"image": "mine/mine__van.png", "tinted": None}}, "examples": {}}
    warned = []
    data = board.build(repo.root, report, index, warn=warned.append)
    assert warned == []
    assert data["version"] == 1 and data["digest"] is None and data["repo_url"].endswith("simhood-assets")
    assert data["counts"] == {"slots": 2, "have": 1, "stand-in": 0, "missing": 1, "procedural": 0, "models": 1, "wanted": 3}
    slot = data["slots"][0]
    assert (slot["have"], slot["wanted_more"], slot["kind_name"], slot["kind_drawn"], slot["budget"]) == (1, 2, "Van", False, 3000)
    model = data["models"][0]
    assert model["thumb"] == "mine/mine__van.png" and model["variant"] == {"index": 1, "of": 3}
    assert model["licence_name"] == "CC0 1.0" and model["ai"]["label"] == "no AI, stated by the source"
    assert [d["id"] for d in data["domains"]][0] == "vehicles" and len(data["domains"]) == 8
    json.dumps(data)


def test_board_data_tolerates_a_missing_index_entry(tmp_path):
    repo = van_repo(tmp_path)
    warned = []
    data = board.build(repo.root, run(repo), {"version": 1, "models": {}, "examples": {}}, warn=warned.append)
    assert data["models"][0]["thumb"] is None and data["models"][0]["thumb_tinted"] is None
    assert len(warned) == 1 and "mine:van" in warned[0]


def test_ai_labels_follow_the_contract():
    from assets_lib.licences import ai_label

    assert ai_label("no", True, None, None) == "no AI, stated by the source"
    assert ai_label("no", False, None, None) == "made without AI"
    assert ai_label("unknown", True, None, None) == "not stated by the source"
    assert ai_label("yes", False, "Meshy", "meshy-t2") == "AI-generated with Meshy (meshy-t2)"


def test_board_examples_use_the_renderers_file_names(tmp_path):
    repo = van_repo(tmp_path)
    repo.write("examples/examples.toml", '[[good]]\nmodel = "mine:van"\nwhy = "ok"\n[[rejected]]\nid = "example:brick"\nname = "Brick"\nwhy = "PBR"\n')
    index = {"version": 1, "models": {}, "examples": {"example:brick": {"image": "examples/example__brick.png"}}}
    data = board.build(repo.root, run(repo), index, warn=lambda m: None)
    assert data["examples"]["good"] == [{"model": "mine:van", "why": "ok"}]
    assert data["examples"]["rejected"][0]["image"] == "examples/example__brick.png"
