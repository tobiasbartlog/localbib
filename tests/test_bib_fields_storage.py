"""Bibliographic columns end to end: schema + one-time backfill, PUT, the
type table endpoint, the dialect setting, and the imports that fill them."""
from __future__ import annotations

import csv
import io
import json

import pytest

import bib_fields
import host_services
from database import BIB_BACKFILL_KEY, Database
from literature_manager import Config


# --- schema + backfill ----------------------------------------------------------

def _row(db, paper_id):
    conn = db._connect()
    try:
        return dict(conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone())
    finally:
        conn.close()


def _insert_raw(db, title, raw, **cols):
    conn = db._connect()
    try:
        names = ["file_hash", "filename", "original_filename", "title", "raw_metadata", *cols]
        values = [f"h-{title}", "", title, title, json.dumps(raw) if isinstance(raw, dict) else raw,
                  *cols.values()]
        cur = conn.execute(
            f"INSERT INTO papers ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})", values)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _rerun_backfill(db):
    conn = db._connect()
    try:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (BIB_BACKFILL_KEY,))
        conn.commit()
    finally:
        conn.close()
    return Database(db.db_path)


def test_every_bib_column_exists(tmp_path):
    db = Database(str(tmp_path / "x.db"))
    row_cols = set(_row(db, _insert_raw(db, "T", "")))
    assert set(bib_fields.BIB_COLUMNS) <= row_cols


def test_backfill_fills_empty_columns_once_and_never_overwrites(tmp_path):
    db = Database(str(tmp_path / "lib.db"))
    bib = _insert_raw(db, "Bib", {"ENTRYTYPE": "inproceedings", "booktitle": "Proc. X",
                                  "pages": "12--19", "volume": "4", "month": "mar", "year": "2011"})
    ris = _insert_raw(db, "Ris", {"TY": "JOUR", "VL": "7", "IS": "2", "SP": "1", "EP": "9"})
    kept = _insert_raw(db, "Kept", {"ENTRYTYPE": "article", "volume": "99", "pages": "1--2"},
                       volume="12", item_type="book")
    broken = _insert_raw(db, "Broken", "not json")
    listy = _insert_raw(db, "List", "[1, 2]")

    db = _rerun_backfill(db)
    row = _row(db, bib)
    assert (row["item_type"], row["booktitle"], row["pages"], row["volume"], row["pub_date"]) == (
        "conference", "Proc. X", "12--19", "4", "2011-03")
    row = _row(db, ris)
    assert (row["item_type"], row["volume"], row["issue"], row["pages"]) == ("article", "7", "2", "1-9")
    row = _row(db, kept)
    assert (row["item_type"], row["volume"], row["pages"]) == ("book", "12", "1--2")
    assert _row(db, broken)["item_type"] == "" and _row(db, listy)["item_type"] == ""

    # The marker makes it one run: a value cleared later is not filled again.
    conn = db._connect()
    try:
        conn.execute("UPDATE papers SET pages = '' WHERE id = ?", (bib,))
        conn.commit()
        assert conn.execute("SELECT value FROM app_settings WHERE key = ?",
                            (BIB_BACKFILL_KEY,)).fetchone()["value"] == "1"
    finally:
        conn.close()
    db = Database(db.db_path)
    assert _row(db, bib)["pages"] == ""

    # Run again without the marker: idempotent, still nothing overwritten.
    before = {pid: _row(db, pid) for pid in (ris, kept)}
    db = _rerun_backfill(db)
    assert {pid: _row(db, pid) for pid in (ris, kept)} == before


def test_add_paper_writes_bib_columns(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    pid = db.add_paper({"file_hash": "h", "filename": "", "original_filename": "x", "title": "T",
                        "authors": "", "year": 2020, "doi": "", "isbn": "", "abstract": "",
                        "journal": "", "publisher": "", "raw_metadata": "", "ocr_text": "",
                        "item_type": "standard", "number": "DIN 1"})
    row = _row(db, pid)
    assert (row["item_type"], row["number"], row["volume"]) == ("standard", "DIN 1", "")


# --- API ------------------------------------------------------------------------

def _paper(db, **cols):
    conn = db._connect()
    try:
        names = ["file_hash", "filename", "original_filename", "title", "authors", "year", "cite_key",
                 *cols]
        values = ["h-api", "", "x", "A Study", "Müller, Hans, Schmidt, Eva", 2020, "Mueller2020",
                  *cols.values()]
        cur = conn.execute(
            f"INSERT INTO papers ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})", values)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_item_types_endpoint_serves_the_table(client):
    resp = client.get("/api/papers/item-types")
    assert resp.status_code == 200
    data = resp.json()
    assert [t["id"] for t in data["types"]] == list(bib_fields.ITEM_TYPES)
    assert data["fields"] == list(bib_fields.WRITABLE_FIELDS)
    assert data["dialect"] in ("biblatex", "bibtex")


def test_put_writes_bib_fields_and_year_follows_pub_date(client, db):
    pid = _paper(db)
    resp = client.put(f"/api/papers/{pid}", json={
        "item_type": "article", "journal": "Bautechnik", "volume": " 101 ", "issue": "3",
        "pages": "145-152", "pub_date": "2024-3", "url": "https://x.org", "accessed": "2025-01-15",
    })
    assert resp.status_code == 200, resp.text
    paper = resp.json()
    assert (paper["item_type"], paper["volume"], paper["issue"], paper["pages"]) == (
        "article", "101", "3", "145-152")
    assert (paper["pub_date"], paper["year"], paper["accessed"]) == ("2024-03", 2024, "2025-01-15")
    assert paper["effective_type"] == "article" and paper["missing_fields"] == []

    # "" clears a bibliographic field to "" (TEXT DEFAULT ''). The form shows
    # the year in the date field: a cleared date without a year clears the
    # year too, stored as NULL (the library's "no year").
    paper = client.put(f"/api/papers/{pid}", json={"volume": "", "pub_date": ""}).json()
    assert (paper["volume"], paper["pub_date"], paper["year"]) == ("", "", None)
    assert paper["missing_fields"] == ["year", "volume"]


def test_a_cleared_date_with_a_year_beside_it_keeps_that_year(client, db):
    pid = _paper(db, pub_date="2024-03")
    client.put(f"/api/papers/{pid}", json={"pub_date": "2024-03"})
    paper = client.put(f"/api/papers/{pid}", json={"pub_date": "", "year": 2023}).json()
    assert (paper["pub_date"], paper["year"]) == ("", 2023)


def test_a_new_year_alone_clears_a_stored_date_of_another_year(client, db):
    pid = _paper(db, pub_date="2020-05")
    paper = client.put(f"/api/papers/{pid}", json={"year": 2020}).json()
    assert (paper["year"], paper["pub_date"]) == (2020, "2020-05")
    paper = client.put(f"/api/papers/{pid}", json={"year": 2021}).json()
    assert (paper["year"], paper["pub_date"]) == (2021, "")
    bib = client.get(f"/api/papers/{pid}/bibtex").json()["bibtex"]
    assert "  year = {2021}," in bib and "date = " not in bib


@pytest.mark.parametrize("body, code", [
    ({"item_type": "poster"}, "error.invalid_item_type"),
    ({"pub_date": "März 2024"}, "error.invalid_date"),
    ({"accessed": "2024-02-30"}, "error.invalid_date"),
])
def test_put_refuses_unknown_types_and_dates(client, db, body, code):
    pid = _paper(db)
    resp = client.put(f"/api/papers/{pid}", json=body)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == code
    assert _row(db, pid)["item_type"] == ""


def test_bibtex_endpoint_follows_the_dialect_setting(client, db, monkeypatch):
    pid = _paper(db, item_type="webpage", url="https://x.org", accessed="2025-01-15")
    monkeypatch.setattr(Config, "BIB_DIALECT", "biblatex")
    bib = client.get(f"/api/papers/{pid}/bibtex").json()["bibtex"]
    assert bib.startswith("@online{Mueller2020,") and "urldate = {2025-01-15}" in bib
    assert '{M{\\"u}ller, Hans and Schmidt, Eva}' in bib

    monkeypatch.setattr(Config, "BIB_DIALECT", "bibtex")
    monkeypatch.setattr(Config, "UI_LANGUAGE", "de")
    bib = client.get(f"/api/papers/{pid}/bibtex").json()["bibtex"]
    assert bib.startswith("@misc{Mueller2020,") and "note = {Zugriff am 2025-01-15}" in bib


def test_settings_accept_only_known_dialects(client, monkeypatch, tmp_path):
    import routers.settings as settings_mod
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings_mod, "_ENV_PATH", env)
    resp = client.put("/api/settings", json={"BIB_DIALECT": "apa"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "error.settings.invalidChoice"
    assert client.put("/api/settings", json={"BIB_DIALECT": "bibtex"}).status_code == 200
    assert "BIB_DIALECT=bibtex" in env.read_text(encoding="utf-8")
    monkeypatch.setattr(Config, "BIB_DIALECT", "bibtex")
    assert client.get("/api/settings").json()["BIB_DIALECT"] == "bibtex"


def test_csv_export_carries_the_bib_columns(client, db):
    pid = _paper(db, item_type="chapter", booktitle="Handbuch", pages="3-9")
    rows = list(csv.DictReader(io.StringIO(
        client.post("/api/export/csv", json={"ids": [pid]}).content.decode("utf-8-sig"))))
    assert (rows[0]["item_type"], rows[0]["booktitle"], rows[0]["pages"]) == ("chapter", "Handbuch", "3-9")


def test_reference_details_read_the_columns_first(client, db):
    _paper(db, volume="12", issue="3", pages="45-67", raw_metadata=json.dumps({"volume": "99"}))
    ref = host_services._CoreLibraryApi().get_reference("Mueller2020")
    assert (ref["volume"], ref["issue"], ref["pages"]) == ("12", "3", "45-67")


# --- imports --------------------------------------------------------------------

INPROCEEDINGS = r"""
@inproceedings{Chen.2011,
  title     = {Bridge monitoring},
  author    = {Chen, Wei and Li, Na},
  booktitle = {Proceedings of the IABSE Symposium},
  pages     = {12--19},
  address   = {London},
  year      = {2011},
  month     = sep,
}

@online{UNEP.2025,
  title   = {Global Status Report},
  author  = {{United Nations Environment Programme}},
  url     = {https://www.unep.org/report},
  urldate = {2025-01-15},
  year    = {2025},
}
"""


def test_inproceedings_survives_import_and_export(client, db, monkeypatch):
    preview = client.post("/api/import/bibtex/preview",
                          files={"bib_file": ("old.bib", INPROCEEDINGS, "text/plain")}).json()
    entries = [{k: e.get(k) for k in ("key", "title", "authors", "year", "doi", "isbn", "abstract",
                                      "journal", "publisher", "entry_type", "raw")}
               for e in preview["entries"]]
    for e in entries:
        e.update({k: v or "" for k, v in e.items() if k not in ("year", "raw")})
    resp = client.post("/api/import/bibtex/commit", json={"entries": entries, "source_name": "old.bib"})
    assert resp.status_code == 200, resp.text
    ids = {r["key"]: r["paper_id"] for r in resp.json()["results"]}

    row = _row(db, ids["Chen.2011"])
    assert (row["item_type"], row["booktitle"], row["journal"], row["pages"], row["pub_date"]) == (
        "conference", "Proceedings of the IABSE Symposium", "", "12--19", "2011-09")
    row = _row(db, ids["UNEP.2025"])
    assert (row["item_type"], row["url"], row["accessed"]) == ("webpage", "https://www.unep.org/report",
                                                              "2025-01-15")

    monkeypatch.setattr(Config, "BIB_DIALECT", "biblatex")
    bib = client.get(f"/api/papers/{ids['Chen.2011']}/bibtex").json()["bibtex"]
    assert bib.startswith("@inproceedings{Chen.2011,")
    assert "booktitle = {Proceedings of the IABSE Symposium}" in bib and "journal" not in bib
    assert "author = {Chen, Wei and Li, Na}" in bib and "pages = {12--19}" in bib
    bib = client.get(f"/api/papers/{ids['UNEP.2025']}/bibtex").json()["bibtex"]
    assert bib.startswith("@online{UNEP.2025,")
    assert "author = {{United Nations Environment Programme}}" in bib
    assert "url = {https://www.unep.org/report}" in bib and "urldate = {2025-01-15}" in bib


def test_migration_writes_ris_and_zotero_fields_into_columns():
    import ris_import
    from migration_items import MigrationOptions
    from routers.migration import _paper_data

    item = ris_import.to_items("TY  - CHAP\nTI  - Kapitel\nT2  - Handbuch\nVL  - 3\nSP  - 33\nEP  - 58\n"
                               "CY  - Berlin\nER  - \n")[0]
    data = _paper_data(item, MigrationOptions(), "x.ris")
    assert (data["item_type"], data["booktitle"], data["journal"], data["pages"], data["place"]) == (
        "chapter", "Handbuch", "", "33-58", "Berlin")
