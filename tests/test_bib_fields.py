"""bib_fields: the one table of Item types and fields, and the raw → column mapping."""
from __future__ import annotations

import pytest

import bib_fields
from bib_fields import (
    BIB_COLUMNS, ITEM_TYPES, WRITABLE_FIELDS, columns_from_raw, effective_type, import_columns,
    infer_type, missing_fields, normalize_date,
)


def test_the_table_names_only_known_fields():
    for item in ITEM_TYPES.values():
        assert item.fields[0] == "title", item.id
        assert set(item.fields) <= set(WRITABLE_FIELDS), item.id
        for group in item.required:
            assert set(group.split("|")) <= set(WRITABLE_FIELDS), item.id
        assert item.biblatex and item.bibtex


def test_the_requested_types_are_there():
    assert list(ITEM_TYPES) == ["article", "book", "chapter", "conference", "thesis", "report",
                                "preprint", "webpage", "standard", "patent", "dataset", "software", "misc"]
    assert WRITABLE_FIELDS[:8] == ("title", "authors", "year", "doi", "isbn", "abstract",
                                   "journal", "publisher")
    assert "item_type" in BIB_COLUMNS and "page_count" not in WRITABLE_FIELDS


def test_wire_table_is_plain_json():
    wire = bib_fields.wire_table()
    article = next(t for t in wire["types"] if t["id"] == "article")
    assert article["entry_types"] == {"biblatex": "article", "bibtex": "article"}
    assert "volume" in article["fields"] and "pages" in article["required"]
    assert wire["dialects"] == ["biblatex", "bibtex"]


@pytest.mark.parametrize("paper, kind", [
    ({"journal": "Nature"}, "article"),
    ({"isbn": "978", "publisher": "Springer"}, "book"),
    ({"isbn": "978"}, "misc"),
    ({}, "misc"),
    ({"thesis_type": "Dissertation", "journal": "x"}, "thesis"),
    ({"booktitle": "Proc. X"}, "conference"),
    ({"booktitle": "Handbuch", "editors": "Weber, K."}, "chapter"),
    ({"booktitle": "Handbuch", "isbn": "978"}, "chapter"),
])
def test_infer_type(paper, kind):
    assert infer_type(paper) == kind


def test_the_stored_type_wins_unless_unknown():
    assert effective_type({"item_type": "patent", "journal": "x"}) == "patent"
    assert effective_type({"item_type": "bogus", "journal": "x"}) == "article"
    assert effective_type({"item_type": "", "journal": "x"}) == "article"


def test_missing_fields():
    article = {"item_type": "article", "title": "T", "authors": "A, B", "journal": "J", "year": 2020}
    assert missing_fields(article) == ["volume", "pages"]
    assert missing_fields({**article, "year": None, "pub_date": "2020-03",
                           "volume": "1", "pages": "1-2"}) == []
    # either-or: a book needs authors OR editors
    book = {"item_type": "book", "title": "T", "publisher": "P", "year": 2000}
    assert missing_fields(book) == ["authors"]
    assert missing_fields({**book, "editors": "E, F"}) == []


@pytest.mark.parametrize("given, out", [
    ("2024", "2024"), ("2024-3", "2024-03"), ("2024-03-15", "2024-03-15"),
    ("2024/03/15/", "2024-03-15"), ("2024///", "2024"), ("2024-03-15T10:00:00Z", "2024-03-15"),
    ("2024-02-30", ""), ("2024-13", ""), ("March 2024", ""), ("", ""), (None, ""),
    ("2024-02-29", "2024-02-29"), ("2023-02-29", ""),
])
def test_normalize_date(given, out):
    assert normalize_date(given) == out


# --- raw import records -------------------------------------------------------

def test_bibtex_record():
    raw = {"ENTRYTYPE": "inproceedings", "ID": "Chen.2011", "title": "T", "booktitle": "Proc. IABSE",
           "editor": "Weber, Karl and Huber, Anna", "pages": "12--19", "address": "London",
           "year": "2011", "month": "sep", "eventtitle": "IABSE 2011", "url": "https://x.org",
           "urldate": "2024-01-02", "number": "4", "series": "LNCS", "note": "the reader's note"}
    assert columns_from_raw(raw) == {
        "item_type": "conference", "booktitle": "Proc. IABSE", "editors": "Weber, Karl; Huber, Anna",
        "pages": "12--19", "place": "London", "pub_date": "2011-09", "event": "IABSE 2011",
        "url": "https://x.org", "accessed": "2024-01-02", "number": "4", "series": "LNCS",
    }


def test_an_article_number_is_its_issue():
    cols = columns_from_raw({"ENTRYTYPE": "article", "volume": "12", "number": "3", "pages": "45--67"})
    assert cols == {"item_type": "article", "volume": "12", "issue": "3", "pages": "45--67"}


def test_a_thesis_type_comes_only_from_what_the_source_says():
    assert columns_from_raw({"ENTRYTYPE": "phdthesis", "school": "RWTH"}) == {
        "item_type": "thesis", "thesis_type": "phdthesis", "institution": "RWTH"}
    assert columns_from_raw({"ENTRYTYPE": "thesis", "type": "Masterarbeit"})["thesis_type"] == "Masterarbeit"
    assert "thesis_type" not in columns_from_raw({"ENTRYTYPE": "thesis"})


def test_biblatex_date_and_online():
    cols = columns_from_raw({"ENTRYTYPE": "online", "date": "2025-01-15", "url": "https://u.org",
                             "urldate": "2025-02-01", "organization": "UNEP"})
    assert cols == {"item_type": "webpage", "pub_date": "2025-01-15", "url": "https://u.org",
                    "accessed": "2025-02-01", "institution": "UNEP"}


def test_a_year_alone_is_no_pub_date():
    assert "pub_date" not in columns_from_raw({"ENTRYTYPE": "book", "year": "2019"})


def test_ris_record():
    raw = {"TY": "CHAP", "T2": "Handbuch", "VL": "3", "SP": "33", "EP": "58", "CY": "Berlin",
           "ET": "2", "T3": "Reihe", "UR": "https://x.org", "SN": "978-3-16-148410-0",
           "DA": "2020/05/01/", "LA": "de"}
    assert columns_from_raw(raw) == {
        "item_type": "chapter", "booktitle": "Handbuch", "volume": "3", "pages": "33-58",
        "place": "Berlin", "edition": "2", "series": "Reihe", "url": "https://x.org",
        "pub_date": "2020-05-01", "language": "de"}
    assert columns_from_raw({"TY": "JOUR", "SN": "0932-8351", "IS": "4"}) == {
        "item_type": "article", "issn": "0932-8351", "issue": "4"}


def test_an_old_ris_import_row():
    """``POST /api/import/ris`` (gone) stored ``type`` = the RIS code."""
    assert columns_from_raw({"type": "RPRT", "title": "T", "VL": "2"})["item_type"] == "report"
    # a BibTeX ``type`` is a thesis type, not an entry type
    assert "item_type" not in columns_from_raw({"type": "Dissertation"})
    # its other tags are RIS tags, read as such
    cols = columns_from_raw({"type": "JOUR", "title": "T", "VL": "7", "IS": "2", "SP": "1", "EP": "9"})
    assert (cols["volume"], cols["issue"], cols["pages"]) == ("7", "2", "1-9")
    # a BibTeX record whose type happens to be upper case stays BibTeX
    assert "volume" not in columns_from_raw({"ENTRYTYPE": "techreport", "type": "RPRT", "VL": "7"})


def test_a_repeated_ris_editor_tag_keeps_every_editor():
    cols = columns_from_raw({"TY": "CHAP", "ED": ["Weber, Karl", "Huber, Anna"], "T2": "Handbuch"})
    assert cols["editors"] == "Weber, Karl; Huber, Anna"


def test_zotero_record():
    raw = {"itemType": "bookSection", "volume": "2", "pages": "10-20", "date": "2019-06",
           "zotero:url": "https://z.org", "zotero:issn": "1234-5678",
           "zotero:editors": "Weber, Karl; Huber, Anna"}
    assert columns_from_raw(raw, "incollection") == {
        "item_type": "chapter", "volume": "2", "pages": "10-20", "pub_date": "2019-06",
        "url": "https://z.org", "issn": "1234-5678", "editors": "Weber, Karl; Huber, Anna"}
    # Zotero's own type is finer than the adapter's entry type
    assert columns_from_raw({"itemType": "preprint"}, "unpublished")["item_type"] == "preprint"


def test_a_crossref_metadata_dict_carries_nothing_bibliographic():
    assert columns_from_raw({"title": "T", "authors": "A, B", "doi": "10.1/x", "journal": "J"}) == {}


def test_import_columns_moves_a_book_title_out_of_journal():
    cols = import_columns("inproceedings", {"ENTRYTYPE": "inproceedings", "booktitle": "Proc. X"}, "Proc. X")
    assert cols["booktitle"] == "Proc. X" and cols["journal"] == ""
    # RIS T2 / Zotero isPartOf: only the journal names the container
    cols = import_columns("incollection", {"TY": "CHAP"}, "Handbuch")
    assert cols["booktitle"] == "Handbuch" and cols["journal"] == ""
    # an article keeps its journal
    assert "journal" not in import_columns("article", {"ENTRYTYPE": "article"}, "Nature")
