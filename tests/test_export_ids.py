"""Export of an explicit Item list (#212, PRD #206).

``POST /api/export/{ris|bibtex|csv|zip}`` takes ``{ids: [...]}`` in the body
(long lists do not fit a URL) and answers with the same files as the GET forms,
holding exactly those Items. The old ``POST /api/import/ris`` is gone: RIS
files come in through the migration wizard.
"""
from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import pytest

from literature_manager import Config

PDF = b"%PDF-1.4 ids"


def _seed(db, title, *, key, filename="", year=2020):
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title, authors, "
            "year, cite_key) VALUES (?, ?, ?, ?, 'Doe, J.', ?, ?)",
            (f"h-{title}", filename, filename or title, title, year, key),
        )
        conn.commit()
        pid = cur.lastrowid
    finally:
        conn.close()
    if filename:
        (Path(Config.ALL_DIR) / filename).write_bytes(PDF)
    return pid


@pytest.fixture
def items(db):
    a = _seed(db, "Alpha", key="doe2020", filename="ids_a.pdf")
    b = _seed(db, "Beta", key="roe2021", year=2021)
    c = _seed(db, "Gamma", key="out2019", filename="ids_c.pdf", year=2019)
    return {"a": a, "b": b, "c": c}


def test_bibtex_holds_exactly_the_listed_items(client, items):
    resp = client.post("/api/export/bibtex", json={"ids": [items["a"], items["b"]]})
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].endswith(".bib")
    assert "doe2020" in resp.text and "roe2021" in resp.text
    assert "out2019" not in resp.text


def test_ris_holds_exactly_the_listed_items(client, items):
    resp = client.post("/api/export/ris", json={"ids": [items["c"]]})
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].endswith(".ris")
    assert resp.text.count("TY  - ") == 1
    assert "Gamma" in resp.text and "Alpha" not in resp.text


def test_csv_holds_exactly_the_listed_items(client, items):
    resp = client.post("/api/export/csv", json={"ids": [items["a"], items["c"]]})
    assert resp.status_code == 200
    assert resp.content.startswith("﻿".encode("utf-8"))
    rows = list(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))))
    assert {r["title"] for r in rows} == {"Alpha", "Gamma"}
    assert "collection_note" not in rows[0]


def test_zip_holds_the_listed_items_pdfs(client, items):
    resp = client.post("/api/export/zip", json={"ids": [items["a"], items["b"]]})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert "doe2020.pdf" in names and "out2019.pdf" not in names
    bib = next(n for n in names if n.endswith(".bib"))
    assert "out2019" not in zf.read(bib).decode("utf-8")
    missing = zf.read("fehlende_pdfs.txt").decode("utf-8")
    assert "roe2021" in missing and "doe2020" not in missing


def test_same_file_as_the_get_form(client, items):
    everything = [items["a"], items["b"], items["c"]]
    for fmt in ("bibtex", "ris", "csv"):
        assert client.post(f"/api/export/{fmt}", json={"ids": everything}).content \
            == client.get(f"/api/export/{fmt}").content


def test_unknown_ids_are_skipped_and_an_empty_list_is_an_empty_file(client, items):
    resp = client.post("/api/export/ris", json={"ids": [items["a"], 99999]})
    assert resp.text.count("TY  - ") == 1
    empty = client.post("/api/export/bibtex", json={"ids": []})
    assert empty.status_code == 200 and empty.text.strip() == ""


def test_unknown_format_and_bad_body(client, items):
    resp = client.post("/api/export/docx", json={"ids": [items["a"]]})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "error.export_format_unknown"
    assert client.post("/api/export/ris", json={"ids": "nope"}).status_code == 422


def test_the_old_ris_import_endpoint_is_gone(client):
    resp = client.post(
        "/api/import/ris",
        files={"file": ("x.ris", b"TY  - JOUR\nTI  - X\nER  -\n", "text/plain")},
    )
    assert resp.status_code in (404, 405)
