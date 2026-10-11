"""The detail layout (#230): ``DETAIL_LAYOUT`` is stored, normalized and served
through ``/api/settings`` and carried by the page's first paint, like light/dark.
The rule module itself is only reached over HTTP (and the first paint)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import settings_store
import webapp
from literature_manager import Config

DEFAULT = {
    "v": 1,
    "main": ["abstract", "annotations"],
    "tabs": [
        {"name": "", "blocks": ["metadata", "customFields", "categories", "collections"]},
        {"name": "", "blocks": ["references"]},
        {"name": "", "blocks": ["notes"]},
    ],
    "hidden": [],
}


@pytest.fixture
def temp_env(tmp_path, monkeypatch):
    import routers.settings as settings_mod

    monkeypatch.setattr(settings_mod, "_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(Config, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv("LLM_CONFIG_PATH", str(tmp_path / "llm.json"))
    Config.reload_from_env()
    yield tmp_path
    monkeypatch.undo()
    Config.reload_from_env()


def put(c, layout):
    text = layout if isinstance(layout, str) else json.dumps(layout)
    return c.put("/api/settings", json={"DETAIL_LAYOUT": text})


def stored(c):
    return json.loads(c.get("/api/settings").json()["DETAIL_LAYOUT"])


def test_without_an_entry_the_default_layout_is_served(temp_env):
    with TestClient(webapp.app) as c:
        assert stored(c) == DEFAULT


def test_a_layout_is_stored_and_read_back(temp_env):
    mine = {"v": 1, "main": ["notes", "abstract"],
            "tabs": [{"name": "Reading", "blocks": ["annotations", "references"]},
                     {"name": "", "blocks": ["metadata", "customFields", "categories", "collections"]}],
            "hidden": []}
    with TestClient(webapp.app) as c:
        assert put(c, mine).status_code == 200
        assert stored(c) == mine
    line = [ln for ln in (temp_env / ".env").read_text(encoding="utf-8").splitlines()
            if ln.startswith("DETAIL_LAYOUT=")]
    assert len(line) == 1  # one compact line


def test_unknown_blocks_fall_away_and_duplicates_count_once(temp_env):
    with TestClient(webapp.app) as c:
        put(c, {"main": ["abstract", "bogus", "abstract", 7], "tabs": [
            {"name": "", "blocks": ["abstract", "metadata", "customFields", "categories", "collections"]},
            {"name": "", "blocks": ["annotations", "references", "notes"]}], "hidden": ["bogus"]})
        got = stored(c)
    assert got["main"] == ["abstract"]
    assert got["tabs"][0]["blocks"] == ["metadata", "customFields", "categories", "collections"]
    assert got["tabs"][1]["blocks"] == ["annotations", "references", "notes"]
    assert got["hidden"] == []


def test_a_missing_block_returns_to_its_default_place(temp_env):
    with TestClient(webapp.app) as c:
        # Annotations and Collections are missing; Notes is hidden on purpose.
        put(c, {"main": ["abstract"],
                "tabs": [{"name": "", "blocks": ["metadata", "customFields", "categories"]},
                         {"name": "", "blocks": ["references"]}],
                "hidden": ["notes"]})
        got = stored(c)
    assert got["main"] == ["abstract", "annotations"]
    assert got["tabs"][0]["blocks"] == ["metadata", "customFields", "categories", "collections"]
    assert got["hidden"] == ["notes"]


def test_a_missing_block_without_siblings_gets_a_tab_of_its_own(temp_env):
    with TestClient(webapp.app) as c:
        put(c, {"main": ["abstract", "annotations", "metadata", "customFields", "categories", "collections"],
                "tabs": [], "hidden": []})
        got = stored(c)
    assert got["tabs"] == [{"name": "", "blocks": ["references"]}, {"name": "", "blocks": ["notes"]}]


def test_empty_tabs_fall_away(temp_env):
    with TestClient(webapp.app) as c:
        put(c, {"main": ["abstract", "annotations"], "tabs": [
            {"name": "Empty", "blocks": []}, {"name": "", "blocks": ["bogus"]},
            {"name": "Rest", "blocks": ["metadata", "customFields", "categories", "collections",
                                         "references", "notes"]}], "hidden": []})
        got = stored(c)
    assert got["tabs"] == [{"name": "Rest", "blocks": [
        "metadata", "customFields", "categories", "collections", "references", "notes"]}]


def test_at_most_four_tabs_the_rest_goes_into_the_fourth(temp_env):
    tabs = [{"name": f"T{i}", "blocks": [b]} for i, b in enumerate(
        ["metadata", "customFields", "categories", "collections", "references", "notes"])]
    with TestClient(webapp.app) as c:
        put(c, {"main": ["abstract", "annotations"], "tabs": tabs, "hidden": []})
        got = stored(c)
    assert [t["name"] for t in got["tabs"]] == ["T0", "T1", "T2", "T3"]
    assert got["tabs"][3]["blocks"] == ["collections", "references", "notes"]


def test_a_tab_name_is_trimmed_and_cut_at_24_characters(temp_env):
    with TestClient(webapp.app) as c:
        put(c, {"main": [], "tabs": [
            {"name": "   Lesen   ", "blocks": ["abstract", "annotations"]},
            {"name": "x" * 40, "blocks": ["metadata", "customFields", "categories", "collections"]},
            {"name": 5, "blocks": ["references", "notes"]}], "hidden": []})
        got = stored(c)
    assert got["tabs"][0]["name"] == "Lesen"
    assert got["tabs"][1]["name"] == "x" * 24
    assert got["tabs"][2]["name"] == ""


@pytest.mark.parametrize("bad", [
    "not json", "[]", "7", "null", '"text"',
    '{"main": "abstract"}', '{"tabs": {}}', '{"tabs": ["abstract"]}',
    '{"tabs": [{"blocks": "notes"}]}', '{"hidden": 3}',
])
def test_no_json_or_a_wrong_basic_shape_is_refused_and_not_written(temp_env, bad):
    with TestClient(webapp.app) as c:
        resp = put(c, bad)
        assert resp.status_code == 422
        assert resp.json()["detail"] == {"code": "error.settings.invalidLayout",
                                         "params": {"key": "DETAIL_LAYOUT"}}
        assert stored(c) == DEFAULT
    assert not (temp_env / ".env").exists() or "DETAIL_LAYOUT" not in (temp_env / ".env").read_text(encoding="utf-8")


def test_an_unusable_stored_value_reads_as_the_default(temp_env):
    (temp_env / ".env").write_text("DETAIL_LAYOUT=garbage\n", encoding="utf-8")
    with TestClient(webapp.app) as c:
        assert stored(c) == DEFAULT
        assert '"detailLayout"' in c.get("/").text


def first_paint_layout(html):
    import re
    m = re.search(r"window\.LB_PREFS = (\{.*?\});\n", html, re.S)
    return json.loads(m.group(1))["detailLayout"]


def test_the_page_carries_the_layout_for_the_first_paint(temp_env):
    with TestClient(webapp.app) as c:
        assert first_paint_layout(c.get("/").text) == DEFAULT
        put(c, {"main": ["notes"], "tabs": [], "hidden": ["abstract"]})
        got = first_paint_layout(c.get("/").text)
    # Notes stays where the user put it; the rest comes back at its defaults.
    assert got["main"] == ["notes", "annotations"]
    assert got["hidden"] == ["abstract"]


def test_a_lone_surrogate_in_a_tab_name_cannot_empty_the_env(temp_env):
    # Review of #235 (1): the surrogate used to reach write_text, which had
    # already truncated the file when the UTF-8 encoding raised.
    env = temp_env / ".env"
    env.write_text("CROSSREF_MAILTO=me@example.org\n", encoding="utf-8")
    with TestClient(webapp.app, raise_server_exceptions=False) as c:
        resp = put(c, '{"tabs":[{"name":"a\\ud83db","blocks":["notes"]}]}')
        assert resp.status_code == 200
        got = stored(c)
    assert [t["name"] for t in got["tabs"] if "notes" in t["blocks"]] == ["ab"]
    assert "CROSSREF_MAILTO=me@example.org" in env.read_text(encoding="utf-8")


def test_a_lone_surrogate_in_any_other_setting_is_a_coded_422_and_writes_nothing(temp_env):
    # Review round 2 of #235 (6).
    env = temp_env / ".env"
    env.write_text("CROSSREF_MAILTO=me@example.org\n", encoding="utf-8")
    with TestClient(webapp.app, raise_server_exceptions=False) as c:
        resp = c.put("/api/settings", content='{"CROSSREF_MAILTO": "a\\ud800", "DARK_MODE": "true"}',
                     headers={"Content-Type": "application/json"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == {"code": "error.settings.invalidValue",
                                     "params": {"key": "CROSSREF_MAILTO"}}
    assert env.read_text(encoding="utf-8") == "CROSSREF_MAILTO=me@example.org\n"


def test_a_locked_env_answers_423_with_a_code(temp_env, monkeypatch):
    # Review round 2 of #235 (1): the old .env stays, the SPA gets a code.
    import os
    env = temp_env / ".env"
    env.write_text("A=1\n", encoding="utf-8")
    monkeypatch.setattr(settings_store, "_REPLACE_DELAY", 0)
    monkeypatch.setattr(os, "replace", lambda s, d: (_ for _ in ()).throw(PermissionError("held")))
    with TestClient(webapp.app) as c:
        resp = c.put("/api/settings", json={"DARK_MODE": "true"})
    assert resp.status_code == 423
    assert resp.json()["detail"] == {"code": "error.settings.locked"}
    assert env.read_text(encoding="utf-8") == "A=1\n"


def test_the_save_answers_with_the_normalized_layout(temp_env):
    # Review of #235 (13): no second GET needed.
    with TestClient(webapp.app) as c:
        body = put(c, {"main": ["notes"], "tabs": [], "hidden": ["abstract"]}).json()
        assert body["status"] == "ok"
        assert body["detail_layout"] == stored(c)
        assert "detail_layout" not in c.put("/api/settings", json={"DARK_MODE": "true"}).json()


def test_the_page_carries_the_layout_rules(temp_env):
    # Review of #235 (14): default and limits come from the server.
    import re
    with TestClient(webapp.app) as c:
        m = re.search(r"window\.LB_PREFS = (\{.*?\});\n", c.get("/").text, re.S)
    rules = json.loads(m.group(1))["detailLayoutRules"]
    assert rules == {"default": DEFAULT, "max_tabs": 4, "max_name": 24}


def test_a_tab_name_counts_code_points(temp_env):
    with TestClient(webapp.app) as c:
        put(c, {"tabs": [{"name": "\U0001F600" * 30, "blocks": ["notes"]}]})
        got = stored(c)
    name = [t["name"] for t in got["tabs"] if "notes" in t["blocks"]][0]
    assert len(name) == 24
