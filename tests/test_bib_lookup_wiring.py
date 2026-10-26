"""Bibliographic lookup wired in (Ticket 2): the single-Item endpoint, the
Refresh Review scope ``bibliographic``, the DOI/BibTeX intake enrichment and
the PDF pipeline.

CrossRef/DataCite/Open Library answer through respx with the recorded shapes
in ``tests/fixtures/bib_sources`` or through a patched fetcher; nothing
reaches the network.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

import paper_ingest
import routers.refresh_review as rr
from openalex_client import BASE_URL as OPENALEX

FIXTURES = Path(__file__).parent / "fixtures" / "bib_sources"
CROSSREF = "https://api.crossref.org/works/"
ARTICLE_DOI = "10.1080/15732479.2020.1832539"
CHAPTER_DOI = "10.1007/978-3-030-91320-5_7"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _seed(db, **values) -> int:
    row = {"file_hash": f"h-{values.get('doi') or values.get('isbn') or values.get('title')}",
           "filename": "", "original_filename": "", "title": "", "authors": "", "year": None,
           "doi": "", "isbn": "", "journal": "", "abstract": "", **values}
    cols = ", ".join(row)
    conn = db._connect()
    try:
        cur = conn.execute(f"INSERT INTO papers ({cols}) VALUES ({', '.join('?' * len(row))})",
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
    return [json.loads(l[6:]) for l in resp.text.split("\n") if l.strip().startswith("data: ")]


# ---------------------------------------------------------------------------
# POST /api/papers/{id}/metadata/lookup
# ---------------------------------------------------------------------------

class TestLookupEndpoint:
    @respx.mock
    def test_fills_and_conflicts_without_writing_anything(self, client, db):
        pid = _seed(db, doi=ARTICLE_DOI, title="Shear capacity of unstabilised earth masonry walls",
                    authors="Schneider, Anna, Chen, Jun, van der Berg, Pieter", year=2020)
        respx.get(CROSSREF + ARTICLE_DOI).mock(
            return_value=httpx.Response(200, json=_fixture("crossref_article.json")))
        before = _row(db, pid)

        resp = client.post(f"/api/papers/{pid}/metadata/lookup")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["found"] is True
        fills = body["fills"]
        assert fills["item_type"] == "article"
        assert fills["journal"] == "Structure and Infrastructure Engineering"
        assert (fills["volume"], fills["issue"], fills["pages"]) == ("17", "3", "121-139")
        assert fills["issn"] == "1573-2479"
        assert "pub_date" not in fills  # 2021-02 disagrees with the stored year
        assert "title" not in fills and "authors" not in fills  # same work, same names
        assert body["conflicts"] == [{"field": "year", "current": 2020, "found": 2021,
                                      "source": "crossref"}]
        assert body["sources"]["journal"] == "crossref"
        assert _row(db, pid) == before  # writes nothing

    @respx.mock
    @pytest.mark.real_bib_sources
    def test_a_book_without_doi_asks_open_library_and_needs_no_pdf(self, client, db):
        pid = _seed(db, isbn="978-0-262-03384-8", title="")
        respx.get("https://openlibrary.org/api/books").mock(
            return_value=httpx.Response(200, json=_fixture("openlibrary_book.json")))
        body = client.post(f"/api/papers/{pid}/metadata/lookup").json()
        assert body["fills"]["item_type"] == "book"
        assert body["fills"]["publisher"] == "MIT Press"
        assert body["fills"]["place"] == "Cambridge, Mass"
        assert body["fills"]["year"] == 2009
        assert body["sources"]["title"] == "openlibrary"
        assert body["conflicts"] == []  # the stored ISBN is the same book

    @respx.mock
    def test_a_doi_typed_but_not_saved_yet_is_asked(self, client, db):
        pid = _seed(db, title="Draft")
        respx.get(CROSSREF + ARTICLE_DOI).mock(
            return_value=httpx.Response(200, json=_fixture("crossref_article.json")))
        body = client.post(f"/api/papers/{pid}/metadata/lookup", json={"doi": ARTICLE_DOI}).json()
        assert body["fills"]["doi"] == ARTICLE_DOI
        assert [c["field"] for c in body["conflicts"]] == ["title"]

    def test_errors_are_codes(self, client, db):
        assert client.post("/api/papers/999999/metadata/lookup").json()["detail"] == "error.item_not_found"
        pid = _seed(db, title="No identifier")
        resp = client.post(f"/api/papers/{pid}/metadata/lookup")
        assert resp.status_code == 422
        assert resp.json()["detail"] == "error.lookup_no_identifier"

    def test_nothing_known_is_an_empty_answer(self, client, db):
        pid = _seed(db, doi="10.5555/unknown")
        with patch("crossref.fetch_crossref_metadata", return_value=None):
            body = client.post(f"/api/papers/{pid}/metadata/lookup").json()
        assert body == {"paper_id": pid, "found": False, "fills": {}, "conflicts": [], "sources": {},
                        "source_status": {"crossref": "not_found", "datacite": "not_found",
                                          "openlibrary": "no_identifier"}}


# ---------------------------------------------------------------------------
# Refresh Review scope "bibliographic"
# ---------------------------------------------------------------------------

CHAPTER_RECORD = {
    "title": "Life-Cycle Cost Analysis of Bridge Superstructures", "authors": "Hoffmann, Lena; Becker, Tom",
    "year": 2022, "doi": CHAPTER_DOI, "abstract": "", "journal": "",
    "publisher": "Springer International Publishing", "isbn": "9783030913199",
    "item_type": "chapter", "booktitle": "Life-Cycle of Structures and Infrastructure Systems",
    "editors": "Biondini, Fabio; Frangopol, Dan M.", "pages": "101-118", "place": "Cham",
}


def _propose(client, **body):
    no_pdf = MagicMock(side_effect=AssertionError("the bibliographic scope reads no PDF"))
    with patch.object(rr, "fetch_crossref_metadata", return_value=dict(CHAPTER_RECORD)), \
         patch.object(rr, "extract_text_from_pdf", no_pdf):
        return _events(client.post("/api/maintenance/refresh-review/propose", json=body))[-1]


class TestRefreshReviewBibliographic:
    def test_gaps_are_written_differing_values_become_rows(self, client, db):
        pid = _seed(db, doi=CHAPTER_DOI, title="LCC of bridge superstructures",
                    authors="Hoffmann, L.; Becker, T.", year=2022)
        done = _propose(client, scopes=["bibliographic"], only_gaps=False)

        row = _row(db, pid)
        assert (row["item_type"], row["booktitle"], row["pages"], row["place"]) == (
            "chapter", CHAPTER_RECORD["booktitle"], "101-118", "Cham")
        assert row["editors"] == "Biondini, Fabio; Frangopol, Dan M."
        assert row["publisher"] == "Springer International Publishing"
        assert row["title"] == "LCC of bridge superstructures"  # never overwritten
        assert done["stats"]["filled"] >= 7 and done["stats"]["filled_items"] == 1
        rows = {r["field"]: r for r in done["rows"]}
        assert set(rows) == {"title", "authors"}
        assert rows["title"]["before"] == "LCC of bridge superstructures"
        assert rows["title"]["after"] == CHAPTER_RECORD["title"]
        assert rows["title"]["source"] == "crossref"
        assert rows["title"]["kind"] == "field"

    def test_a_ticked_row_applies_a_stale_one_is_skipped(self, client, db):
        pid = _seed(db, doi=CHAPTER_DOI, title="LCC of bridge superstructures", year=2022)
        rows = {r["field"]: r for r in _propose(client, scopes=["bibliographic"], only_gaps=False)["rows"]}
        title = rows["title"]
        assert client.post("/api/maintenance/refresh-review/apply", json={"rows": [title]}).json() == {
            "applied": 1, "skipped": []}
        assert _row(db, pid)["title"] == CHAPTER_RECORD["title"]
        # The same row again: the title is no longer what the preview saw.
        again = client.post("/api/maintenance/refresh-review/apply", json={"rows": [title]}).json()
        assert again["skipped"] == [{"id": title["id"], "reason": "changed"}]

    def test_year_is_a_row_never_a_write(self, client, db):
        pid = _seed(db, doi=CHAPTER_DOI, title=CHAPTER_RECORD["title"], year=2021)
        done = _propose(client, scopes=["bibliographic"], only_gaps=False)
        assert _row(db, pid)["year"] == 2021
        assert [(r["field"], r["before"], r["after"]) for r in done["rows"]] == [("year", 2021, 2022)]

    def test_only_gaps_takes_items_missing_required_fields_with_an_identifier(self, client, db):
        complete = _seed(db, doi="10.1/complete", item_type="article", title="T", authors="A, B",
                         journal="J", year=2020, volume="1", pages="1-2")
        lacking = _seed(db, doi=CHAPTER_DOI)  # no title: misc lacks its one required field
        _seed(db, title="No identifier at all")
        count = client.post("/api/maintenance/refresh-review/count",
                            json={"scopes": ["bibliographic"]}).json()["count"]
        assert count == 1
        done = _propose(client, scopes=["bibliographic"])
        assert done["stats"]["items"] == 1
        assert _row(db, lacking)["item_type"] == "chapter"
        assert _row(db, complete)["booktitle"] == ""
        everything = client.post("/api/maintenance/refresh-review/count",
                                 json={"scopes": ["bibliographic"], "only_gaps": False}).json()["count"]
        assert everything == 2


# ---------------------------------------------------------------------------
# Intake: DOI per API and BibTeX import enrichment
# ---------------------------------------------------------------------------

@pytest.mark.real_enrichment
@respx.mock
def test_a_doi_intake_comes_in_complete(client, db):
    respx.get(f"{OPENALEX}/works").mock(return_value=httpx.Response(200, json={"results": []}))
    respx.get(CROSSREF + CHAPTER_DOI).mock(
        return_value=httpx.Response(200, json=_fixture("crossref_book_chapter.json")))
    body = client.post("/api/papers/by-doi", json={"doi": CHAPTER_DOI}).json()
    row = _row(db, body["paper_id"])
    assert (row["item_type"], row["booktitle"], row["series"]) == (
        "chapter", "Life-Cycle of Structures and Infrastructure Systems", "Lecture Notes in Civil Engineering")
    assert row["editors"] == "Biondini, Fabio; Frangopol, Dan M."
    assert row["authors"] == "Hoffmann, Lena; Becker, Tom"
    assert (row["pages"], row["place"], row["year"]) == ("101-118", "Cham", 2022)
    assert row["isbn"] == "9783030913199, 9783030913205"
    assert row["journal"] == ""
    assert body["citekey"] == row["cite_key"] == "Hoffmann2022"


@pytest.mark.real_enrichment
@pytest.mark.real_bib_sources
@respx.mock
def test_an_arxiv_doi_is_named_by_datacite(client, db):
    respx.get(f"{OPENALEX}/works").mock(return_value=httpx.Response(200, json={"results": []}))
    respx.get("https://api.datacite.org/dois/10.48550/arxiv.2108.08090").mock(
        return_value=httpx.Response(200, json=_fixture("datacite_arxiv.json")))
    body = client.post("/api/papers/by-doi", json={"doi": "10.48550/arXiv.2108.08090"}).json()
    row = _row(db, body["paper_id"])
    assert (row["title"], row["item_type"], row["eprint"], row["version"]) == (
        "Graph Surrogates for Structural Health Monitoring", "preprint", "2108.08090", "2")
    assert row["authors"] == "Okafor, Ada; Lund, Per"


@pytest.mark.real_enrichment
def test_enrichment_by_isbn_fills_a_book_and_keeps_given_values(db, monkeypatch):
    monkeypatch.setattr("openlibrary_client.fetch_openlibrary_metadata", lambda isbn: {
        "item_type": "book", "title": "Introduction to algorithms", "publisher": "MIT Press",
        "place": "Cambridge, Mass", "year": 2009, "isbn": "9780262033848"})
    pid = _seed(db, isbn="9780262033848", title="Introduction to Algorithms, 3rd ed.")
    assert paper_ingest.enrich_imported_papers([{"paper_id": pid, "doi": ""}], works={}) == 1
    row = _row(db, pid)
    assert (row["item_type"], row["publisher"], row["place"], row["year"]) == (
        "book", "MIT Press", "Cambridge, Mass", 2009)
    assert row["title"] == "Introduction to Algorithms, 3rd ed."
