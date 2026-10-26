"""Bibliographic lookup (Ticket 2): the three clients and the one seam.

CrossRef, DataCite and Open Library are driven with respx and recorded answer
shapes (``tests/fixtures/bib_sources``) — no test reaches the network. The
rules of ``bib_lookup`` (source order, fills vs. conflicts, the year) run on
plain dicts.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

import bib_lookup
import crossref
import datacite
import openlibrary_client

FIXTURES = Path(__file__).parent / "fixtures" / "bib_sources"
CROSSREF = "https://api.crossref.org/works/"
DATACITE = "https://api.datacite.org/dois/"
OPENLIBRARY = "https://openlibrary.org/api/books"

pytestmark = pytest.mark.real_bib_sources


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# CrossRef
# ---------------------------------------------------------------------------

class TestCrossRef:
    @respx.mock
    def test_article_full_mapping_keeps_the_eight_keys(self):
        doi = "10.1080/15732479.2020.1832539"
        respx.get(CROSSREF + doi).mock(return_value=httpx.Response(200, json=_fixture("crossref_article.json")))
        rec = crossref.fetch_crossref_metadata(doi)
        # The eight historic keys every caller reads.
        assert rec["title"] == "Shear capacity of unstabilised earth masonry walls"
        assert rec["authors"] == "Schneider, Anna; Chen, Jun; van der Berg, Pieter"
        assert rec["year"] == 2021  # print before online (2020-10)
        assert rec["doi"] == doi
        assert rec["abstract"] == ("Earth masonry is a low-carbon building material. "
                                   "This study tests 24 walls under combined loading.")
        assert rec["journal"] == "Structure and Infrastructure Engineering"
        assert rec["publisher"] == "Informa UK Limited"
        assert rec["isbn"] == ""
        # The bibliographic columns.
        assert (rec["item_type"], rec["volume"], rec["issue"], rec["pages"]) == ("article", "17", "3", "121-139")
        assert rec["pub_date"] == "2021-02"
        assert rec["issn"] == "1573-2479"  # the print ISSN
        assert rec["language"] == "en"
        assert "url" not in rec  # the DOI link says nothing the doi does not
        assert "booktitle" not in rec

    @respx.mock
    def test_book_chapter_takes_book_title_series_and_editors(self):
        doi = "10.1007/978-3-030-91320-5_7"
        respx.get(CROSSREF + doi).mock(return_value=httpx.Response(200, json=_fixture("crossref_book_chapter.json")))
        rec = crossref.fetch_crossref_metadata(doi)
        assert rec["item_type"] == "chapter"
        assert rec["journal"] == ""  # a book is no journal (Ticket 1)
        assert rec["booktitle"] == "Life-Cycle of Structures and Infrastructure Systems"
        assert rec["series"] == "Lecture Notes in Civil Engineering"
        assert rec["editors"] == "Biondini, Fabio; Frangopol, Dan M."
        assert rec["authors"] == "Hoffmann, Lena; Becker, Tom"
        assert rec["pages"] == "101-118"
        assert rec["place"] == "Cham"
        assert rec["publisher"] == "Springer International Publishing"
        assert rec["isbn"] == "9783030913199, 9783030913205"
        assert rec["year"] == 2022
        assert "pub_date" not in rec  # print date is the year only

    @respx.mock
    def test_proceedings_article_is_a_conference_paper_with_event(self):
        doi = "10.1109/cvpr.2016.90"
        respx.get(CROSSREF + doi).mock(return_value=httpx.Response(200, json=_fixture("crossref_proceedings.json")))
        rec = crossref.fetch_crossref_metadata(doi)
        assert rec["item_type"] == "conference"
        assert rec["booktitle"] == "2016 IEEE Conference on Computer Vision and Pattern Recognition (CVPR)"
        assert rec["event"] == rec["booktitle"]
        assert "series" not in rec
        assert rec["authors"] == "He, Kaiming; Zhang, Xiangyu; Ren, Shaoqing; Sun, Jian"
        assert (rec["pages"], rec["year"], rec["pub_date"]) == ("770-778", 2016, "2016-06")

    @respx.mock
    def test_unknown_doi_is_none(self):
        respx.get(CROSSREF + "10.5555/missing").mock(return_value=httpx.Response(404))
        assert crossref.fetch_crossref_metadata("10.5555/missing") is None

    def test_parse_tolerates_empty_dates_and_unknown_types(self):
        rec = crossref.parse_crossref_message(
            {"type": "peer-review", "published-print": {"date-parts": [[None]]},
             "issued": {"date-parts": [[2019, 4, 2]]}, "title": ["T"]}, "10.1/x")
        assert rec["year"] == 2019 and rec["pub_date"] == "2019-04-02"
        assert "item_type" not in rec

    def test_dissertation_degree_becomes_thesis_type(self):
        rec = crossref.parse_crossref_message(
            {"type": "dissertation", "title": ["T"], "degree": ["PhD"],
             "institution": [{"name": "TU Delft"}]}, "10.1/x")
        assert (rec["item_type"], rec["thesis_type"], rec["institution"]) == ("thesis", "phdthesis", "TU Delft")


# ---------------------------------------------------------------------------
# DataCite
# ---------------------------------------------------------------------------

class TestDataCite:
    @respx.mock
    def test_arxiv_preprint(self):
        doi = "10.48550/arXiv.2108.08090"
        respx.get(DATACITE + doi).mock(return_value=httpx.Response(200, json=_fixture("datacite_arxiv.json")))
        rec = datacite.fetch_datacite_metadata(doi)
        assert rec == {
            "title": "Graph Surrogates for Structural Health Monitoring",
            "authors": "Okafor, Ada; Lund, Per",
            "year": 2021,
            "doi": "10.48550/arxiv.2108.08090",
            "publisher": "arXiv",
            "abstract": "We train graph surrogates on sensor data of 40 bridges.",
            "item_type": "preprint",
            "version": "2",
            "url": "https://arxiv.org/abs/2108.08090",
            "eprint": "2108.08090",
        }

    @respx.mock
    def test_zenodo_dataset_with_an_organization(self):
        doi = "10.5281/zenodo.7898765"
        respx.get(DATACITE + doi).mock(return_value=httpx.Response(200, json=_fixture("datacite_zenodo_dataset.json")))
        rec = datacite.fetch_datacite_metadata(doi)
        assert rec["item_type"] == "dataset"
        assert rec["authors"] == "{Bundesanstalt für Wasserbau}; Weber, Clara"  # Organizational: braced
        assert "editors" not in rec  # a contact person is no editor
        assert (rec["publisher"], rec["version"], rec["pub_date"]) == ("Zenodo", "1.2.0", "2023-05-04")
        assert rec["abstract"] == "Measured scour depths of 60 flume tests."
        assert rec["url"] == "https://zenodo.org/record/7898765"
        assert rec["language"] == "en"
        assert "eprint" not in rec

    @respx.mock
    def test_zenodo_software_with_a_publisher_object(self):
        doi = "10.5281/zenodo.7301245"
        respx.get(DATACITE + doi).mock(return_value=httpx.Response(200, json=_fixture("datacite_zenodo_software.json")))
        rec = datacite.fetch_datacite_metadata(doi)
        assert (rec["item_type"], rec["version"], rec["publisher"]) == ("software", "v0.4.1", "Zenodo")
        assert (rec["year"], rec["pub_date"]) == (2022, "2022-11-02")
        assert rec["authors"] == "Nguyen, Thi; Patel, Raj"

    @respx.mock
    def test_unknown_doi_is_none(self):
        respx.get(DATACITE + "10.5555/x").mock(return_value=httpx.Response(404))
        assert datacite.fetch_datacite_metadata("10.5555/x") is None

    @respx.mock
    def test_unreachable_is_none(self):
        respx.get(DATACITE + "10.5555/x").mock(side_effect=httpx.ConnectError("down"))
        assert datacite.fetch_datacite_metadata("10.5555/x") is None


# ---------------------------------------------------------------------------
# Open Library
# ---------------------------------------------------------------------------

class TestOpenLibrary:
    @respx.mock
    def test_book_by_isbn(self):
        route = respx.get(OPENLIBRARY).mock(return_value=httpx.Response(200, json=_fixture("openlibrary_book.json")))
        rec = openlibrary_client.fetch_openlibrary_metadata("978-0-262-03384-8")
        assert route.calls[0].request.url.params["bibkeys"] == "ISBN:9780262033848"
        assert route.calls[0].request.url.params["jscmd"] == "data"
        assert rec == {
            "item_type": "book",
            "title": "Introduction to algorithms",
            "authors": "Cormen, Thomas H.; Leiserson, Charles E.; Rivest, Ronald L.; Stein, Clifford",
            "publisher": "MIT Press",
            "place": "Cambridge, Mass",
            "year": 2009,
            "isbn": "9780262033848",
        }

    @respx.mock
    def test_unknown_isbn_is_none(self):
        respx.get(OPENLIBRARY).mock(return_value=httpx.Response(200, json={}))
        assert openlibrary_client.fetch_openlibrary_metadata("9780000000002") is None

    def test_no_valid_isbn_asks_nothing(self):
        assert openlibrary_client.fetch_openlibrary_metadata("12-34") is None

    def test_subtitle_edition_series_and_dates(self):
        rec = openlibrary_client.parse_openlibrary({
            "title": "Baustatik", "subtitle": "Grundlagen und Beispiele",
            "edition_name": "3rd ed.", "series": ["Studium Bauwesen"],
            "publish_date": "Sep 15, 2016", "authors": [{"name": "Deutsches Institut für Normung"}],
        }, "9783658000000")
        assert rec["title"] == "Baustatik: Grundlagen und Beispiele"
        assert (rec["edition"], rec["series"]) == ("3", "Studium Bauwesen")
        assert (rec["year"], rec["pub_date"]) == (2016, "2016-09-15")
        assert rec["authors"] == "{Deutsches Institut für Normung}"  # an organization: braced
        assert openlibrary_client.parse_openlibrary({"publish_date": "July 2009"}, "x")["pub_date"] == "2009-07"


# ---------------------------------------------------------------------------
# The seam: order and per-field source
# ---------------------------------------------------------------------------

class TestFetch:
    def test_crossref_first_and_every_field_names_its_source(self):
        rec = bib_lookup.fetch(doi="10.1/a", crossref_fetch=lambda d: {"title": "T", "year": "2020", "isbn": ""},
                               datacite_fetch=lambda d: pytest.fail("DataCite asked after a CrossRef hit"))
        assert rec["fields"] == {"title": "T", "year": 2020}
        assert rec["sources"] == {"title": "crossref", "year": "crossref"}

    def test_datacite_when_crossref_knows_nothing(self):
        rec = bib_lookup.fetch(doi="10.48550/arXiv.1", crossref_fetch=lambda d: None,
                               datacite_fetch=lambda d: {"title": "Pre", "eprint": "1"})
        assert rec["sources"] == {"title": "datacite", "eprint": "datacite"}

    def test_open_library_only_without_a_doi_hit(self):
        rec = bib_lookup.fetch(doi="10.1/none", isbn="9780262033848",
                               crossref_fetch=lambda d: None, datacite_fetch=lambda d: None,
                               openlibrary_fetch=lambda i: {"title": "Book", "item_type": "book"})
        assert rec["sources"]["title"] == "openlibrary"

    def test_nothing_known_and_a_raising_fetcher_are_empty(self):
        def boom(_):
            raise RuntimeError("x")
        assert bib_lookup.fetch(doi="10.1/x", crossref_fetch=boom, datacite_fetch=lambda d: None) == {
            "fields": {}, "sources": {},
            "status": {"crossref": "not_found", "datacite": "not_found", "openlibrary": "no_identifier"}}
        assert bib_lookup.fetch() == {"fields": {}, "sources": {}, "status": {
            "crossref": "no_identifier", "datacite": "no_identifier", "openlibrary": "no_identifier"}}

    def test_status_names_what_each_source_said(self):
        rec = bib_lookup.fetch(doi="10.1/a", isbn="9780262033848",
                               crossref_fetch=lambda d: {"title": "T"},
                               openlibrary_fetch=lambda i: pytest.fail("asked after a DOI hit"))
        assert rec["status"] == {"crossref": "found", "datacite": "not_asked", "openlibrary": "not_asked"}
        rec = bib_lookup.fetch(isbn="9780262033848", openlibrary_fetch=lambda i: {"title": "Book"})
        assert rec["status"] == {"crossref": "no_identifier", "datacite": "no_identifier",
                                 "openlibrary": "found"}


# ---------------------------------------------------------------------------
# The seam: fills and conflicts
# ---------------------------------------------------------------------------

class TestCompare:
    def test_empty_fields_fill_differing_ones_conflict_equal_ones_are_quiet(self):
        paper = {"title": "Shear capacity of earth walls", "authors": "Schneider, Anna, Chen, Jun",
                 "year": 2021, "journal": "", "volume": "", "pages": "121–139", "doi": "10.1080/X",
                 "isbn": "978-3-030-91319-9"}
        found = {"title": "Shear capacity of unstabilised earth walls",
                 "authors": "Schneider, Anna; Chen, Jun", "year": 2021, "journal": "SIE",
                 "volume": "17", "pages": "121-139", "doi": "10.1080/x",
                 "isbn": "9783030913199, 9783030913205"}
        out = bib_lookup.compare(paper, found, {f: "crossref" for f in found})
        assert out["fills"] == {"journal": "SIE", "volume": "17"}
        assert out["conflicts"] == [{"field": "title", "current": paper["title"],
                                     "found": found["title"], "source": "crossref"}]
        assert out["sources"] == {"journal": "crossref", "volume": "crossref", "title": "crossref"}

    def test_year_is_never_filled_over_a_stored_one(self):
        out = bib_lookup.compare({"year": 2019, "pub_date": ""}, {"year": 2021, "pub_date": "2021-02"})
        assert out["fills"] == {}  # a date of another year is dropped
        assert [c["field"] for c in out["conflicts"]] == ["year"]

    def test_a_date_of_the_stored_year_fills(self):
        out = bib_lookup.compare({"year": 2021, "pub_date": ""}, {"year": 2021, "pub_date": "2021-02"})
        assert out == {"fills": {"pub_date": "2021-02"}, "conflicts": [], "sources": {}}

    def test_without_a_year_the_date_brings_its_year(self):
        assert bib_lookup.compare({"year": None}, {"pub_date": "2022-11-02"})["fills"] == {
            "pub_date": "2022-11-02", "year": 2022}

    def test_a_year_of_zero_is_no_year_and_fills(self):
        out = bib_lookup.compare({"year": 0, "pub_date": ""}, {"year": 2021, "pub_date": "2021-02"})
        assert out["fills"] == {"year": 2021, "pub_date": "2021-02"}
        assert out["conflicts"] == []

    def test_the_abstract_only_ever_fills(self):
        """Maintainer decision (b): an abstract the Item has is never set
        against the source's — no conflict row either."""
        out = bib_lookup.compare({"abstract": "Our own summary."}, {"abstract": "The publisher's text."},
                                 {"abstract": "crossref"})
        assert out == {"fills": {}, "conflicts": [], "sources": {}}
        out = bib_lookup.compare({"abstract": ""}, {"abstract": "The publisher's text."})
        assert out["fills"] == {"abstract": "The publisher's text."}

    def test_pages_dash_styles_and_separator_only_authors(self):
        out = bib_lookup.compare({"pages": "101–118", "authors": " , ;"},
                                 {"pages": "101-118", "authors": "Doe, Jane"})
        assert out["fills"] == {"authors": "Doe, Jane"}
        assert out["conflicts"] == []

    def test_bib_columns_picks_only_filled_bibliographic_columns(self):
        assert bib_lookup.bib_columns({"title": "T", "volume": "3", "pages": "", "item_type": "article"}) == {
            "volume": "3", "item_type": "article"}
