"""Unit tests for the ``database`` sub-module (literature_manager split, #94).

Exercises the SQLite CRUD surface against a fresh temp-file Database — no
FastAPI app, no network. The schema is created by ``Database.__init__``.
"""
from __future__ import annotations

import pytest

from database import Database


@pytest.fixture
def tmp_db(tmp_path):
    return Database(str(tmp_path / "test.db"))


# --- Categories -----------------------------------------------------------

class TestCategories:
    def test_add_category_returns_id(self, tmp_db):
        cat_id = tmp_db.add_category("AI", None, "desc", "kw")
        assert isinstance(cat_id, int) and cat_id > 0

    def test_add_duplicate_category_returns_existing_id(self, tmp_db):
        # Note: SQLite treats NULL parent_id as distinct in UNIQUE, so the
        # dedup path only triggers for non-null parents.
        parent = tmp_db.add_category("Top", None)
        first = tmp_db.add_category("Sub", parent)
        second = tmp_db.add_category("Sub", parent)
        assert first == second

    def test_get_categories_returns_inserted(self, tmp_db):
        tmp_db.add_category("AI", None, "desc", "kw")
        cats = tmp_db.get_categories()
        assert len(cats) == 1
        assert cats[0]["name"] == "AI"
        assert cats[0]["description"] == "desc"

    def test_nested_categories_and_tree(self, tmp_db):
        parent = tmp_db.add_category("Themen", None)
        tmp_db.add_category("Lehm", parent)
        tree = tmp_db.get_category_tree()
        assert "Themen" in tree
        assert "Lehm" in tree

    def test_empty_tree(self, tmp_db):
        assert tmp_db.get_category_tree() == "(keine Kategorien definiert)"


# --- Papers ---------------------------------------------------------------

def _paper(**overrides):
    data = {
        "file_hash": "hash-1",
        "filename": "2024_Doe_Title.pdf",
        "original_filename": "orig.pdf",
        "title": "A Study of Things",
        "authors": "Doe, John",
        "year": 2024,
        "doi": "10.1000/abc",
        "isbn": "",
        "abstract": "",
        "journal": "",
        "publisher": "",
        "raw_metadata": "",
        "ocr_text": "",
    }
    data.update(overrides)
    return data


class TestPapers:
    def test_add_paper_and_exists(self, tmp_db):
        pid = tmp_db.add_paper(_paper())
        assert pid > 0
        assert tmp_db.paper_exists("hash-1") is True
        assert tmp_db.paper_exists("nope") is False

    def test_add_paper_autogenerates_cite_key(self, tmp_db):
        pid = tmp_db.add_paper(_paper())
        papers = tmp_db.get_all_papers()
        row = next(p for p in papers if p["id"] == pid)
        assert row["cite_key"]  # non-empty key was assigned

    def test_caller_supplied_cite_key_is_kept(self, tmp_db):
        tmp_db.add_paper(_paper(cite_key="MyKey2024"))
        papers = tmp_db.get_all_papers()
        assert papers[0]["cite_key"] == "MyKey2024"

    def test_get_all_papers(self, tmp_db):
        tmp_db.add_paper(_paper(file_hash="h1"))
        tmp_db.add_paper(_paper(file_hash="h2", doi="10.1/x"))
        assert len(tmp_db.get_all_papers()) == 2

    def test_search_papers_matches_title(self, tmp_db):
        tmp_db.add_paper(_paper(file_hash="h1", title="Deep Learning Survey"))
        tmp_db.add_paper(_paper(file_hash="h2", title="Something Else"))
        results = tmp_db.search_papers("Deep Learning")
        assert len(results) == 1
        assert results[0]["title"] == "Deep Learning Survey"


class TestDuplicateDetection:
    def test_find_duplicate_by_hash(self, tmp_db):
        tmp_db.add_paper(_paper(file_hash="dup-hash"))
        dup = tmp_db.find_duplicate_paper(file_hash="dup-hash")
        assert dup is not None

    def test_find_duplicate_by_doi(self, tmp_db):
        tmp_db.add_paper(_paper(doi="10.5555/unique"))
        dup = tmp_db.find_duplicate_paper(doi="10.5555/UNIQUE")  # case-insensitive
        assert dup is not None

    def test_find_duplicate_by_title(self, tmp_db):
        tmp_db.add_paper(_paper(title="A Comprehensive Survey of Methods"))
        dup = tmp_db.find_duplicate_paper(title="a comprehensive survey of methods!")
        assert dup is not None

    def test_no_false_positive(self, tmp_db):
        tmp_db.add_paper(_paper())
        assert tmp_db.find_duplicate_paper(doi="10.9/none", title="Totally Different Long Title") is None


class TestCategoriesAssignment:
    def test_assign_and_get_paper_categories(self, tmp_db):
        pid = tmp_db.add_paper(_paper())
        cid = tmp_db.add_category("AI", None)
        tmp_db.assign_category(pid, cid, 0.9)
        cats = tmp_db.get_paper_categories(pid)
        assert len(cats) == 1
        assert cats[0]["id"] == cid
        assert cats[0]["confidence"] == 0.9

    def test_get_papers_by_category_includes_descendants(self, tmp_db):
        parent = tmp_db.add_category("Themen", None)
        child = tmp_db.add_category("Lehm", parent)
        pid = tmp_db.add_paper(_paper())
        tmp_db.assign_category(pid, child)
        # querying the parent must include papers tagged on the child
        papers = tmp_db.get_papers_by_category(parent)
        assert len(papers) == 1
        assert papers[0]["id"] == pid


class TestAppSettings:
    def test_get_unset_returns_empty(self, tmp_db):
        assert tmp_db.get_app_setting("missing") == ""

    def test_set_then_get(self, tmp_db):
        tmp_db.set_app_setting("license_key", "ABC123")
        assert tmp_db.get_app_setting("license_key") == "ABC123"

    def test_set_overwrites(self, tmp_db):
        tmp_db.set_app_setting("k", "v1")
        tmp_db.set_app_setting("k", "v2")
        assert tmp_db.get_app_setting("k") == "v2"
