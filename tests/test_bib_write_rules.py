"""The rules every metadata write follows, and organizations in braces.

Maintainer decisions for Ticket 3:

(a) A person line in braces (``{Autobahn Westfalen}``) is an organization.
    Storage keeps the braces, the export writes them, a braced corporate
    author of an old .bib keeps them on import, and every display strips
    them — the contract's display fields too, so an Add-on never shows them.
(c) ``validation_policy.validate_apply`` and so the Refresh Review apply
    validate ``item_type`` and keep ``year`` and ``pub_date`` in step exactly
    like ``PUT /api/papers/{id}`` — one helper, ``bib_fields.checked_changes``.
"""
from __future__ import annotations

import pytest

import bib_fields
import host_services
from services import collection_export
from services import refresh_review


def _seed(db, **values) -> int:
    row = {"file_hash": f"h-{values.get('title') or values.get('doi')}", "filename": "",
           "original_filename": "", "title": "", "authors": "", "year": None, "doi": "",
           "isbn": "", "journal": "", "abstract": "", **values}
    conn = db._connect()
    try:
        cur = conn.execute(f"INSERT INTO papers ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                           tuple(row.values()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _row(db, paper_id) -> dict:
    conn = db._connect()
    try:
        return dict(conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# (c) one helper for the rules
# ---------------------------------------------------------------------------

class TestCheckedChanges:
    def test_year_follows_a_given_date(self):
        assert bib_fields.checked_changes({"pub_date": " 2011-3 ", "year": 1999}, {}) == {
            "pub_date": "2011-03", "year": 2011}

    def test_a_new_year_alone_clears_a_stored_date_of_another_year(self):
        assert bib_fields.checked_changes({"year": 2012}, {"pub_date": "2011-03"}) == {
            "year": 2012, "pub_date": ""}
        assert bib_fields.checked_changes({"year": 2011}, {"pub_date": "2011-03"}) == {"year": 2011}

    def test_none_is_not_given_and_strings_are_stripped(self):
        assert bib_fields.checked_changes({"volume": " 7 ", "issue": None, "title": "T"}, {}) == {
            "volume": "7", "title": "T"}

    @pytest.mark.parametrize("changes, code", [
        ({"item_type": "poem"}, "error.invalid_item_type"),
        ({"pub_date": "spring"}, "error.invalid_date"),
        ({"accessed": "2024-13-01"}, "error.invalid_date"),
    ])
    def test_refusals_carry_the_error_code(self, changes, code):
        with pytest.raises(bib_fields.InvalidValue) as exc:
            bib_fields.checked_changes(changes, {})
        assert exc.value.code == code


class TestValidateApplyFollowsTheRules:
    def test_an_unknown_type_is_refused_like_put(self, client, db):
        pid = _seed(db, title="T")
        resp = client.post(f"/api/papers/{pid}/validate/apply", json={"changes": {"item_type": "poem"}})
        assert resp.status_code == 422
        assert resp.json()["detail"] == {"code": "error.invalid_item_type", "params": {"value": "poem"}}
        put = client.put(f"/api/papers/{pid}", json={"item_type": "poem"})
        assert (put.status_code, put.json()["detail"]["code"]) == (422, "error.invalid_item_type")

    def test_year_follows_the_date_and_a_new_year_clears_a_stale_date(self, client, db):
        pid = _seed(db, title="T", year=2010)
        assert client.post(f"/api/papers/{pid}/validate/apply",
                           json={"changes": {"pub_date": "2011-3"}}).status_code == 200
        row = _row(db, pid)
        assert (row["pub_date"], row["year"]) == ("2011-03", 2011)
        assert client.post(f"/api/papers/{pid}/validate/apply",
                           json={"changes": {"year": 2012}}).status_code == 200
        row = _row(db, pid)
        assert (row["pub_date"], row["year"]) == ("", 2012)

    def test_a_refresh_review_year_row_keeps_the_date_in_step(self, client, db):
        pid = _seed(db, title="T", year=2011, pub_date="2011-03")
        row = {"id": f"{pid}:field:year", "paper_id": pid, "kind": "field", "field": "year",
               "before": 2011, "after": 2012, "source": "crossref", "clears": False}
        assert client.post("/api/maintenance/refresh-review/apply", json={"rows": [row]}).json() == {
            "applied": 1, "skipped": []}
        stored = _row(db, pid)
        assert (stored["year"], stored["pub_date"]) == (2012, "")


# ---------------------------------------------------------------------------
# (a) organizations in braces
# ---------------------------------------------------------------------------

BRACED_BIB = r"""
@online{Autobahn.2023,
  title   = {Brueckenmodernisierung im Bestand},
  author  = {{Autobahn Westfalen} and Chen, Li},
  url     = {https://example.org/bruecken},
  year    = {2023},
}
"""


class TestBracedOrganizations:
    def test_an_old_bib_round_trips_with_its_braces(self, client, db):
        preview = client.post("/api/import/bibtex/preview",
                              files={"bib_file": ("old.bib", BRACED_BIB, "text/plain")}).json()
        entry = preview["entries"][0]
        assert entry["authors"] == "{Autobahn Westfalen}; Chen, Li"
        body = {"source_name": "old.bib", "entries": [{
            **{k: entry.get(k) or "" for k in ("key", "title", "authors", "doi", "isbn", "abstract",
                                               "journal", "publisher", "entry_type")},
            "year": entry.get("year"), "raw": entry.get("raw") or {}}]}
        result = client.post("/api/import/bibtex/commit", json=body).json()
        assert result["collection"] is None   # no Collection: the import keeps none
        pid = result["results"][0]["paper_id"]
        assert _row(db, pid)["authors"] == "{Autobahn Westfalen}; Chen, Li"
        bib = client.get(f"/api/papers/{pid}/bibtex").json()["bibtex"]
        assert "author = {{Autobahn Westfalen} and Chen, Li}" in bib

    def test_the_contract_display_fields_strip_the_braces(self, db):
        _seed(db, title="T", authors="{Autobahn Westfalen}; Chen, Li", cite_key="Autobahn.2023")
        ref = host_services._CoreLibraryApi().get_reference("Autobahn.2023")
        assert ref["authors"] == "Autobahn Westfalen; Chen, Li"

    def test_csv_and_ris_strip_the_braces(self):
        paper = {"id": 1, "title": "T", "authors": "{Autobahn Westfalen}; Chen, Li",
                 "editors": "{DEGES GmbH}", "year": 2023, "cite_key": "A"}
        text = collection_export.csv_bytes([paper]).decode("utf-8-sig")
        assert "Autobahn Westfalen; Chen, Li" in text and "{" not in text
        assert "DEGES GmbH" in text
        ris = collection_export.ris_text([paper], all_dir="")
        assert "AU  - Autobahn Westfalen" in ris and "AU  - Chen, Li" in ris and "{" not in ris


# ---------------------------------------------------------------------------
# The SPA's smokes read the real table
# ---------------------------------------------------------------------------

def test_the_item_types_fixture_of_the_spa_smokes_is_the_table(client):
    """tests/js serve tests/fixtures/item_types.json as GET
    /api/papers/item-types: it must be the table, labels per type included,
    or the smokes test a form the server never describes. Regenerate with
    ``json.dumps({**bib_fields.wire_table(), "dialect": "biblatex"}, indent=1)``."""
    import json
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / "fixtures" / "item_types.json").read_text(encoding="utf-8"))
    served = client.get("/api/papers/item-types").json()
    assert fixture == {**served, "dialect": "biblatex"}
    conference = next(t for t in fixture["types"] if t["id"] == "conference")
    assert conference["labels"] == {"booktitle": "proceedings"}


# ---------------------------------------------------------------------------
# Refresh Review: what the bibliographic scope skips
# ---------------------------------------------------------------------------

def test_without_identifier_counts_what_the_scope_cannot_ask_for():
    papers = [{"doi": "10.1/x", "title": ""}, {"isbn": "978", "title": ""},
              {"title": ""}, {"title": "Complete misc"}]
    assert refresh_review.without_identifier(papers, only_gaps=False) == 2
    assert refresh_review.without_identifier(papers, only_gaps=True) == 1


def test_the_count_names_the_skipped_items(client, db):
    _seed(db, doi="10.1/x")
    _seed(db, title="No identifier")
    out = client.post("/api/maintenance/refresh-review/count",
                      json={"scopes": ["bibliographic"], "only_gaps": False}).json()
    assert out == {"count": 1, "no_identifier": 1}
    assert "no_identifier" not in client.post("/api/maintenance/refresh-review/count",
                                              json={"scopes": ["abstracts"]}).json()
