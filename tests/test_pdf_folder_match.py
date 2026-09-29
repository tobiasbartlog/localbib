"""Unit tests for the pure PDF-folder abgleich (#176).

The service answers one question per PDF — *which item is this the missing
file of?* — and it must answer it without touching the library: no write, no
connection of its own, and never an item that already has a file.

The fixtures are real PDFs (PyMuPDF) with real text, because the whole point
of the module is what it reads out of them.
"""

from __future__ import annotations

import hashlib
import os

import fitz
import pytest

from services import pdf_folder_match

PAD = [
    "This page carries enough running text that the extraction step",
    "returns well above the OCR threshold, so no scanned-page fallback",
    "is ever attempted while the test suite runs offline.",
]


def _make_pdf(path, lines) -> str:
    """One page, one line of text per entry — in reading order."""
    doc = fitz.open()
    page = doc.new_page()
    y = 72.0
    for line in list(lines) + PAD:
        page.insert_text((72, y), line, fontsize=11)
        y += 18
    doc.save(str(path))
    doc.close()
    return str(path)


def _metadata_only(db, title: str, doi: str = "") -> int:
    """A paper the way every PDF-less intake creates it: no filename, the
    title+authors placeholder as file_hash."""
    return db.add_paper({
        "file_hash": hashlib.sha256(f"{title}_".encode("utf-8")).hexdigest(),
        "filename": "", "original_filename": f"Migration: {title[:40]}",
        "title": title, "authors": "", "year": 2019, "doi": doi, "isbn": "",
        "abstract": "", "journal": "", "publisher": "", "raw_metadata": "",
        "ocr_text": "",
    })


@pytest.fixture
def folder(tmp_path):
    """Three PDFs: one names a DOI, one names its title, one is unrelated."""
    _make_pdf(tmp_path / "a.pdf", [
        "Deep Learning for Coffee Roasting",
        "doi:10.5555/coffee-roast",
    ])
    _make_pdf(tmp_path / "b.pdf", [
        "The Secret Life of Sourdough Starters",
        "A field study of domestic fermentation",
    ])
    _make_pdf(tmp_path / "c.pdf", [
        "Quarterly Numbers of a Company Nobody Cited",
    ])
    return tmp_path


@pytest.fixture
def three_items(db):
    """Three metadata-only items — one matchable by DOI, one by title, one by
    neither."""
    return {
        "doi": _metadata_only(db, "Roasting Beans At Scale", "10.5555/coffee-roast"),
        "title": _metadata_only(db, "The Secret Life of Sourdough Starters"),
        "other": _metadata_only(db, "Plate Tectonics Of The Lower Rhine Embayment"),
    }


class TestScan:
    def test_two_matches_with_their_strategy_and_one_unmatched(
            self, db, folder, three_items):
        conn = db._connect()
        try:
            result = pdf_folder_match.scan(str(folder), conn)
        finally:
            conn.close()

        assert result.scanned == 3
        by_name = {m.pdf_name: m for m in result.matches}
        assert set(by_name) == {"a.pdf", "b.pdf"}
        assert by_name["a.pdf"].paper_id == three_items["doi"]
        assert by_name["a.pdf"].strategy == "doi"
        assert by_name["b.pdf"].paper_id == three_items["title"]
        assert by_name["b.pdf"].strategy == "title"
        assert [os.path.basename(p) for p in result.unmatched] == ["c.pdf"]
        assert result.already_owned == []

    def test_the_pdf_path_is_the_stable_id_of_a_match(self, db, folder, three_items):
        conn = db._connect()
        try:
            result = pdf_folder_match.scan(str(folder), conn)
        finally:
            conn.close()
        for m in result.matches:
            assert os.path.isfile(m.pdf_path)
            assert m.confidence > 0

    def test_a_pdf_the_library_already_owns_is_reported_not_matched(
            self, db, folder, three_items):
        data = (folder / "a.pdf").read_bytes()
        db.add_paper({
            "file_hash": hashlib.sha256(data).hexdigest(),
            "filename": "owned.pdf", "original_filename": "owned.pdf",
            "title": "Already In The Library", "authors": "", "year": 2020,
            "doi": "", "isbn": "", "abstract": "", "journal": "",
            "publisher": "", "raw_metadata": "", "ocr_text": "",
        })
        conn = db._connect()
        try:
            result = pdf_folder_match.scan(str(folder), conn)
        finally:
            conn.close()
        assert [o.pdf_name for o in result.already_owned] == ["a.pdf"]
        assert "a.pdf" not in {m.pdf_name for m in result.matches}

    def test_items_that_already_have_a_file_are_no_candidates(self, db, folder):
        db.add_paper({
            "file_hash": "has-a-file", "filename": "somewhere.pdf",
            "original_filename": "somewhere.pdf",
            "title": "The Secret Life of Sourdough Starters", "authors": "",
            "year": 2019, "doi": "10.5555/coffee-roast", "isbn": "",
            "abstract": "", "journal": "", "publisher": "", "raw_metadata": "",
            "ocr_text": "",
        })
        conn = db._connect()
        try:
            result = pdf_folder_match.scan(str(folder), conn)
        finally:
            conn.close()
        assert result.matches == []
        assert len(result.unmatched) == 3

    def test_the_filename_carries_the_match_when_the_text_does_not(self, db, tmp_path):
        paper_id = _metadata_only(db, "The Hidden Cost of Widget Maintenance")
        _make_pdf(tmp_path / "Smith - 2019 - The Hidden Cost of Widget Maintenance.pdf",
                  ["Scanned cover sheet without a usable heading line"])
        conn = db._connect()
        try:
            result = pdf_folder_match.scan(str(tmp_path), conn)
        finally:
            conn.close()
        assert len(result.matches) == 1
        assert result.matches[0].paper_id == paper_id
        assert result.matches[0].strategy == "filename"

    def test_one_item_is_claimed_by_one_pdf_only(self, db, tmp_path):
        _metadata_only(db, "The Secret Life of Sourdough Starters")
        for name in ("one.pdf", "two.pdf"):
            _make_pdf(tmp_path / name, ["The Secret Life of Sourdough Starters"])
        conn = db._connect()
        try:
            result = pdf_folder_match.scan(str(tmp_path), conn)
        finally:
            conn.close()
        assert len(result.matches) == 1
        assert len(result.unmatched) == 1

    def test_subfolders_are_walked_only_when_asked(self, db, tmp_path, three_items):
        sub = tmp_path / "deeper"
        sub.mkdir()
        _make_pdf(sub / "b.pdf", ["The Secret Life of Sourdough Starters"])

        conn = db._connect()
        try:
            flat = pdf_folder_match.scan(str(tmp_path), conn)
            deep = pdf_folder_match.scan(str(tmp_path), conn, recursive=True)
        finally:
            conn.close()
        assert flat.scanned == 0
        assert deep.scanned == 1
        assert len(deep.matches) == 1

    def test_an_empty_folder_is_an_empty_result_not_an_error(self, db, tmp_path):
        conn = db._connect()
        try:
            result = pdf_folder_match.scan(str(tmp_path), conn)
        finally:
            conn.close()
        assert (result.scanned, result.matches, result.unmatched) == (0, [], [])

    def test_it_writes_nothing(self, db, folder, three_items):
        conn = db._connect()
        try:
            before = conn.execute(
                "SELECT id, filename, file_hash FROM papers ORDER BY id").fetchall()
            pdf_folder_match.scan(str(folder), conn)
            after = conn.execute(
                "SELECT id, filename, file_hash FROM papers ORDER BY id").fetchall()
        finally:
            conn.close()
        assert [tuple(r) for r in before] == [tuple(r) for r in after]
        assert sorted(os.listdir(folder)) == ["a.pdf", "b.pdf", "c.pdf"]


class TestFilenameTitle:
    @pytest.mark.parametrize("name,expected", [
        ("Smith - 2019 - A Study of Things.pdf", "A Study of Things"),
        ("Smith et al. - 2019 - A Study of Things.pdf", "A Study of Things"),
        ("2019_Smith_A_Study_of_Things.pdf", "Smith A Study of Things"),
        ("A Study of Things (1).pdf", "A Study of Things"),
        ("A-Study-of-Things.pdf", "A Study of Things"),
    ])
    def test_decoration_is_stripped(self, name, expected):
        assert pdf_folder_match._filename_title(name) == expected
