from __future__ import annotations

from bibtex_builder import build, escape_latex, format_entry


def _paper(**overrides):
    paper = {
        "cite_key": "Smith2020",
        "title": "A Study",
        "authors": "Smith, John; Doe, Jane",
        "year": 2020,
        "journal": "Nature",
        "publisher": "",
        "isbn": "",
        "doi": "10.1000/abc",
        "abstract": "",
        "page_count": 0,
    }
    paper.update(overrides)
    return paper


class TestEscapeLatex:
    def test_special_chars(self):
        assert escape_latex("50% & more") == "50\\% \\& more"

    def test_umlauts(self):
        assert escape_latex("Müller") == 'M{\\"u}ller'

    def test_empty(self):
        assert escape_latex("") == ""


class TestFormatEntry:
    def test_article_when_journal_present(self):
        entry = format_entry(_paper())
        assert entry.startswith("@article{Smith2020,")
        assert "  journal = {Nature}," in entry

    def test_book_when_isbn_and_publisher(self):
        entry = format_entry(
            _paper(journal="", isbn="978-3", publisher="Springer")
        )
        assert entry.startswith("@book{Smith2020,")

    def test_misc_otherwise(self):
        entry = format_entry(_paper(journal="", doi=""))
        assert entry.startswith("@misc{Smith2020,")

    def test_authors_joined_with_and(self):
        entry = format_entry(_paper())
        assert "  author = {Smith, John and Doe, Jane}," in entry

    def test_empty_fields_omitted(self):
        entry = format_entry(_paper(journal="", doi="", abstract=""))
        assert "journal" not in entry
        assert "doi" not in entry
        assert "abstract" not in entry

    def test_title_escaped(self):
        entry = format_entry(_paper(title="Salt & Pepper"))
        assert "  title = {Salt \\& Pepper}," in entry

    def test_explicit_key_overrides_stored(self):
        entry = format_entry(_paper(), key="Custom1")
        assert entry.startswith("@article{Custom1,")


class TestBuild:
    def test_empty_library(self):
        assert build([]) == ""

    def test_entries_separated_and_trailing_newline(self):
        content = build([_paper(), _paper(cite_key="Doe2021", title="Other")])
        assert "@article{Smith2020," in content
        assert "@article{Doe2021," in content
        assert "\n\n@article{Doe2021," in content
        assert content.endswith("}\n")
