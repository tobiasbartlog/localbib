"""Analyses and export scoped to a Collection (#202, PRD #200 Seam 1).

Every endpoint that takes ``collection_id`` answers with exactly the
Collection's Items: BibTeX, RIS, CSV, ZIP, preview, stats and the citation
network build. Plus the one-click "take this DOI into the Collection" from the
network, with OpenAlex mocked via respx and CrossRef via patch.
"""
from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

import paper_ingest
import routers.analysis
from literature_manager import Config
from openalex_client import BASE_URL, Work

PDF = b"%PDF-1.4 scoped"


def _seed(db, title, *, key="", filename="", year=2020, doi="", journal="", authors="Doe, J."):
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title, authors, "
            "year, doi, journal, cite_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"h-{title}", filename, filename or title, title, authors, year, doi,
             journal, key),
        )
        conn.commit()
        pid = cur.lastrowid
    finally:
        conn.close()
    if filename:
        (Path(Config.ALL_DIR) / filename).write_bytes(PDF)
    return pid


def _exec(db, sql, args=()):
    conn = db._connect()
    try:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _collection(client, name, ids):
    resp = client.post("/api/collections", json={"name": name, "paper_ids": ids})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.fixture
def scoped(client, db):
    """Three Items, two of them in "Paper X" (one with a PDF on disk)."""
    a = _seed(db, "Alpha", key="doe2020", filename="sc_a.pdf", doi="10.1/a", journal="AiC")
    b = _seed(db, "Beta", key="roe2021", year=2021, doi="10.1/b", journal="AiC")
    c = _seed(db, "Gamma", key="out2019", year=2019, filename="sc_c.pdf")
    cid = _collection(client, "Paper X", [a, b])
    client.patch(f"/api/collections/{cid}/items/{a}", json={"note": "key method"})
    return {"a": a, "b": b, "c": c, "cid": cid}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def test_bibtex_ris_and_preview_take_exactly_the_collection(client, scoped):
    cid = scoped["cid"]
    bib = client.get(f"/api/export/bibtex?collection_id={cid}")
    assert bib.status_code == 200
    assert "Paper_X.bib" in bib.headers["content-disposition"]
    assert "doe2020" in bib.text and "roe2021" in bib.text
    assert "out2019" not in bib.text

    ris = client.get(f"/api/export/ris?collection_id={cid}").text
    assert ris.count("TY  - JOUR") == 2
    assert "Gamma" not in ris

    preview = client.get(f"/api/export/preview?collection_id={cid}").json()
    assert preview["count"] == 2
    assert preview["pdf_count"] == 1
    assert {p["id"] for p in preview["papers"]} == {scoped["a"], scoped["b"]}
    assert preview["collection"] == {"id": cid, "name": "Paper X"}

    # Without a scope: the whole Library, unchanged.
    assert client.get("/api/export/preview").json()["count"] == 3


def test_unknown_or_deleted_collection_is_a_404_everywhere(client, scoped):
    client.delete(f"/api/collections/{scoped['cid']}")
    for url in ("/api/export/bibtex", "/api/export/ris", "/api/export/csv",
                "/api/export/zip", "/api/export/preview", "/api/stats"):
        for cid in (scoped["cid"], 9999):
            resp = client.get(f"{url}?collection_id={cid}")
            assert resp.status_code == 404, url
            assert resp.json()["detail"] == "error.collection_not_found"
    resp = client.post(f"/api/analysis/build?collection_id={scoped['cid']}")
    assert resp.status_code == 404


def test_csv_has_bom_core_metadata_custom_fields_and_note(client, db, scoped):
    cat = _exec(db, "INSERT INTO categories (name) VALUES ('Robotics')")
    _exec(db, "INSERT INTO paper_categories (paper_id, category_id) VALUES (?, ?)", (scoped["a"], cat))
    field = _exec(db, "INSERT INTO custom_fields (name) VALUES ('Lesestatus')")
    _exec(db, "INSERT INTO paper_custom_values (paper_id, field_id, value) VALUES (?, ?, ?)",
          (scoped["a"], field, "gelesen – Übersicht"))

    resp = client.get(f"/api/export/csv?collection_id={scoped['cid']}")
    assert resp.status_code == 200
    assert resp.content.startswith("﻿".encode("utf-8"))
    rows = list(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))))
    assert {r["title"] for r in rows} == {"Alpha", "Beta"}
    alpha = next(r for r in rows if r["title"] == "Alpha")
    assert alpha["cite_key"] == "doe2020"
    assert alpha["categories"] == "Robotics"
    assert alpha["Lesestatus"] == "gelesen – Übersicht"
    assert alpha["collection_note"] == "key method"
    assert alpha["has_pdf"] == "yes"
    assert next(r for r in rows if r["title"] == "Beta")["has_pdf"] == "no"

    # Library-wide CSV: no note column.
    lib = list(csv.DictReader(io.StringIO(client.get("/api/export/csv").content.decode("utf-8-sig"))))
    assert len(lib) == 3
    assert "collection_note" not in lib[0]


def test_zip_holds_pdfs_by_cite_key_bib_ris_and_missing_list(client, scoped):
    resp = client.get(f"/api/export/zip?collection_id={scoped['cid']}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = set(zf.namelist())
    assert names == {"doe2020.pdf", "Paper_X.bib", "Paper_X.ris", "fehlende_pdfs.txt"}
    assert zf.read("doe2020.pdf") == PDF
    assert "roe2021" in zf.read("Paper_X.bib").decode("utf-8")
    missing = zf.read("fehlende_pdfs.txt").decode("utf-8")
    assert "roe2021" in missing and "Beta" in missing
    assert "doe2020" not in missing


def test_empty_collection_exports_empty_files(client, scoped):
    cid = _collection(client, "Leer", [])
    assert client.get(f"/api/export/preview?collection_id={cid}").json()["count"] == 0
    zf = zipfile.ZipFile(io.BytesIO(client.get(f"/api/export/zip?collection_id={cid}").content))
    assert sorted(zf.namelist()) == ["Leer.bib", "Leer.ris", "fehlende_pdfs.txt"]
    assert zf.read("fehlende_pdfs.txt") == b""
    rows = client.get(f"/api/export/csv?collection_id={cid}").content.decode("utf-8-sig").splitlines()
    assert len(rows) == 1  # header only


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def test_stats_cover_only_the_collection(client, scoped):
    stats = client.get(f"/api/stats?collection_id={scoped['cid']}").json()
    assert stats["scope"] == {"type": "collection", "id": scoped["cid"], "name": "Paper X"}
    assert stats["paper_count"] == 2
    assert stats["with_pdf"] == 1
    assert stats["with_doi"] == 2
    assert (stats["year_min"], stats["year_max"]) == (2020, 2021)
    assert stats["by_year"] == [{"year": 2020, "count": 1}, {"year": 2021, "count": 1}]
    assert stats["top_venues"] == [{"name": "AiC", "count": 2}]
    assert client.get("/api/stats").json()["paper_count"] == 3


# ---------------------------------------------------------------------------
# Citation network
# ---------------------------------------------------------------------------

def _oa_client(works):
    fake = MagicMock()
    fake.fetch_works_by_doi.side_effect = lambda dois: [w for w in works if w.doi in dois]
    fake.fetch_works_by_id.return_value = {}
    fake.fetch_work_by_title.return_value = None
    return fake


def test_network_seeds_are_the_collection_items(client, db, scoped, monkeypatch):
    d = _seed(db, "Delta", doi="10.1/d")  # in the Library, not in the Collection
    works = [
        Work(id="W-A", doi="10.1/a", title="Alpha", authors=[], year=2020,
             cited_by_count=1, referenced_works=["W-D"]),
        Work(id="W-B", doi="10.1/b", title="Beta", authors=[], year=2021,
             cited_by_count=1, referenced_works=[]),
        Work(id="W-D", doi="10.1/d", title="Delta", authors=[], year=2018,
             cited_by_count=1, referenced_works=[]),
    ]
    fake = _oa_client(works)
    fake.fetch_works_by_id.return_value = {"W-D": works[2]}
    monkeypatch.setattr(routers.analysis, "OpenAlexClient", lambda *a, **k: fake)
    import network_orchestrator
    monkeypatch.setattr(network_orchestrator.time, "sleep", lambda *a, **k: None)

    body = client.post(f"/api/analysis/build?depth=1&collection_id={scoped['cid']}").json()
    own = {n["paper_id"] for n in body["nodes"] if n["type"] == "own"}
    assert own == {scoped["a"], scoped["b"]}
    assert body["scope"] == {"type": "collection", "id": scoped["cid"], "name": "Paper X"}
    # Delta is in the Library but outside the Collection: an external neighbour.
    delta = next(n for n in body["nodes"] if n.get("doi") == "10.1/d")
    assert delta["type"] != "own"

    lib = client.post("/api/analysis/build?depth=1").json()
    assert lib["scope"] == {"type": "library"}
    assert {n["paper_id"] for n in lib["nodes"] if n["type"] == "own"} >= {scoped["a"], scoped["b"], d}


# ---------------------------------------------------------------------------
# DOI into the Collection
# ---------------------------------------------------------------------------

def test_existing_item_by_doi_is_assigned_not_recreated(client, db, scoped):
    resp = client.post(f"/api/collections/{scoped['cid']}/items/by-doi",
                       json={"doi": "https://doi.org/10.1/A"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"paper_id": scoped["a"], "doi": "10.1/a", "citekey": "doe2020",
                           "created": False, "added": False, "has_pdf": True}

    gamma = _seed(db, "Epsilon", doi="10.1/e")
    body = client.post(f"/api/collections/{scoped['cid']}/items/by-doi",
                       json={"doi": "10.1/e"}).json()
    assert body["paper_id"] == gamma
    assert body["created"] is False and body["added"] is True and body["has_pdf"] is False
    members = client.get(f"/api/papers?collection_id={scoped['cid']}").json()
    assert gamma in {p["id"] for p in members}


def test_doi_into_collection_rejects_empty_doi_and_unknown_collection(client, scoped):
    assert client.post(f"/api/collections/{scoped['cid']}/items/by-doi",
                       json={"doi": "  "}).json()["detail"] == "error.doi_empty"
    resp = client.post("/api/collections/9999/items/by-doi", json={"doi": "10.1/zz"})
    assert resp.status_code == 404


@pytest.mark.real_enrichment
@respx.mock
def test_new_doi_is_created_enriched_with_oa_pdf_and_assigned(client, scoped):
    respx.get(f"{BASE_URL}/works").mock(return_value=httpx.Response(200, json={"results": [{
        "id": "https://openalex.org/W9", "doi": "https://doi.org/10.5555/neighbour",
        "title": "Neighbour", "authorships": [{"author": {"display_name": "Nachbar, N."}}],
        "publication_year": 2017, "cited_by_count": 4, "referenced_works": [],
        "abstract_inverted_index": None,
        "best_oa_location": {"pdf_url": "https://repo.example.org/n.pdf", "landing_page_url": ""},
        "open_access": {"oa_url": ""},
    }]}))
    respx.get("https://repo.example.org/n.pdf").mock(return_value=httpx.Response(
        200, content=PDF, headers={"content-type": "application/pdf"}))
    with patch.object(paper_ingest, "fetch_crossref_metadata", return_value=None), \
         patch.object(paper_ingest, "categorize_with_llm", return_value=[]), \
         patch.object(paper_ingest, "extract_text_from_pdf", return_value="Text"):
        resp = client.post(f"/api/collections/{scoped['cid']}/items/by-doi",
                           json={"doi": "10.5555/neighbour", "title": "Neighbour"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] is True and body["added"] is True and body["has_pdf"] is True
    detail = client.get(f"/api/collections/{scoped['cid']}").json()
    assert detail["item_count"] == 3
    assert detail["pdf_count"] == 2
