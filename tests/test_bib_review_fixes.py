"""Regression tests for the code review of the bibliographic metadata branch.

One section per finding:

1. An organization author is one name: structured sources brace it, and
   ``split_people`` never splits an unbraced one at its " and ".
2. One emptiness rule (``bib_fields.is_empty``): a year of 0 and a
   separator-only people string are empty for the export and the
   completeness check alike.
3. A patent's ``institution`` is labelled what the export writes: the holder.
4. The import enrichment asks a source only for an incomplete Item, and the
   BibTeX commit runs it off the event loop.
5. A cleared date without a year clears the year (NULL).
6. Open Library dates are valid dates; no lookup or enrichment writes a value
   the write rules would refuse.
7. ``PUT`` renames the PDF only when title, authors or year really change.
8. ``bibtex_import`` uses ``author_names``' brace helpers, no copy.
9. The Refresh Review's gap fills: written by the router, never over a stored
   type, no rename/symlink rebuild per Item, counted in every progress event.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import author_names
import bib_fields
import bib_lookup
import bibtex_builder
import bibtex_import
import crossref
import datacite
import openlibrary_client
import paper_ingest
import routers.papers as papers_router
import routers.refresh_review as rr
import validation_policy
from config import Config

ROOT = Path(__file__).resolve().parent.parent
CHAPTER_DOI = "10.1007/978-3-030-91320-5_7"
CHAPTER_RECORD = {
    "title": "Life-Cycle Cost Analysis of Bridge Superstructures", "authors": "Hoffmann, Lena; Becker, Tom",
    "year": 2022, "doi": CHAPTER_DOI, "publisher": "Springer International Publishing",
    "item_type": "chapter", "booktitle": "Life-Cycle of Structures and Infrastructure Systems",
    "pages": "101-118", "place": "Cham",
}


def _seed(db, **values) -> int:
    row = {"file_hash": f"h-{values.get('doi') or values.get('title') or values.get('filename')}",
           "filename": "", "original_filename": "", "title": "", "authors": "", "year": None,
           "doi": "", "isbn": "", "journal": "", "abstract": "", **values}
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


def _events(resp) -> list[dict]:
    return [json.loads(line[6:]) for line in resp.text.split("\n") if line.strip().startswith("data: ")]


def _pdf(name: str) -> str:
    os.makedirs(Config.ALL_DIR, exist_ok=True)
    with open(os.path.join(Config.ALL_DIR, name), "wb") as fh:
        fh.write(b"%PDF-1.4\n%%EOF\n")
    return name


# ---------------------------------------------------------------------------
# 1. Organizations stay one name
# ---------------------------------------------------------------------------

class TestOrganizations:
    def test_split_people_keeps_an_unbraced_organization_with_and_whole(self):
        assert author_names.split_people("Food and Agriculture Organization") == [
            "Food and Agriculture Organization"]
        # People joined by " and " still split, even with more words than a name.
        assert author_names.split_people("John Smith and Jane Doe") == ["John Smith", "Jane Doe"]
        assert author_names.split_people("Mustermann, T. and Krause, M.") == ["Mustermann, T.", "Krause, M."]

    def test_crossref_braces_an_organization(self):
        assert crossref.format_crossref_authors([
            {"name": "Food and Agriculture Organization"}, {"family": "Doe", "given": "Jane"},
        ]) == "{Food and Agriculture Organization}; Doe, Jane"
        export = bibtex_builder.people_field("{Food and Agriculture Organization}; Doe, Jane")
        assert export == "{Food and Agriculture Organization} and Doe, Jane"

    def test_datacite_braces_organizational_and_untyped_organizations(self):
        assert datacite._people([
            {"name": "Food and Agriculture Organization", "nameType": "Organizational"},
            {"name": "Weber, Clara", "nameType": "Personal", "givenName": "Clara", "familyName": "Weber"},
            {"name": "Bundesanstalt für Wasserbau"},
            {"name": "Doe, Jane"},
        ]) == "{Food and Agriculture Organization}; Weber, Clara; {Bundesanstalt für Wasserbau}; Doe, Jane"

    def test_open_library_braces_an_organization(self):
        rec = openlibrary_client.parse_openlibrary(
            {"title": "T", "authors": [{"name": "Food and Agriculture Organization"}, {"name": "Jane Doe"}]}, "x")
        assert rec["authors"] == "{Food and Agriculture Organization}; Doe, Jane"

    def test_organization_braces_once(self):
        assert author_names.organization("  ACME  Consortium ") == "{ACME Consortium}"
        assert author_names.organization("{ACME Consortium}") == "{ACME Consortium}"
        assert author_names.organization("") == ""


# ---------------------------------------------------------------------------
# 2. One emptiness rule
# ---------------------------------------------------------------------------

class TestEmptiness:
    def test_one_rule_shared_by_lookup_and_fields(self):
        assert bib_lookup.is_empty is bib_fields.is_empty
        assert bib_fields.is_empty("year", 0) and bib_fields.is_empty("year", "0")
        assert bib_fields.is_empty("authors", " , ;") and bib_fields.is_empty("editors", ";")
        assert not bib_fields.is_empty("year", 2020)

    def test_year_zero_and_separator_only_people_are_missing(self):
        paper = {"item_type": "article", "title": "T", "authors": ", ;", "journal": "J",
                 "year": 0, "pub_date": "", "volume": "1", "pages": "1-2"}
        assert bib_fields.missing_fields(paper) == ["authors", "year"]

    def test_the_export_writes_no_year_zero_and_no_empty_people(self):
        paper = {"item_type": "article", "cite_key": "K", "title": "T", "authors": ", ;",
                 "editors": " ; ", "journal": "J", "year": 0}
        for dialect in ("biblatex", "bibtex"):
            entry = bibtex_builder.format_entry(paper, dialect=dialect)
            assert "year" not in entry and "author" not in entry and "editor" not in entry
        dated = bibtex_builder.format_entry({**paper, "pub_date": "2021-03"}, dialect="biblatex")
        assert "  date = {2021-03}," in dated   # a year of 0 does not beat the date


# ---------------------------------------------------------------------------
# 3. A patent's holder
# ---------------------------------------------------------------------------

class TestPatentHolder:
    def test_the_label_names_what_the_export_writes(self):
        assert bib_fields.ITEM_TYPES["patent"].label_ids()["institution"] == "holder"
        en = (ROOT / "static/locales/core.en.js").read_text(encoding="utf-8")
        de = (ROOT / "static/locales/core.de.js").read_text(encoding="utf-8")
        assert '"bib.label.holder": "Holder",' in en
        assert '"bib.label.holder": "Inhaber:in",' in de
        assert "patentOffice" not in en and "patentOffice" not in de

    @pytest.mark.parametrize("institution, holder", [
        ("Robert Bosch GmbH", "{Robert Bosch GmbH}"),
        ("Food and Agriculture Organization", "{Food and Agriculture Organization}"),
        ("Müller, Schmidt & Partner GmbH", "{Müller, Schmidt \\& Partner GmbH}"),
        ("{Ingenieurbüro Krause}", "{Ingenieurbüro Krause}"),
        ("Müller, Hans; Siemens AG", "Müller, Hans and {Siemens AG}"),
        ("Hans Müller", "Müller, Hans"),
    ])
    def test_an_organization_holder_stays_one_braced_name(self, institution, holder):
        assert bibtex_builder.holder_field(institution) == holder.replace("ü", '{\\"u}')
        paper = {"item_type": "patent", "cite_key": "P", "title": "T", "number": "DE 1",
                 "institution": institution, "year": 2020}
        assert f"  holder = {{{bibtex_builder.holder_field(institution)}}}," in \
            bibtex_builder.format_entry(paper, dialect="biblatex")


# ---------------------------------------------------------------------------
# 4. Enrichment: only incomplete Items, off the event loop
# ---------------------------------------------------------------------------

@pytest.mark.real_enrichment
def test_a_complete_item_asks_no_source(db, monkeypatch):
    monkeypatch.setattr(paper_ingest, "_bib_record",
                        MagicMock(side_effect=AssertionError("a complete Item asks no source")))
    pid = _seed(db, doi="10.1/complete", item_type="article", title="T", authors="Doe, Jane",
                journal="J", year=2020, volume="1", pages="1-2", abstract="Known.")
    assert paper_ingest.enrich_imported_papers([{"paper_id": pid, "doi": "10.1/complete"}], works={}) == 0


@pytest.mark.real_enrichment
def test_an_item_missing_a_required_field_asks(db, monkeypatch):
    asked = []
    monkeypatch.setattr(paper_ingest, "_bib_record", lambda doi, isbn: asked.append(doi) or {
        "fields": {"pages": "1-2"}, "sources": {"pages": "crossref"}})
    pid = _seed(db, doi="10.1/lacking", item_type="article", title="T", authors="Doe, Jane",
                journal="J", year=2020, volume="1", abstract="Known.")
    assert paper_ingest.enrich_imported_papers([{"paper_id": pid, "doi": "10.1/lacking"}], works={}) == 1
    assert asked == ["10.1/lacking"] and _row(db, pid)["pages"] == "1-2"


def test_the_bibtex_commit_enriches_off_the_event_loop(client, monkeypatch):
    seen = {}

    def enrich(created, works=None):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return 0

    monkeypatch.setattr(paper_ingest, "enrich_imported_papers", enrich)
    body = {"source_name": "x.bib", "entries": [{"key": "Off2020", "title": "Off the loop", "authors": "Doe, Jane",
                                                 "year": 2020, "doi": "", "isbn": "", "abstract": "",
                                                 "journal": "", "publisher": "", "entry_type": "misc",
                                                 "raw": {}}]}
    assert client.post("/api/import/bibtex/commit", json=body).status_code == 200
    assert seen == {"on_loop": False}


# ---------------------------------------------------------------------------
# 5. A cleared date clears the year
# ---------------------------------------------------------------------------

class TestClearedDate:
    def test_checked_changes_clears_the_year_with_the_date(self):
        assert bib_fields.checked_changes({"pub_date": ""}, {"year": 2020, "pub_date": "2020-03"}) == {
            "pub_date": "", "year": ""}
        assert bib_fields.checked_changes({"pub_date": "", "year": 2019}, {}) == {"pub_date": "", "year": 2019}
        assert bib_fields.valid_changes({"pub_date": ""}, {}) == {"pub_date": "", "year": None}

    def test_validate_apply_stores_no_year_as_null(self, client, db):
        pid = _seed(db, title="T", year=2020, pub_date="2020-03")
        assert client.post(f"/api/papers/{pid}/validate/apply",
                           json={"changes": {"pub_date": ""}}).status_code == 200
        row = _row(db, pid)
        assert (row["pub_date"], row["year"]) == ("", None)


# ---------------------------------------------------------------------------
# 6. Valid dates only
# ---------------------------------------------------------------------------

class TestValidDates:
    @pytest.mark.parametrize("text, expected", [
        ("Marine engineering 2009", (2009, "")),
        ("Feb 30, 2009", (2009, "2009-02")),
        ("2009-02-30", (2009, "2009-02")),
        ("2009-13", (2009, "")),
        ("Sept. 2009", (2009, "2009-09")),
        ("März 2009", (2009, "2009-03")),
        ("Sep 15, 2016", (2016, "2016-09-15")),
    ])
    def test_open_library_dates(self, text, expected):
        assert openlibrary_client._date(text) == expected

    def test_month_names_are_whole_words(self):
        assert bib_fields.month_number("Marine") is None
        assert bib_fields.month_number("Mayday") is None
        assert (bib_fields.month_number("March"), bib_fields.month_number("mar"),
                bib_fields.month_number("Sept."), bib_fields.month_number("Dezember")) == (3, 3, 9, 12)

    def test_a_lookup_never_returns_an_invalid_value(self):
        rec = bib_lookup.fetch(doi="10.1/x", crossref_fetch=lambda d: {
            "title": "T", "pub_date": "2021-02-30", "accessed": "yesterday", "item_type": "poem", "year": 2021})
        assert rec["fields"] == {"title": "T", "year": 2021}
        out = bib_lookup.compare({}, {"pub_date": "spring", "item_type": "poem", "volume": "3"})
        assert out["fills"] == {"volume": "3"}

    @pytest.mark.real_enrichment
    def test_enrichment_writes_through_the_write_rules(self, db, monkeypatch):
        monkeypatch.setattr(paper_ingest, "_bib_record", lambda doi, isbn: {"fields": {}, "sources": {}})
        monkeypatch.setattr(bib_lookup, "compare", lambda *a, **k: {
            "fills": {"pub_date": "2021-3", "item_type": "poem", "accessed": "spring", "volume": "3"},
            "conflicts": [], "sources": {}})
        pid = _seed(db, doi="10.1/rules", title="T")
        assert paper_ingest.enrich_imported_papers([{"paper_id": pid, "doi": "10.1/rules"}], works={}) == 1
        row = _row(db, pid)
        assert (row["pub_date"], row["year"], row["volume"]) == ("2021-03", 2021, "3")
        assert (row["item_type"], row["accessed"]) == ("", "")


# ---------------------------------------------------------------------------
# 7. PUT renames only on a real change
# ---------------------------------------------------------------------------

class TestRenameOnRealChange:
    def test_a_date_of_the_stored_year_renames_nothing(self, client, db):
        name = _pdf("keep_this_name.pdf")
        pid = _seed(db, title="Bridges", authors="Doe, Jane", year=2011, filename=name)
        rebuild = MagicMock()
        with patch.object(papers_router, "_rebuild_all_symlinks", rebuild):
            assert client.put(f"/api/papers/{pid}", json={"pub_date": "2011-05"}).status_code == 200
            assert client.put(f"/api/papers/{pid}", json={"title": "Bridges"}).status_code == 200
        assert _row(db, pid)["filename"] == name
        rebuild.assert_not_called()

    def test_a_new_title_renames(self, client, db):
        name = _pdf("rename_me.pdf")
        pid = _seed(db, title="Bridges", authors="Doe, Jane", year=2011, filename=name)
        with patch.object(papers_router, "_rebuild_all_symlinks", MagicMock()) as rebuild:
            client.put(f"/api/papers/{pid}", json={"title": "Tunnels"})
        assert _row(db, pid)["filename"] != name
        rebuild.assert_called_once()

    def test_name_fields_changed(self):
        stored = {"title": "T", "authors": "A", "year": 2011}
        assert not papers_router._name_fields_changed({"year": 2011}, stored)
        assert not papers_router._name_fields_changed({"title": " T "}, stored)
        assert papers_router._name_fields_changed({"year": 2012}, stored)
        assert papers_router._name_fields_changed({"year": None}, stored)


# ---------------------------------------------------------------------------
# 8. One copy of the brace helpers
# ---------------------------------------------------------------------------

def test_bibtex_import_uses_the_author_names_helpers():
    assert not hasattr(bibtex_import, "_split_and") and not hasattr(bibtex_import, "_is_braced")
    assert bibtex_import.split_outside is author_names.split_outside
    assert bibtex_import.is_braced is author_names.is_braced
    assert bibtex_import.format_authors("{Müller, Schmidt and Partner} and Doe, Jane") == \
        "{Müller, Schmidt and Partner}; Doe, Jane"


# ---------------------------------------------------------------------------
# 9. Refresh Review gap fills
# ---------------------------------------------------------------------------

def _propose(client, **body):
    with patch.object(rr, "fetch_crossref_metadata", return_value=dict(CHAPTER_RECORD)):
        return _events(client.post("/api/maintenance/refresh-review/propose", json=body))


class TestRefreshReviewFills:
    def test_fills_rename_nothing_and_rebuild_no_symlinks(self, client, db):
        name = _pdf("unknown_item.pdf")
        pid = _seed(db, doi=CHAPTER_DOI, filename=name)
        rebuild = MagicMock()
        with patch.object(rr, "_rebuild_all_symlinks", rebuild), \
             patch.object(validation_policy, "_rebuild_all_symlinks", rebuild), \
             patch.object(rr, "extract_text_from_pdf", return_value=""):
            done = _propose(client, scopes=["bibliographic"], only_gaps=False)[-1]
        row = _row(db, pid)
        assert (row["title"], row["year"], row["item_type"]) == (CHAPTER_RECORD["title"], 2022, "chapter")
        assert row["filename"] == name
        rebuild.assert_not_called()
        assert done["stats"]["filled_items"] == 1

    def test_a_stored_type_is_never_filled_over(self, db):
        pid = _seed(db, doi=CHAPTER_DOI, item_type="article", title="T")
        stored = _row(db, pid)
        written = rr._write_fills(stored, {"item_type": "chapter", "pages": "1-2", "title": "Other"})
        assert written == {"pages": "1-2"}
        row = _row(db, pid)
        assert (row["item_type"], row["pages"], row["title"]) == ("article", "1-2", "T")

    def test_a_year_fill_never_clears_a_stored_date(self, db):
        pid = _seed(db, doi="10.1/dated", title="T", pub_date="2020-03")
        assert rr._write_fills(_row(db, pid), {"year": 2021}) == {}
        assert _row(db, pid)["pub_date"] == "2020-03"

    def test_every_progress_event_counts_the_fills_so_far(self, client, db):
        _seed(db, doi=CHAPTER_DOI)
        _seed(db, doi="10.1/second", title="Second")
        events = _propose(client, scopes=["bibliographic"], only_gaps=False)
        progress = [e for e in events if e["type"] == "progress"]
        assert len(progress) == 2
        assert progress[0]["filled"] > 0 and progress[0]["filled_items"] == 1
        assert progress[-1]["filled"] == events[-1]["stats"]["filled"]
        assert progress[-1]["filled_items"] == events[-1]["stats"]["filled_items"]
