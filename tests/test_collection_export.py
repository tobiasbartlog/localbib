"""The pure export building block (#202): edge cases the HTTP seam makes
clumsy — Cite Key collisions in the ZIP, Items without a Cite Key, an empty
Collection, and the CSV's Excel-safety."""
from __future__ import annotations

import csv
import io

from services import collection_export as ex


def _p(pid, key="", **extra):
    return {"id": pid, "cite_key": key, "title": f"T{pid}", **extra}


def test_colliding_cite_keys_get_suffixes_case_insensitively():
    names = ex.pdf_member_names([_p(1, "doe2020"), _p(2, "Doe2020"), _p(3, "doe2020")])
    assert names == {1: "doe2020.pdf", 2: "Doe2020-2.pdf", 3: "doe2020-3.pdf"}


def test_item_without_cite_key_falls_back_to_legacy_key_or_id():
    names = ex.pdf_member_names([
        _p(1, "", authors="Müller, A.; Roe, B.", year=2019),
        _p(2, ""),
        _p(3, "a/b:c"),
    ])
    assert names[1] == "Muller2019.pdf"
    assert names[2] == "item-2.pdf"
    assert names[3] == "a_b_c.pdf"


def test_zip_plan_splits_pdfs_and_missing():
    plan = ex.zip_plan([_p(1, "a2020", year=2020), _p(2, "b2021"), _p(3, "a2020")], [1, 3])
    assert plan.pdfs == [(1, "a2020.pdf"), (3, "a2020-2.pdf")]
    assert [p["id"] for p in plan.missing] == [2]
    assert plan.missing_text == "b2021\tT2\r\n"


def test_empty_collection_plan_and_csv():
    plan = ex.zip_plan([], [])
    assert plan.pdfs == [] and plan.missing == [] and plan.missing_text == ""
    data = ex.csv_bytes([], notes={})
    text = data.decode("utf-8")
    assert text.startswith("﻿")
    assert text.strip("﻿\r\n").split(",")[-1] == ex.NOTE_COLUMN


def test_csv_neutralises_formulas_and_keeps_umlauts():
    data = ex.csv_bytes([_p(1, "k", title="=HYPERLINK(\"x\")", authors="Größe, Ä.")],
                        custom_fields=[{"id": 7, "name": "Status"}],
                        custom_values={1: {7: "+1"}})
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    assert rows[0]["title"].startswith("'=")
    assert rows[0]["authors"] == "Größe, Ä."
    assert rows[0]["Status"] == "'+1"
    assert "collection_note" not in rows[0]


def test_safe_basename():
    assert ex.safe_basename("Paper X: Klima/Ökologie") == "Paper_X_Klima_Okologie"
    assert ex.safe_basename("Größe") == "Grosse"
    assert ex.safe_basename("  ") == ex.DEFAULT_BASENAME
