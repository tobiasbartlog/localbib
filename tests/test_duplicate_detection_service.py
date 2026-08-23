"""Unit tests for services.duplicate_detection (issue #91).

The service contains only pure functions (no DB writes, no network, no FS ops).
All tests run fully offline and require no mocking.
"""
from __future__ import annotations

import pytest

from services.duplicate_detection import find_duplicate_groups, normalize_title


# ===========================================================================
# normalize_title — title normalisation
# ===========================================================================


class TestNormalizeTitle:
    """Pure function — deterministic, no mocks needed."""

    def test_empty_string_returns_empty(self):
        assert normalize_title("") == ""

    def test_none_like_falsy_returns_empty(self):
        # The public API accepts str; falsy str is ""
        assert normalize_title("") == ""

    def test_lowercases(self):
        assert normalize_title("Machine Learning") == "machine learning"

    def test_strips_leading_trailing_whitespace(self):
        assert normalize_title("  Hello World  ") == "hello world"

    def test_collapses_internal_whitespace(self):
        assert normalize_title("Hello   World") == "hello world"

    def test_removes_colon(self):
        result = normalize_title("Machine Learning: A Survey")
        assert ":" not in result
        assert result == "machine learning a survey"

    def test_removes_parentheses(self):
        result = normalize_title("Deep Learning (2020)")
        assert "(" not in result and ")" not in result
        assert result == "deep learning 2020"

    def test_unicode_nfkd_removes_combining_accents(self):
        # 'é' decomposes to 'e' + combining accent (U+0301 = not \w) → stripped
        result = normalize_title("Réseaux de neurones")
        assert result == "reseaux de neurones"

    def test_hyphen_removed_words_concatenate(self):
        # Hyphens are removed without inserting space → words concatenate
        result = normalize_title("State-of-the-Art Methods")
        assert "-" not in result
        assert result == "stateoftheart methods"

    def test_apostrophe_removed(self):
        result = normalize_title("Author's Guide to Science")
        assert "'" not in result
        assert result == "authors guide to science"

    def test_digits_preserved(self):
        result = normalize_title("GPT-4 Analysis 2023")
        assert "4" in result
        assert "2023" in result

    def test_slash_removed(self):
        result = normalize_title("NLP/ML Approaches")
        assert "/" not in result

    def test_numbers_only_title(self):
        assert normalize_title("2024") == "2024"

    def test_tab_treated_as_whitespace(self):
        # \t is \s so it is NOT removed by [^\w\s]; it is then collapsed by \s+
        result = normalize_title("Deep\tLearning")
        assert "deep" in result and "learning" in result

    def test_already_clean_title_unchanged(self):
        assert normalize_title("machine learning") == "machine learning"

    def test_mixed_case_and_punctuation(self):
        result = normalize_title("A Survey: On ML/DL Approaches (2022)")
        assert ":" not in result
        assert "/" not in result
        assert "(" not in result
        assert result == "a survey on mldl approaches 2022"


# ===========================================================================
# find_duplicate_groups — three-strategy grouping
# ===========================================================================


def _make_paper(pid: int, title: str = "", doi: str = "") -> dict:
    return {"id": pid, "title": title, "doi": doi, "has_file": False}


class TestFindDuplicateGroupsEmpty:
    def test_empty_list_returns_empty(self):
        assert find_duplicate_groups([]) == []

    def test_single_paper_returns_empty(self):
        assert find_duplicate_groups([_make_paper(1, title="Unique Title Here")]) == []


class TestStrategy1SameDoi:
    def test_same_doi_produces_group(self):
        papers = [
            _make_paper(1, doi="10.1000/abc"),
            _make_paper(2, doi="10.1000/abc"),
        ]
        groups = find_duplicate_groups(papers)
        assert len(groups) == 1
        assert "DOI" in groups[0]["reason"]

    def test_doi_normalised_to_lowercase(self):
        papers = [
            _make_paper(1, doi="10.1000/ABC"),
            _make_paper(2, doi="10.1000/abc"),
        ]
        groups = find_duplicate_groups(papers)
        assert len(groups) == 1

    def test_doi_whitespace_stripped(self):
        papers = [
            _make_paper(1, doi="  10.1000/xyz  "),
            _make_paper(2, doi="10.1000/xyz"),
        ]
        groups = find_duplicate_groups(papers)
        assert len(groups) == 1

    def test_empty_doi_not_grouped(self):
        papers = [
            _make_paper(1, doi="", title="Ignored Title for DOI Test ABC"),
            _make_paper(2, doi="", title="Ignored Title for DOI Test DEF"),
        ]
        groups = find_duplicate_groups(papers)
        doi_groups = [g for g in groups if "DOI" in g["reason"]]
        assert doi_groups == []

    def test_different_dois_not_grouped(self):
        papers = [
            _make_paper(1, doi="10.1/aaa"),
            _make_paper(2, doi="10.1/bbb"),
        ]
        groups = find_duplicate_groups(papers)
        assert groups == []

    def test_doi_reason_contains_doi_value(self):
        papers = [
            _make_paper(1, doi="10.9999/test"),
            _make_paper(2, doi="10.9999/test"),
        ]
        groups = find_duplicate_groups(papers)
        assert "10.9999/test" in groups[0]["reason"]

    def test_group_contains_both_papers(self):
        papers = [
            _make_paper(1, doi="10.7/both"),
            _make_paper(2, doi="10.7/both"),
        ]
        groups = find_duplicate_groups(papers)
        ids = {p["id"] for p in groups[0]["papers"]}
        assert ids == {1, 2}


class TestStrategy2SameNormTitle:
    def test_identical_title_produces_group(self):
        title = "A Very Long Identical Title For Testing"
        papers = [
            _make_paper(1, title=title),
            _make_paper(2, title=title),
        ]
        groups = find_duplicate_groups(papers)
        assert len(groups) == 1
        assert groups[0]["reason"] == "Gleicher Titel"

    def test_case_insensitive_match(self):
        papers = [
            _make_paper(1, title="Machine Learning Study Result"),
            _make_paper(2, title="machine learning study result"),
        ]
        groups = find_duplicate_groups(papers)
        assert len(groups) == 1

    def test_punctuation_differences_normalised_away(self):
        papers = [
            _make_paper(1, title="Machine Learning: A Survey Paper"),
            _make_paper(2, title="Machine Learning A Survey Paper"),
        ]
        groups = find_duplicate_groups(papers)
        assert len(groups) == 1

    def test_short_title_under_10_chars_skipped(self):
        """Normalized titles with len <= 10 are skipped by strategy 2."""
        papers = [
            _make_paper(1, title="AI"),
            _make_paper(2, title="AI"),
        ]
        groups = find_duplicate_groups(papers)
        gleicher = [g for g in groups if g["reason"] == "Gleicher Titel"]
        assert gleicher == []

    def test_doi_pair_not_duplicated_by_title_strategy(self):
        """A pair already grouped by DOI must NOT form a second group via title."""
        title = "Unique Title For Dedup Test Across Detection Strategies Now"
        papers = [
            _make_paper(1, title=title, doi="10.1000/dedup"),
            _make_paper(2, title=title, doi="10.1000/dedup"),
        ]
        groups = find_duplicate_groups(papers)
        # Each frozenset of paper IDs appears at most once across all groups
        seen_pairs: list = []
        for g in groups:
            pair = frozenset(p["id"] for p in g["papers"])
            assert pair not in seen_pairs, f"Same pair grouped twice: {pair}"
            seen_pairs.append(pair)


class TestStrategy3SimilarTitle:
    def test_substring_title_detected(self):
        """If one normalized title is a substring of the other they are grouped."""
        papers = [
            _make_paper(1, title="Deep Learning Methods Overview Techniques"),
            _make_paper(2, title="Deep Learning Methods Overview Techniques Extended"),
        ]
        groups = find_duplicate_groups(papers)
        assert len(groups) >= 1
        reasons = [g["reason"] for g in groups]
        assert any("enthalten" in r or "Titel" in r or "hnlich" in r for r in reasons)

    def test_high_jaccard_detected(self):
        """Word-Jaccard >= 0.8 triggers an 'Aehnlicher Titel' group."""
        t1 = "Neural Network Image Classification Study For The Advanced Learning Techniques"
        t2 = "Neural Network Image Classification Study For The Advanced Learning Approaches"
        papers = [_make_paper(1, title=t1), _make_paper(2, title=t2)]
        groups = find_duplicate_groups(papers)
        assert len(groups) >= 1

    def test_low_jaccard_not_detected(self):
        """Word-Jaccard < 0.8 with no substring → no strategy-3 group."""
        papers = [
            _make_paper(
                1, title="Quantum Computing Algorithms Optimization Framework Design"
            ),
            _make_paper(
                2, title="Natural Language Processing Text Mining Analysis Techniques"
            ),
        ]
        groups = find_duplicate_groups(papers)
        assert groups == []

    def test_title_under_15_chars_skipped(self):
        """Strategy 3 skips normalized titles shorter than 15 chars."""
        papers = [
            _make_paper(1, title="Simple Title"),   # 12 chars → skipped
            _make_paper(2, title="Simple Titles"),
        ]
        groups = find_duplicate_groups(papers)
        sub_groups = [g for g in groups if g["reason"] == "Titel enthalten"]
        assert sub_groups == []

    def test_fewer_than_3_words_jaccard_skipped(self):
        """Jaccard path is skipped when either title has < 3 words."""
        papers = [
            _make_paper(1, title="Short One"),
            _make_paper(2, title="Short Two"),
        ]
        # No crash expected; result may be empty
        groups = find_duplicate_groups(papers)
        assert isinstance(groups, list)


class TestInternalIdTracking:
    def test_internal_ids_key_not_exposed(self):
        """The internal '_ids' tracking key must be stripped from returned dicts."""
        papers = [
            _make_paper(1, doi="10.0/strip"),
            _make_paper(2, doi="10.0/strip"),
        ]
        groups = find_duplicate_groups(papers)
        for g in groups:
            assert "_ids" not in g

    def test_total_papers_equals_sum_of_group_sizes(self):
        """Sanity: total_duplicates = sum(len(g['papers'])) for all groups."""
        papers = [
            _make_paper(1, doi="10.0/cnt"),
            _make_paper(2, doi="10.0/cnt"),
        ]
        groups = find_duplicate_groups(papers)
        total = sum(len(g["papers"]) for g in groups)
        assert total == 2
