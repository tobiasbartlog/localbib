from __future__ import annotations

from pathlib import Path

import pytest

import bib_fields
from bibtex_builder import build, escape_latex, format_entry, page_range, people_field

GOLDEN = Path(__file__).parent / "fixtures" / "bib_golden"


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


class TestAuthorsAndPages:
    def test_crossref_comma_form_becomes_one_person_per_name(self):
        """CrossRef stored "Family, Given, Family, Given": split at ";" it was
        one person with too many commas — BibTeX's "Too many commas in name"."""
        entry = format_entry(_paper(authors="Müller, Hans, Schmidt, Eva, Weber, K."))
        assert '  author = {M{\\"u}ller, Hans and Schmidt, Eva and Weber, K.},' in entry

    def test_given_family_and_organizations(self):
        assert people_field("Tobias Mustermann; ACME Consortium") == "Mustermann, Tobias and {ACME Consortium}"
        assert people_field("United Nations Environment Programme") == "{United Nations Environment Programme}"
        assert people_field("Jan van der Berg") == "van der Berg, Jan"

    def test_pages_is_the_range_never_the_pdf_page_count(self):
        entry = format_entry(_paper(page_count=12))
        assert "pages" not in entry
        entry = format_entry(_paper(page_count=12, pages="45-67"))
        assert "  pages = {45--67}," in entry
        assert "12" not in entry

    @pytest.mark.parametrize("given, out", [
        ("45-67", "45--67"), ("45–67", "45--67"), ("45 -- 67", "45--67"), ("e1234", "e1234"),
    ])
    def test_page_range(self, given, out):
        assert page_range(given) == out

    def test_editors_are_people_too(self):
        entry = format_entry(_paper(item_type="chapter", booktitle="Handbuch",
                                    editors="Weber, Karl, Huber, Anna"))
        assert "  editor = {Weber, Karl and Huber, Anna}," in entry


class TestTypes:
    def test_the_stored_type_wins_over_the_guess(self):
        assert format_entry(_paper(item_type="conference", booktitle="Proc. X")).startswith(
            "@inproceedings{Smith2020,")

    def test_an_unknown_stored_type_falls_back_to_the_guess(self):
        assert format_entry(_paper(item_type="nonsense")).startswith("@article{")

    def test_a_legacy_booktitle_in_journal_goes_to_booktitle(self):
        entry = format_entry(_paper(item_type="conference", journal="Proc. of X"))
        assert "  booktitle = {Proc. of X}," in entry
        assert "journal" not in entry

    @pytest.mark.parametrize("thesis_type, entry", [
        ("Dissertation", "@phdthesis"), ("phdthesis", "@phdthesis"), ("Masterarbeit", "@mastersthesis"),
        ("Master's thesis", "@mastersthesis"), ("", "@phdthesis"),
    ])
    def test_classic_bibtex_thesis_types(self, thesis_type, entry):
        paper = _paper(item_type="thesis", journal="", thesis_type=thesis_type)
        assert format_entry(paper, dialect="bibtex").startswith(entry + "{")
        assert format_entry(paper, dialect="biblatex").startswith("@thesis{")

    @pytest.mark.parametrize("thesis_type, biblatex", [
        ("phdthesis", "phdthesis"), ("mastersthesis", "mathesis"), ("Dissertation", "Dissertation"),
    ])
    def test_a_thesis_type_key_is_no_word(self, thesis_type, biblatex):
        """An imported @phdthesis/@mastersthesis keeps its kind as a key:
        classic BibTeX would print a ``type`` verbatim, biblatex localizes
        only its own keys (``mathesis``, not ``mastersthesis``)."""
        paper = _paper(item_type="thesis", journal="", thesis_type=thesis_type)
        assert f"  type = {{{biblatex}}}," in format_entry(paper, dialect="biblatex")
        classic = format_entry(paper, dialect="bibtex")
        assert ("type = " in classic) == (thesis_type == "Dissertation")
        if thesis_type == "mastersthesis":
            assert classic.startswith("@mastersthesis{")

    def test_a_patent_holder_is_a_name_list(self):
        paper = _paper(item_type="patent", journal="", number="DE 1", institution="Maurer SE")
        assert "  holder = {{Maurer SE}}," in format_entry(paper, dialect="biblatex")

    def test_a_date_of_another_year_gives_way_to_the_year(self):
        entry = format_entry(_paper(year=2021, pub_date="2020-05"), dialect="biblatex")
        assert "  year = {2021}," in entry and "date" not in entry
        assert "month" not in format_entry(_paper(year=2021, pub_date="2020-05"), dialect="bibtex")

    def test_a_report_without_type_is_a_techreport_in_biblatex(self):
        assert format_entry(_paper(item_type="report"), dialect="biblatex").startswith("@techreport{")
        assert format_entry(_paper(item_type="report", thesis_type="Gutachten"),
                            dialect="biblatex").startswith("@report{")

    def test_the_standard_label_comes_from_the_caller(self):
        paper = _paper(item_type="standard", number="DIN 1", institution="DIN")
        assert "  type = {Standard}," in format_entry(paper)
        assert "  type = {Norm}," in format_entry(paper, labels={"standard": "Norm"})

    def test_an_unknown_dialect_is_biblatex(self):
        assert format_entry(_paper(item_type="webpage"), dialect="apa").startswith("@online{")


PAPERS = {
    "article": {"cite_key": "Mueller2024", "item_type": "article", "title": "Lebenszykluskosten von Brücken",
                "authors": "Müller, Hans, Schmidt, Eva", "journal": "Bautechnik", "year": 2024,
                "pub_date": "2024-03", "volume": "101", "issue": "3", "pages": "145-152",
                "doi": "10.1002/bate.202400012", "issn": "0932-8351", "page_count": 8},
    "book": {"cite_key": "Krause2019", "item_type": "book", "title": "Holzbau",
             "authors": "Krause, Maria", "edition": "2", "place": "Berlin", "publisher": "Springer",
             "year": 2019, "series": "Bauwesen", "volume": "7", "isbn": "978-3-662-00000-0"},
    "chapter": {"cite_key": "Lindner2020", "item_type": "chapter", "title": "Stahl & Beton",
                "authors": "Lindner, Sven", "booktitle": "Handbuch Brückenbau",
                "editors": "Weber, Karl; Huber, Anna", "publisher": "Ernst & Sohn", "place": "Berlin",
                "year": 2020, "pages": "33–58", "isbn": "978-3-433-00000-0"},
    "conference": {"cite_key": "Chen2011", "item_type": "conference", "title": "Bridge monitoring",
                   "authors": "Chen, Wei; Li, Na", "booktitle": "Proceedings of the IABSE Symposium",
                   "event": "IABSE Symposium 2011", "place": "London", "year": 2011, "pages": "12-19",
                   "doi": "10.2749/222137811796088"},
    "thesis": {"cite_key": "Mustermann2022", "item_type": "thesis", "title": "Erdbau",
               "authors": "Mustermann, Tobias", "thesis_type": "Dissertation",
               "institution": "RWTH Aachen University", "place": "Aachen", "year": 2022},
    "report": {"cite_key": "BASt2021", "item_type": "report", "title": "Brückenstatistik",
               "authors": "Bundesanstalt für Straßenwesen", "institution": "BASt", "number": "B 170",
               "place": "Bergisch Gladbach", "year": 2021},
    "preprint": {"cite_key": "Doe2023", "item_type": "preprint", "title": "Large models",
                 "authors": "Doe, Jane", "year": 2023, "eprint": "2301.01234", "version": "2",
                 "doi": "10.48550/arXiv.2301.01234"},
    "webpage": {"cite_key": "UNEP2025", "item_type": "webpage", "title": "Global Status Report",
                "authors": "United Nations Environment Programme", "institution": "UNEP",
                "url": "https://www.unep.org/resources/report_2025", "accessed": "2025-01-15",
                "year": 2025},
    "standard": {"cite_key": "DIN1990", "item_type": "standard",
                 "title": "Eurocode: Grundlagen der Tragwerksplanung", "number": "DIN EN 1990",
                 "institution": "Deutsches Institut für Normung", "publisher": "Beuth", "place": "Berlin",
                 "year": 2021, "pub_date": "2021-10"},
    "patent": {"cite_key": "Huber2018", "item_type": "patent", "title": "Lager für Brücken",
               "authors": "Huber, Anna", "number": "DE 10 2018 000 001", "institution": "Maurer SE",
               "year": 2018, "url": "https://example.org/patent"},
    "dataset": {"cite_key": "Zenodo2022", "item_type": "dataset", "title": "Brückendaten",
                "authors": "Weber, Karl", "publisher": "Zenodo", "year": 2022, "version": "1.1",
                "doi": "10.5281/zenodo.123456", "url": "https://zenodo.org/record/123456"},
    "software": {"cite_key": "LocalBib2026", "item_type": "software", "title": "LocalBib",
                 "authors": "Bartlog, Tobias", "version": "0.9.0", "year": 2026,
                 "url": "https://github.com/example/localbib"},
    "misc": {"cite_key": "Note2020", "item_type": "misc", "title": "Eine Notiz", "authors": "Nobody, N.",
             "year": 2020, "note_bib": "Unveröffentlicht", "tags": '["Brücke", "Kosten"]'},
}


def test_every_item_type_has_a_golden_paper():
    assert set(PAPERS) == set(bib_fields.ITEM_TYPES)


@pytest.mark.parametrize("dialect", bib_fields.DIALECTS)
@pytest.mark.parametrize("item_type", list(PAPERS))
def test_golden_entry_per_type_and_dialect(item_type, dialect):
    expected = (GOLDEN / f"{item_type}.{dialect}.bib").read_text(encoding="utf-8")
    assert format_entry(PAPERS[item_type], dialect=dialect) + "\n" == expected


class TestBuild:
    def test_empty_library(self):
        assert build([]) == ""

    def test_entries_separated_and_trailing_newline(self):
        content = build([_paper(), _paper(cite_key="Doe2021", title="Other")])
        assert "@article{Smith2020," in content
        assert "@article{Doe2021," in content
        assert "\n\n@article{Doe2021," in content
        assert content.endswith("}\n")

    def test_dialect_reaches_every_entry(self):
        content = build([_paper(item_type="webpage", url="https://x.org")], dialect="bibtex")
        assert content.startswith("@misc{") and "howpublished = {\\url{https://x.org}}" in content
