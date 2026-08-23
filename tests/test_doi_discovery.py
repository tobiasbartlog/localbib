"""Unit tests for the ``doi_discovery`` sub-module (literature_manager split, #94).

The pure regex extractors run without mocking; the online lookups
(``search_crossref_for_doi``, ``search_openalex_for_doi``) and the
``discover_doi`` orchestration mock their network boundary via monkeypatch.
"""
from __future__ import annotations

import pytest

import doi_discovery
from doi_discovery import (
    discover_doi,
    extract_arxiv_id_from_text,
    extract_doi_from_text,
    extract_isbn_from_text,
    extract_year_from_text,
    resolve_doi_from_arxiv,
    search_crossref_for_doi,
)


class TestExtractDOI:
    def test_plain_doi(self):
        assert extract_doi_from_text("doi: 10.1234/abcd.5678") == "10.1234/abcd.5678"

    def test_doi_url(self):
        assert extract_doi_from_text("see https://doi.org/10.1000/xyz here") == "10.1000/xyz"

    def test_bare_doi_pattern(self):
        assert extract_doi_from_text("10.5555/foobar end") == "10.5555/foobar"

    def test_trailing_dot_stripped(self):
        assert extract_doi_from_text("doi: 10.1234/abc.") == "10.1234/abc"

    def test_none_when_absent(self):
        assert extract_doi_from_text("no identifier here") is None

    def test_only_searches_first_chars(self):
        text = "x" * 4000 + " 10.1/late"
        assert extract_doi_from_text(text, max_chars=3000) is None


class TestExtractYear:
    def test_picks_most_common(self):
        assert extract_year_from_text("2019 2019 1998") == 2019

    def test_none_when_absent(self):
        assert extract_year_from_text("no year") is None


class TestExtractISBN:
    def test_isbn13(self):
        assert extract_isbn_from_text("ISBN: 978-3-16-148410-0") == "9783161484100"

    def test_none_when_absent(self):
        assert extract_isbn_from_text("nothing") is None


class TestExtractArxiv:
    def test_abs_url(self):
        assert extract_arxiv_id_from_text("arxiv.org/abs/1803.10122") == "1803.10122"

    def test_prefixed(self):
        assert extract_arxiv_id_from_text("arXiv:2101.00001v2") == "2101.00001v2"

    def test_none_when_absent(self):
        assert extract_arxiv_id_from_text("nothing") is None


class TestResolveArxiv:
    def test_datacite_doi(self):
        assert resolve_doi_from_arxiv("1803.10122") == "10.48550/arXiv.1803.10122"


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class TestSearchCrossref:
    def test_returns_doi_on_good_match(self, monkeypatch):
        payload = {
            "message": {
                "items": [
                    {"DOI": "10.1/match", "title": ["Deep Learning for Image Recognition"]}
                ]
            }
        }
        monkeypatch.setattr(
            doi_discovery.requests, "get",
            lambda *a, **k: _FakeResponse(200, payload),
        )
        doi = search_crossref_for_doi("Deep Learning for Image Recognition")
        assert doi == "10.1/match"

    def test_returns_none_on_title_mismatch(self, monkeypatch):
        payload = {"message": {"items": [{"DOI": "10.1/x", "title": ["Totally Unrelated Work"]}]}}
        monkeypatch.setattr(
            doi_discovery.requests, "get",
            lambda *a, **k: _FakeResponse(200, payload),
        )
        assert search_crossref_for_doi("Deep Learning for Image Recognition") is None

    def test_short_title_skipped(self):
        assert search_crossref_for_doi("AI") is None

    def test_http_error_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            doi_discovery.requests, "get",
            lambda *a, **k: _FakeResponse(500, {}),
        )
        assert search_crossref_for_doi("A Reasonably Long Title Here") is None


class TestDiscoverDOI:
    def test_strategy1_text_doi_short_circuits(self, monkeypatch):
        # If a DOI is in the text, no online strategy must be called.
        monkeypatch.setattr(doi_discovery, "search_openalex_for_doi", lambda *a, **k: pytest.fail("called"))
        monkeypatch.setattr(doi_discovery, "search_crossref_for_doi", lambda *a, **k: pytest.fail("called"))
        assert discover_doi("doi: 10.1234/inline", title="x") == "10.1234/inline"

    def test_strategy2_arxiv(self, monkeypatch):
        monkeypatch.setattr(doi_discovery, "search_openalex_for_doi", lambda *a, **k: pytest.fail("called"))
        result = discover_doi("see arXiv:1803.10122 for details")
        assert result == "10.48550/arXiv.1803.10122"

    def test_strategy3_openalex(self, monkeypatch):
        monkeypatch.setattr(doi_discovery, "search_openalex_for_doi", lambda t, a, y: "10.2/oa")
        monkeypatch.setattr(doi_discovery, "search_crossref_for_doi", lambda *a, **k: pytest.fail("called"))
        assert discover_doi("no id here", title="Some Title") == "10.2/oa"

    def test_strategy4_crossref_fallback(self, monkeypatch):
        monkeypatch.setattr(doi_discovery, "search_openalex_for_doi", lambda t, a, y: None)
        monkeypatch.setattr(doi_discovery, "search_crossref_for_doi", lambda t, a: "10.3/cr")
        assert discover_doi("no id here", title="Some Title") == "10.3/cr"

    def test_returns_none_when_all_fail(self, monkeypatch):
        monkeypatch.setattr(doi_discovery, "search_openalex_for_doi", lambda t, a, y: None)
        monkeypatch.setattr(doi_discovery, "search_crossref_for_doi", lambda t, a: None)
        assert discover_doi("nothing", title="Title") is None
