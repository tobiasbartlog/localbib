from __future__ import annotations

import sqlite3

import pytest

from paper_matcher import FUZZY_THRESHOLD, MatchResult, match


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        """CREATE TABLE papers (
            id INTEGER PRIMARY KEY,
            doi TEXT,
            title TEXT
        )"""
    )
    db.execute(
        "INSERT INTO papers VALUES (1, '10.1000/abc', 'Deep Learning for Image Recognition')"
    )
    db.execute(
        "INSERT INTO papers VALUES (2, NULL, 'Attention Is All You Need')"
    )
    db.execute(
        "INSERT INTO papers VALUES (3, '10.2000/xyz', NULL)"
    )
    db.commit()
    return db


class TestDOIMatch:
    def test_exact_doi_returns_doi_strategy(self, conn):
        result = match({"doi": "10.1000/abc", "title": ""}, conn)
        assert result.match_strategy == "doi"
        assert result.matched_paper_id == 1
        assert result.match_confidence == 1.0

    def test_doi_case_insensitive(self, conn):
        result = match({"doi": "10.1000/ABC", "title": ""}, conn)
        assert result.matched_paper_id == 1

    def test_doi_with_https_prefix(self, conn):
        result = match({"doi": "https://doi.org/10.1000/abc", "title": ""}, conn)
        assert result.matched_paper_id == 1

    def test_doi_priority_over_title(self, conn):
        # Give correct DOI and wrong title → should still match by DOI
        result = match({"doi": "10.1000/abc", "title": "Attention Is All You Need"}, conn)
        assert result.match_strategy == "doi"
        assert result.matched_paper_id == 1


class TestFuzzyMatch:
    def test_exact_title_match(self, conn):
        result = match({"doi": "", "title": "Attention Is All You Need"}, conn)
        assert result.match_strategy == "fuzzy"
        assert result.matched_paper_id == 2
        assert result.match_confidence == 1.0

    def test_near_title_match_above_threshold(self, conn):
        result = match({"doi": "", "title": "Attention is all you need"}, conn)
        assert result.match_strategy == "fuzzy"
        assert result.matched_paper_id == 2

    def test_near_miss_below_threshold_returns_none(self, conn):
        result = match({"doi": "", "title": "Completely Unrelated Paper Title Here"}, conn)
        assert result.match_strategy == "none"
        assert result.matched_paper_id is None

    def test_short_title_skips_fuzzy(self, conn):
        result = match({"doi": "", "title": "Short"}, conn)
        assert result.match_strategy == "none"


class TestNoMatch:
    def test_empty_ref_returns_none(self, conn):
        result = match({}, conn)
        assert result.match_strategy == "none"
        assert result.matched_paper_id is None
        assert result.match_confidence == 0.0

    def test_unknown_doi_and_title_returns_none(self, conn):
        result = match({"doi": "10.9999/unknown", "title": "No Such Paper Exists Here"}, conn)
        assert result.match_strategy == "none"
