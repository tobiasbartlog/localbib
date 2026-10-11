"""Refresh Review: propose writes nothing, apply writes only ticked rows.

The pure half (``services/refresh_review``) is tested directly; the router is
driven through the TestClient with the LLM/CrossRef/PDF boundaries patched on
``routers.refresh_review``.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import routers.refresh_review as rr
from literature_manager import Config
from services import refresh_review as svc
from tests.llm_helpers import llm_off


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _seed(db, title="Old Title", abstract="", doi="10.1/x", with_file=True,
          filename="rr_test.pdf") -> int:
    if with_file:
        (Path(Config.ALL_DIR) / filename).write_bytes(b"%PDF-1.4 fake")
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title, authors, year, doi, abstract) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (f"h-{filename}", filename, filename, title, "Doe, J.", 2020, doi, abstract),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _category(db, name) -> int:
    conn = db._connect()
    try:
        cur = conn.execute("INSERT INTO categories (name, description, keywords) VALUES (?, '', '')", (name,))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _assign(db, pid, cid, by="manual"):
    db.assign_category(pid, cid, 0.9, by)


def _cats(db, pid) -> set:
    return {c["id"] for c in db.get_paper_categories(pid)}


def _field(db, pid, fld):
    conn = db._connect()
    try:
        return conn.execute(f"SELECT {fld} FROM papers WHERE id = ?", (pid,)).fetchone()[0]
    finally:
        conn.close()


def _events(resp) -> list[dict]:
    return [json.loads(l[6:]) for l in resp.text.split("\n") if l.strip().startswith("data: ")]


def _proposal(changes=None, sources=None):
    return SimpleNamespace(changes=changes or {}, source_per_field=sources or {})


# ---------------------------------------------------------------------------
# pure rows
# ---------------------------------------------------------------------------

class TestRows:
    def test_field_rows_skip_equal_values_and_flag_clears(self):
        paper = {"id": 1, "title": "Same", "year": 2020, "doi": "10.1/x", "journal": ""}
        rows = svc.field_rows(paper, {"title": "Same", "year": "2020", "doi": "", "journal": "J"},
                              {"journal": "crossref"})
        by_field = {r["field"]: r for r in rows}
        assert set(by_field) == {"doi", "journal"}
        assert by_field["doi"]["clears"] is True
        assert by_field["journal"]["clears"] is False
        assert by_field["journal"]["source"] == "crossref"

    def test_category_rows_add_and_remove(self):
        current = [{"id": 1, "name": "A", "assigned_by": "manual"}, {"id": 2, "name": "B"}]
        rows = svc.category_rows(7, current, [{"category_id": 2, "confidence": 0.8},
                                              {"category_id": 3, "confidence": 0.6}],
                                 {1: "A", 2: "B", 3: "C"})
        kinds = {(r["kind"], r["category_id"]) for r in rows}
        assert kinds == {("category_add", 3), ("category_remove", 1)}
        removal = next(r for r in rows if r["kind"] == "category_remove")
        assert removal["before"] == "A" and removal["after"] is None
        assert removal["assigned_by"] == "manual"

    def test_empty_suggestion_never_proposes_removing_everything(self):
        rows = svc.category_rows(7, [{"id": 1, "name": "A"}], [], {1: "A"})
        assert rows == []

    def test_plan_apply_skips_stale_rows(self):
        papers = {1: {"id": 1, "title": "Edited by user"}}
        rows = [
            {"id": "a", "paper_id": 1, "kind": "field", "field": "title", "before": "Old", "after": "New"},
            {"id": "b", "paper_id": 1, "kind": "category_add", "category_id": 5},
            {"id": "c", "paper_id": 1, "kind": "category_remove", "category_id": 6},
            {"id": "d", "paper_id": 1, "kind": "category_add", "category_id": 99},
            {"id": "e", "paper_id": 2, "kind": "category_add", "category_id": 5},
            {"id": "f", "paper_id": 1, "kind": "field", "field": "filename", "before": "", "after": "x"},
        ]
        plan, skipped = svc.plan_apply(rows, papers, {1: {5}}, {5, 6})
        assert plan == {}
        assert {s["id"]: s["reason"] for s in skipped} == {
            "a": "changed", "b": "already_assigned", "c": "already_removed",
            "d": "category_not_found", "e": "item_not_found", "f": "invalid",
        }


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------

class TestPropose:
    def test_writes_nothing_and_returns_before_after(self, client, db):
        keep = _category(db, "Keep")
        old = _category(db, "Old")
        new = _category(db, "New")
        pid = _seed(db)
        _assign(db, pid, keep)
        _assign(db, pid, old, by="llm")

        with patch.object(rr, "extract_text_from_pdf", return_value="pdf text"), \
             patch.object(rr, "llm_for", return_value=MagicMock(last_error=None)), \
             patch.object(rr.metadata_validation, "propose",
                          return_value=_proposal({"title": "New Title"}, {"title": "crossref"})), \
             patch.object(rr, "fetch_crossref_metadata", return_value={"abstract": "An abstract " * 10}), \
             patch.object(rr, "categorize_with_llm",
                          return_value=[{"category_id": keep, "confidence": 0.9},
                                        {"category_id": new, "confidence": 0.7}]):
            resp = client.post("/api/maintenance/refresh-review/propose",
                               json={"scopes": ["categories", "metadata", "abstracts"],
                                     "only_gaps": False})
        assert resp.status_code == 200
        done = _events(resp)[-1]
        assert done["type"] == "done"
        rows = {r["id"]: r for r in done["rows"]}
        assert rows[f"{pid}:field:title"]["before"] == "Old Title"
        assert rows[f"{pid}:field:title"]["after"] == "New Title"
        assert rows[f"{pid}:field:abstract"]["source"] == "crossref"
        assert rows[f"{pid}:category_add:{new}"]["after"] == "New"
        assert rows[f"{pid}:category_remove:{old}"]["before"] == "Old"
        assert all(r["paper_title"] == "Old Title" for r in done["rows"])
        # nothing persisted
        assert _field(db, pid, "title") == "Old Title"
        assert _cats(db, pid) == {keep, old}

    def test_only_selected_scope_runs(self, client, db):
        pid = _seed(db)
        cat = _category(db, "Only")
        with patch.object(rr, "extract_text_from_pdf", return_value="pdf text"), \
             patch.object(rr, "llm_for", return_value=MagicMock(last_error=None)), \
             patch.object(rr.metadata_validation, "propose") as propose, \
             patch.object(rr, "categorize_with_llm",
                          return_value=[{"category_id": cat, "confidence": 0.9}]):
            resp = client.post("/api/maintenance/refresh-review/propose",
                               json={"scopes": ["categories"], "paper_ids": [pid],
                                     "only_gaps": False})
        propose.assert_not_called()
        rows = _events(resp)[-1]["rows"]
        assert [r["kind"] for r in rows] == ["category_add"]

    def test_no_scope_is_rejected(self, client, db):
        resp = client.post("/api/maintenance/refresh-review/propose", json={"scopes": ["bogus"]})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "error.refresh_review_no_scope"

    def test_categories_without_llm_warn(self, client, db, monkeypatch):
        _seed(db)
        llm_off(monkeypatch)
        with patch.object(rr, "extract_text_from_pdf", return_value=""):
            resp = client.post("/api/maintenance/refresh-review/propose", json={"scopes": ["categories"]})
        done = _events(resp)[-1]
        assert "llm_not_ready" in done["warnings"]
        assert done["rows"] == []


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

class TestApply:
    def test_applies_only_ticked_rows(self, client, db):
        keep = _category(db, "Keep")
        old = _category(db, "Old")
        new = _category(db, "New")
        pid = _seed(db)
        _assign(db, pid, keep)
        _assign(db, pid, old)
        rows = [
            {"id": "1", "paper_id": pid, "kind": "category_add", "category_id": new, "confidence": 0.7},
            {"id": "2", "paper_id": pid, "kind": "category_remove", "category_id": old},
            {"id": "3", "paper_id": pid, "kind": "field", "field": "abstract",
             "before": "", "after": "Fresh abstract"},
        ]
        with patch.object(rr, "_rebuild_all_symlinks") as rebuild:
            resp = client.post("/api/maintenance/refresh-review/apply", json={"rows": rows})
        assert resp.status_code == 200
        assert resp.json() == {"applied": 3, "skipped": []}
        assert _cats(db, pid) == {keep, new}
        assert _field(db, pid, "abstract") == "Fresh abstract"
        assert _field(db, pid, "abstract_source") == "refresh"
        rebuild.assert_called_once()

    def test_field_edited_since_preview_is_not_overwritten(self, client, db):
        pid = _seed(db, title="User edit")
        rows = [{"id": "t", "paper_id": pid, "kind": "field", "field": "title",
                 "before": "Old Title", "after": "LLM Title"}]
        resp = client.post("/api/maintenance/refresh-review/apply", json={"rows": rows})
        assert resp.json() == {"applied": 0, "skipped": [{"id": "t", "reason": "changed"}]}
        assert _field(db, pid, "title") == "User edit"

    def test_add_only_links_without_full_rebuild(self, client, db):
        cat = _category(db, "Fresh")
        pid = _seed(db)
        rows = [{"id": "a", "paper_id": pid, "kind": "category_add", "category_id": cat}]
        with patch.object(rr, "_rebuild_all_symlinks") as rebuild, \
             patch.object(rr, "create_symlinks") as link:
            resp = client.post("/api/maintenance/refresh-review/apply", json={"rows": rows})
        assert resp.json()["applied"] == 1
        rebuild.assert_not_called()
        link.assert_called_once()
        assert _cats(db, pid) == {cat}
        assert db.get_paper_categories(pid)[0]["assigned_by"] == "refresh-review"

# ---------------------------------------------------------------------------
# "only fill gaps" and count (#210)
# ---------------------------------------------------------------------------

class TestScopesFor:
    def test_only_gaps_narrows_categories_and_metadata(self):
        bare = {"id": 1, "doi": "", "abstract": "x"}
        full = {"id": 2, "doi": "10.1/x", "abstract": ""}
        assert svc.scopes_for(bare, False, ["categories", "metadata", "abstracts"], True) == {"categories", "metadata"}
        assert svc.scopes_for(full, True, ["categories", "metadata", "abstracts"], True) == {"abstracts"}

    def test_without_the_flag_every_item_is_in_but_abstracts_keep_their_gap(self):
        full = {"id": 2, "doi": "10.1/x", "abstract": "has one"}
        assert svc.scopes_for(full, True, ["categories", "metadata", "abstracts"], False) == {"categories", "metadata"}


class TestOnlyGaps:
    def _library(self, db):
        """a: no category, no DOI. b: categorised, has a DOI."""
        cat = _category(db, "Existing")
        a = _seed(db, title="A", doi="", filename="a.pdf")
        b = _seed(db, title="B", doi="10.1/b", filename="b.pdf")
        _assign(db, b, cat)
        return a, b, cat

    def _propose(self, client, cat, **body):
        with patch.object(rr, "extract_text_from_pdf", return_value="pdf text"), \
             patch.object(rr, "llm_for", return_value=MagicMock(last_error=None)), \
             patch.object(rr.metadata_validation, "propose",
                          return_value=_proposal({"journal": "J"}, {"journal": "crossref"})), \
             patch.object(rr, "categorize_with_llm",
                          return_value=[{"category_id": cat, "confidence": 0.9}]):
            resp = client.post("/api/maintenance/refresh-review/propose", json=body)
        return _events(resp)[-1]

    def test_categories_only_for_uncategorised_items_and_only_additions(self, client, db):
        a, b, cat = self._library(db)
        done = self._propose(client, cat, scopes=["categories"])  # only_gaps defaults on
        assert done["stats"]["items"] == 1
        assert {r["paper_id"] for r in done["rows"]} == {a}
        assert all(r["kind"] == "category_add" for r in done["rows"])

    def test_metadata_only_for_items_without_doi(self, client, db):
        a, b, cat = self._library(db)
        done = self._propose(client, cat, scopes=["metadata"])
        assert {r["paper_id"] for r in done["rows"]} == {a}

    def test_without_the_flag_everything_is_reassessed(self, client, db):
        a, b, cat = self._library(db)
        done = self._propose(client, cat, scopes=["metadata"], only_gaps=False)
        assert done["stats"]["items"] == 2
        assert {r["paper_id"] for r in done["rows"]} == {a, b}


class TestCount:
    def test_same_set_as_propose_and_no_network(self, client, db):
        cat = _category(db, "Existing")
        a = _seed(db, title="A", doi="", filename="a.pdf")
        b = _seed(db, title="B", doi="10.1/b", filename="b.pdf")
        _assign(db, b, cat)
        boom = MagicMock(side_effect=AssertionError("count must not reach out"))
        for body in ({"scopes": ["categories"]},
                     {"scopes": ["categories", "metadata"], "only_gaps": False},
                     {"scopes": ["metadata", "abstracts"], "paper_ids": [b]},
                     {"scopes": ["abstracts"], "only_gaps": False}):
            with patch.object(rr, "llm_for", boom), patch.object(rr, "fetch_crossref_metadata", boom), \
                 patch.object(rr, "categorize_with_llm", boom), patch.object(rr, "extract_text_from_pdf", boom):
                counted = client.post("/api/maintenance/refresh-review/count", json=body).json()["count"]
            with patch.object(rr, "extract_text_from_pdf", return_value=""), \
                 patch.object(rr, "llm_for", return_value=MagicMock(last_error=None)), \
                 patch.object(rr, "categorize_with_llm", return_value=[]):
                done = _events(client.post("/api/maintenance/refresh-review/propose", json=body))[-1]
            assert counted == done["stats"]["items"], body

    def test_numbers(self, client, db):
        cat = _category(db, "Existing")
        _seed(db, title="A", doi="", filename="a.pdf")
        b = _seed(db, title="B", doi="10.1/b", filename="b.pdf")
        _assign(db, b, cat)
        post = lambda **body: client.post("/api/maintenance/refresh-review/count", json=body).json()["count"]
        assert post(scopes=["categories"]) == 1
        assert post(scopes=["categories"], only_gaps=False) == 2
        assert post(scopes=["metadata"]) == 1

    def test_no_scope_is_rejected(self, client, db):
        resp = client.post("/api/maintenance/refresh-review/count", json={"scopes": ["x"]})
        assert resp.status_code == 400
