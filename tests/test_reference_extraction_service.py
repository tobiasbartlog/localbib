"""Unit tests for services.reference_extraction (issue #88).

The service contains pure functions (no DB writes, no SSE, no FS mutations
beyond reading PDFs). External HTTP and LLM calls are mocked so the suite
runs fully offline.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import services.reference_extraction as ref_svc
from llm_client import LLMClient


def _stub_llm(return_value: str = "", side_effect=None) -> LLMClient:
    """Real LLMClient with .complete() stubbed, damit das echte
    complete_json-Parsing mitlaeuft; nur der HTTP-Call ist gefaked."""
    llm = LLMClient("http://fake-llm/v1", "test-model", "test-key")
    llm.complete = MagicMock(return_value=return_value, side_effect=side_effect)
    return llm


# ---------------------------------------------------------------------------
# extract_all_pdf_pages
# ---------------------------------------------------------------------------

class TestExtractAllPdfPages:
    def test_returns_page_texts(self, tmp_path):
        """Returns a list of page text strings (one per page)."""
        fake_page = MagicMock()
        fake_page.get_text.return_value = "Page content"
        fake_doc = MagicMock()
        fake_doc.__iter__ = MagicMock(return_value=iter([fake_page, fake_page]))

        with patch("fitz.open", return_value=fake_doc):
            result = ref_svc.extract_all_pdf_pages("/some/path.pdf")

        assert result == ["Page content", "Page content"]
        fake_doc.close.assert_called_once()

    def test_returns_empty_on_exception(self):
        """Returns [] rather than raising when fitz fails."""
        with patch("fitz.open", side_effect=Exception("corrupt PDF")):
            result = ref_svc.extract_all_pdf_pages("/bad/path.pdf")
        assert result == []


# ---------------------------------------------------------------------------
# find_reference_section
# ---------------------------------------------------------------------------

class TestFindReferenceSection:
    def test_empty_pages_returns_empty(self):
        assert ref_svc.find_reference_section([]) == ""

    def test_detects_references_header(self):
        """A page with 'References' heading is selected."""
        pages = [
            "Introduction and method text.",
            "References\n[1] Smith, A. (2020). Title. J. 10.1000/doi.\n[2] Doe, B. (2021). Another.\n",
        ]
        result = ref_svc.find_reference_section(pages)
        assert "References" in result

    def test_falls_back_to_last_pages(self):
        """With no header, falls back to a high-scoring tail section."""
        # Build a page with strong reference signals (many DOIs)
        doi_rich = "\n".join(f"Author (2020). Title {i}. J. 10.9999/{i}." for i in range(10))
        pages = ["intro page"] * 5 + [doi_rich]
        result = ref_svc.find_reference_section(pages)
        # Should include some content (either scored or fallback)
        assert len(result) > 0

    def test_single_page_returns_it(self):
        pages = ["Only page content"]
        result = ref_svc.find_reference_section(pages)
        assert "Only page content" in result


# ---------------------------------------------------------------------------
# llm_extract_references
# ---------------------------------------------------------------------------

class TestLlmExtractReferences:
    def test_returns_empty_when_no_api_key(self, monkeypatch):
        """Returns [] immediately when KICONNECT_API_KEY is not set."""
        from literature_manager import Config
        monkeypatch.setattr(Config, "KICONNECT_API_KEY", "")
        result = ref_svc.llm_extract_references("some ref text")
        assert result == []

    def test_calls_llm_client_and_parses_json(self, monkeypatch):
        """Happy path: LLM returns valid JSON array → list of dicts."""
        from literature_manager import Config
        monkeypatch.setattr(Config, "KICONNECT_API_KEY", "test-key")
        monkeypatch.setattr(Config, "KICONNECT_API_URL", "http://fake-llm/v1")
        monkeypatch.setattr(Config, "LLM_MODEL", "test-model")

        fake_response = '[{"title":"Paper A","authors":"Auth","year":2020,"journal":"J","doi":""}]'
        mock_llm = _stub_llm(fake_response)

        with patch("services.reference_extraction.llm_for", return_value=mock_llm):
            result = ref_svc.llm_extract_references("References section text " * 10)

        assert len(result) == 1
        assert result[0]["title"] == "Paper A"
        assert result[0]["year"] == 2020

    def test_chunks_long_text(self, monkeypatch):
        """Text longer than 15000 chars is split into chunks; all refs merged."""
        from literature_manager import Config
        monkeypatch.setattr(Config, "KICONNECT_API_KEY", "test-key")
        monkeypatch.setattr(Config, "KICONNECT_API_URL", "http://fake-llm/v1")
        monkeypatch.setattr(Config, "LLM_MODEL", "test-model")

        chunk_result = '[{"title":"Ref","authors":"A","year":2022,"journal":"","doi":""}]'
        mock_llm = _stub_llm(chunk_result)

        long_text = "Reference entry text.\n" * 800  # well over 15000 chars

        with patch("services.reference_extraction.llm_for", return_value=mock_llm):
            result = ref_svc.llm_extract_references(long_text)

        # Multiple chunks → multiple result dicts (same ref repeated)
        assert len(result) >= 2
        assert all(r["title"] == "Ref" for r in result)

    def test_llm_error_returns_empty(self, monkeypatch):
        """LLM exception → returns [] without re-raising."""
        from literature_manager import Config
        monkeypatch.setattr(Config, "KICONNECT_API_KEY", "test-key")
        monkeypatch.setattr(Config, "KICONNECT_API_URL", "http://fake-llm/v1")
        monkeypatch.setattr(Config, "LLM_MODEL", "test-model")

        mock_llm = _stub_llm(side_effect=RuntimeError("LLM error"))

        with patch("services.reference_extraction.llm_for", return_value=mock_llm):
            result = ref_svc.llm_extract_references("Some text")

        assert result == []

    def test_llm_non_list_response_returns_empty(self, monkeypatch):
        """If LLM returns a dict instead of a list, returns []."""
        from literature_manager import Config
        monkeypatch.setattr(Config, "KICONNECT_API_KEY", "test-key")
        monkeypatch.setattr(Config, "KICONNECT_API_URL", "http://fake-llm/v1")
        monkeypatch.setattr(Config, "LLM_MODEL", "test-model")

        mock_llm = _stub_llm('{"error": "not a list"}')

        with patch("services.reference_extraction.llm_for", return_value=mock_llm):
            result = ref_svc.llm_extract_references("Some text")

        assert result == []


# ---------------------------------------------------------------------------
# crossref_doi_lookup
# ---------------------------------------------------------------------------

class TestCrossrefDoiLookup:
    def test_returns_none_for_short_title(self):
        """Titles shorter than 10 chars are skipped without HTTP call."""
        result = ref_svc.crossref_doi_lookup("Short")
        assert result is None

    def test_returns_doi_on_match(self):
        """When CrossRef returns a title with >=65% word overlap, DOI is returned."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "message": {
                "items": [{"DOI": "10.1000/found", "title": ["A Great Paper About Science"]}]
            }
        }

        with patch("services.reference_extraction.requests.get", return_value=mock_resp):
            result = ref_svc.crossref_doi_lookup("A Great Paper About Science")

        assert result == "10.1000/found"

    def test_returns_none_on_low_similarity(self):
        """No DOI when CrossRef title doesn't match sufficiently."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "message": {
                "items": [{"DOI": "10.9999/x", "title": ["Completely Unrelated Topic Here"]}]
            }
        }

        with patch("services.reference_extraction.requests.get", return_value=mock_resp):
            result = ref_svc.crossref_doi_lookup("Something Totally Different Science Paper")

        assert result is None

    def test_returns_none_on_http_error(self):
        """Non-200 HTTP status → None without raising."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch("services.reference_extraction.requests.get", return_value=mock_resp):
            result = ref_svc.crossref_doi_lookup("A Long Enough Title For Lookup")

        assert result is None

    def test_returns_none_on_exception(self):
        """Network exception → None without re-raising."""
        with patch("services.reference_extraction.requests.get", side_effect=Exception("timeout")):
            result = ref_svc.crossref_doi_lookup("A Long Enough Title For Lookup")

        assert result is None

    def test_adds_first_author_to_query(self):
        """First author surname is appended to the query string."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": {"items": []}}

        with patch("services.reference_extraction.requests.get", return_value=mock_resp) as mock_get:
            ref_svc.crossref_doi_lookup(
                "A Long Enough Title For Lookup",
                authors="Mueller, Hans; Schmidt, Anna",
            )

        call_kwargs = mock_get.call_args
        query = call_kwargs[1]["params"]["query.bibliographic"]
        assert "Mueller" in query


# ---------------------------------------------------------------------------
# match_ref_to_library
# ---------------------------------------------------------------------------

class TestMatchRefToLibrary:
    def test_returns_match_dict(self):
        """Delegates to paper_matcher.match and returns {matched_paper_id, match_confidence}."""
        from paper_matcher import MatchResult

        mock_result = MatchResult(matched_paper_id=42, match_confidence=0.92, match_strategy="doi")
        mock_conn = MagicMock()

        with patch("services.reference_extraction._pm_match", return_value=mock_result):
            result = ref_svc.match_ref_to_library({"doi": "10.1/x", "title": "T"}, mock_conn)

        assert result == {"matched_paper_id": 42, "match_confidence": 0.92}

    def test_returns_no_match_dict(self):
        """No match → matched_paper_id is None, confidence 0.0."""
        from paper_matcher import MatchResult

        mock_result = MatchResult(matched_paper_id=None, match_confidence=0.0, match_strategy="none")
        mock_conn = MagicMock()

        with patch("services.reference_extraction._pm_match", return_value=mock_result):
            result = ref_svc.match_ref_to_library({"doi": "", "title": "Unknown"}, mock_conn)

        assert result["matched_paper_id"] is None
        assert result["match_confidence"] == 0.0
