"""Unit tests for services/thesis_analyzer.py (PURE service).

Tests:
- extract_author_names: parse last-names from author strings
- match_citations_to_references: citation ↔ reference matching
- check_online_availability: HTTP mocked via patch, no real network

All tests are deterministic and require no DB, no fixtures, no FastAPI app.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import services.thesis_analyzer as _svc


# ===========================================================================
# extract_author_names
# ===========================================================================


class TestExtractAuthorNames:

    def test_empty_string_returns_empty(self):
        assert _svc.extract_author_names("") == []

    def test_none_returns_empty(self):
        assert _svc.extract_author_names(None) == []  # type: ignore[arg-type]

    def test_whitespace_only_returns_empty(self):
        assert _svc.extract_author_names("   ") == []

    def test_lastname_comma_firstname(self):
        assert _svc.extract_author_names("Mueller, Hans") == ["Mueller"]

    def test_multiple_authors_semicolon(self):
        names = _svc.extract_author_names("Mueller, Hans; Schmidt, Anna")
        assert "Mueller" in names
        assert "Schmidt" in names
        assert len(names) == 2

    def test_abbreviated_initials_extract_lastname(self):
        names = _svc.extract_author_names("Mueller, H.; Doe, J.")
        assert "Mueller" in names
        assert "Doe" in names

    def test_firstname_lastname_format_uses_last_word(self):
        names = _svc.extract_author_names("Hans Mueller")
        assert "Mueller" in names

    def test_and_separator(self):
        names = _svc.extract_author_names("Mueller and Schmidt")
        assert "Mueller" in names
        assert "Schmidt" in names

    def test_ampersand_separator(self):
        names = _svc.extract_author_names("Mueller & Schmidt")
        assert "Mueller" in names
        assert "Schmidt" in names

    def test_german_und_separator(self):
        names = _svc.extract_author_names("Mueller und Schmidt")
        assert "Mueller" in names
        assert "Schmidt" in names

    def test_single_letter_names_excluded(self):
        """Names shorter than 2 chars are excluded."""
        names = _svc.extract_author_names("X, Y")
        assert names == []

    def test_three_authors(self):
        names = _svc.extract_author_names("Alpha, A.; Beta, B.; Gamma, G.")
        assert len(names) == 3
        assert "Alpha" in names
        assert "Beta" in names
        assert "Gamma" in names


# ===========================================================================
# match_citations_to_references
# ===========================================================================


def _ref(title="Paper", authors="Mueller, Hans", year=2020, doi="", index=1) -> dict:
    return {"title": title, "authors": authors, "year": year, "doi": doi, "_ref_index": index}


class TestMatchCitationsToReferences:

    def test_empty_refs_returns_empty_lists(self):
        used, unused = _svc.match_citations_to_references([], "some body text")
        assert used == []
        assert unused == []

    def test_harvard_round_parens_detected(self):
        refs = [_ref(authors="Mueller, Hans", year=2020)]
        used, unused = _svc.match_citations_to_references(refs, "(Mueller, 2020) showed …")
        assert len(used) == 1
        assert unused == []

    def test_narrative_style_detected(self):
        refs = [_ref(authors="Mueller, Hans", year=2020)]
        used, unused = _svc.match_citations_to_references(refs, "Mueller (2020) argued that …")
        assert len(used) == 1
        assert unused == []

    def test_uncited_ref_is_unused(self):
        refs = [_ref(authors="Doe, Bob", year=2021, title="Uncited Paper")]
        used, unused = _svc.match_citations_to_references(refs, "No mention of anything relevant.")
        assert used == []
        assert len(unused) == 1

    def test_numbered_citation_triggers_via_multi_element_bracket(self):
        """[1, 2, 3] (multi-element bracket) → numbered matching engaged."""
        refs = [
            _ref(authors="Alpha, A.", year=2020, index=1),
            _ref(authors="Beta, B.", year=2021, index=2),
            _ref(authors="Gamma, G.", year=2022, index=3),
        ]
        used, unused = _svc.match_citations_to_references(refs, "See [1, 2, 3] for details.")
        assert len(used) == 3
        assert unused == []

    def test_range_numbered_citations_expand(self):
        refs = [
            _ref(authors="Alpha, A.", year=2020, index=1),
            _ref(authors="Beta, B.", year=2021, index=2),
            _ref(authors="Gamma, G.", year=2022, index=3),
        ]
        used, unused = _svc.match_citations_to_references(refs, "These works [1-3] are crucial.")
        assert len(used) == 3

    def test_empty_body_text_all_unused(self):
        refs = [
            _ref(authors="Alpha, A.", year=2020, index=1),
            _ref(authors="Beta, B.", year=2021, index=2),
        ]
        used, unused = _svc.match_citations_to_references(refs, "")
        assert used == []
        assert len(unused) == 2

    def test_used_in_text_flag_true_on_used(self):
        refs = [_ref(authors="Mueller, Hans", year=2020)]
        used, _ = _svc.match_citations_to_references(refs, "(Mueller, 2020) key study.")
        assert used[0]["_used_in_text"] is True

    def test_used_in_text_flag_false_on_unused(self):
        refs = [_ref(authors="Doe, Bob", year=2021, title="Uncited Work")]
        _, unused = _svc.match_citations_to_references(refs, "No mention.")
        assert unused[0]["_used_in_text"] is False

    def test_mixed_used_and_unused(self):
        refs = [
            _ref(authors="Mueller, Hans", year=2020, index=1),
            _ref(authors="Smith, John", year=2019, index=2),
            _ref(authors="Doe, Bob", year=2021, title="Never Cited Paper X Y Z", index=3),
        ]
        body = "(Mueller, 2020) is cited. Smith (2019) also mentioned."
        used, unused = _svc.match_citations_to_references(refs, body)
        assert len(used) == 2
        assert len(unused) == 1
        assert unused[0]["authors"] == "Doe, Bob"

    def test_result_is_partition(self):
        """used + unused must together equal all refs."""
        refs = [_ref(index=i) for i in range(1, 6)]
        body = "(Mueller, 2020) is cited."
        used, unused = _svc.match_citations_to_references(refs, body)
        assert len(used) + len(unused) == len(refs)


# ===========================================================================
# check_online_availability
# ===========================================================================


class TestCheckOnlineAvailability:

    def _make_ref(self, doi="", title="Climate Change Study", authors="Mueller, Hans"):
        return {"doi": doi, "title": title, "authors": authors}

    def test_returns_dict_with_required_keys(self):
        with patch("services.thesis_analyzer.http_requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=404)
            result = _svc.check_online_availability(self._make_ref())
        assert "online_found" in result
        assert "doi_found" in result
        assert "source" in result
        assert "openalex_id" in result

    def test_found_via_doi_redirect(self):
        """A 301 from doi.org → online_found=True, source='doi'."""
        ref = self._make_ref(doi="10.1000/test")
        with patch("services.thesis_analyzer.http_requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=301)
            result = _svc.check_online_availability(ref)
        assert result["online_found"] is True
        assert result["source"] == "doi"
        assert result["doi_found"] == "10.1000/test"

    def test_not_found_when_doi_returns_404(self):
        ref = self._make_ref(doi="10.9999/nonexistent")
        with patch("services.thesis_analyzer.http_requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=404)
            # Also stub crossref to return nothing
            with patch("services.thesis_analyzer._crossref_doi_lookup", return_value=None):
                with patch("services.thesis_analyzer.OpenAlexClient") as mock_oa_cls:
                    mock_oa_cls.return_value.fetch_work_by_title.return_value = None
                    result = _svc.check_online_availability(ref)
        assert result["online_found"] is False

    def test_found_via_crossref_fallback(self):
        """When DOI check fails, CrossRef lookup returns a DOI → found."""
        ref = self._make_ref(title="A Real Paper About Something Specific")
        with patch("services.thesis_analyzer.http_requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=404)
            with patch("services.thesis_analyzer._crossref_doi_lookup", return_value="10.1234/found"):
                result = _svc.check_online_availability(ref)
        assert result["online_found"] is True
        assert result["source"] == "crossref"
        assert result["doi_found"] == "10.1234/found"

    def test_found_via_openalex_fallback(self):
        """When DOI + CrossRef both fail, OpenAlex title search finds a work."""
        ref = self._make_ref(title="A Real Paper About Climate Change")
        mock_work = MagicMock()
        mock_work.doi = "10.5678/oa"
        mock_work.id = "https://openalex.org/W123"
        with patch("services.thesis_analyzer.http_requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=404)
            with patch("services.thesis_analyzer._crossref_doi_lookup", return_value=None):
                with patch("services.thesis_analyzer.OpenAlexClient") as mock_oa_cls:
                    mock_oa_cls.return_value.fetch_work_by_title.return_value = mock_work
                    result = _svc.check_online_availability(ref)
        assert result["online_found"] is True
        assert result["source"] == "openalex"
        assert result["openalex_id"] == "https://openalex.org/W123"

    def test_network_exception_returns_not_found(self):
        """HTTP error is silently absorbed; result is not_found."""
        ref = self._make_ref(doi="10.1000/broken")
        with patch("services.thesis_analyzer.http_requests.get", side_effect=Exception("timeout")):
            with patch("services.thesis_analyzer._crossref_doi_lookup", return_value=None):
                with patch("services.thesis_analyzer.OpenAlexClient") as mock_oa_cls:
                    mock_oa_cls.return_value.fetch_work_by_title.return_value = None
                    result = _svc.check_online_availability(ref)
        assert result["online_found"] is False

    def test_empty_ref_no_crash(self):
        """An empty ref dict must not raise."""
        with patch("services.thesis_analyzer.http_requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=404)
            with patch("services.thesis_analyzer._crossref_doi_lookup", return_value=None):
                with patch("services.thesis_analyzer.OpenAlexClient") as mock_oa_cls:
                    mock_oa_cls.return_value.fetch_work_by_title.return_value = None
                    result = _svc.check_online_availability({})
        assert isinstance(result, dict)
        assert result["online_found"] is False
