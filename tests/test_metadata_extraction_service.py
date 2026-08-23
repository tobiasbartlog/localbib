"""Unit tests for services.metadata_extraction.

Covers both exported pure functions:
  - ``llm_extract_metadata`` — LLM-based title/author/DOI/year/ISBN extraction.
  - ``llm_suggest_category`` — LLM-based category keyword/description suggestion.

All LLM calls are mocked via ``unittest.mock.MagicMock``; no network calls.
The functions must be side-effect-free (no DB, no FS, no logging config).

Prior art: test_metadata_validation.py, test_llm_client.py.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from llm_client import LLMClient
from services.metadata_extraction import llm_extract_metadata, llm_suggest_category


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_llm(return_value: str) -> LLMClient:
    """Real LLMClient with .complete() stubbed — so the real complete_json
    parsing runs, only the HTTP call is faked."""
    llm = LLMClient("http://fake-llm/v1", "test-model", "test-key")
    llm.complete = MagicMock(return_value=return_value)
    return llm


# ===========================================================================
# llm_extract_metadata
# ===========================================================================

class TestLlmExtractMetadata:
    def test_returns_empty_when_client_is_none(self):
        assert llm_extract_metadata(None, "some text", "Old Title", "Old Author") == {}

    def test_parses_json_from_llm_response(self):
        payload = {"title": "New Title", "authors": "Smith, John", "year": 2023}
        llm = _mock_llm(json.dumps(payload))
        result = llm_extract_metadata(llm, "some pdf text", "Old Title", "Old Author")
        assert result == payload

    def test_returns_empty_on_invalid_json(self):
        llm = _mock_llm("not-json-at-all")
        result = llm_extract_metadata(llm, "text", "T", "A")
        assert result == {}

    def test_returns_empty_on_llm_exception(self):
        llm = _mock_llm("")
        llm.complete.side_effect = RuntimeError("network down")
        result = llm_extract_metadata(llm, "text", "T", "A")
        assert result == {}

    def test_prompt_contains_pdf_text_when_long_enough(self):
        llm = _mock_llm("{}")
        text = "A" * 100  # more than 50 chars
        llm_extract_metadata(llm, text, "T", "A")
        prompt = llm.complete.call_args[0][0][0]["content"]
        assert "PDF-TEXT (erste Seiten)" in prompt
        assert "A" * 50 in prompt  # text appears in prompt

    def test_prompt_signals_scanned_pdf_when_text_short(self):
        llm = _mock_llm("{}")
        llm_extract_metadata(llm, "short", "T", "A")  # len("short") < 50
        prompt = llm.complete.call_args[0][0][0]["content"]
        assert "Kein Text extrahierbar" in prompt

    def test_prompt_contains_current_title_and_authors(self):
        llm = _mock_llm("{}")
        llm_extract_metadata(llm, "A" * 100, "Current Title XYZ", "Current Author ABC")
        prompt = llm.complete.call_args[0][0][0]["content"]
        assert "Current Title XYZ" in prompt
        assert "Current Author ABC" in prompt

    def test_prompt_contains_filename_hint(self):
        llm = _mock_llm("{}")
        llm_extract_metadata(
            llm, "text", "T", "A",
            filename="paper_v2.pdf",
            original_filename="original.pdf",
        )
        prompt = llm.complete.call_args[0][0][0]["content"]
        assert "paper_v2.pdf" in prompt
        assert "original.pdf" in prompt

    def test_prompt_omits_filename_hint_when_both_empty(self):
        llm = _mock_llm("{}")
        llm_extract_metadata(llm, "text", "T", "A", filename="", original_filename="")
        prompt = llm.complete.call_args[0][0][0]["content"]
        assert "DATEINAME" not in prompt

    def test_passes_timeout_60_to_llm(self):
        llm = _mock_llm("{}")
        llm_extract_metadata(llm, "text", "T", "A")
        _, kwargs = llm.complete.call_args
        assert kwargs.get("timeout") == 60

    def test_returns_all_fields_from_payload(self):
        payload = {
            "title": "T", "authors": "A", "year": 2020,
            "doi": "10.1/x", "isbn": "978-0-0", "arxiv_id": "1234.5678",
        }
        llm = _mock_llm(json.dumps(payload))
        result = llm_extract_metadata(llm, "some text here with enough chars", "Old", "Old")
        assert result == payload

    def test_only_filename_shown_when_same_as_original(self):
        """When filename == original_filename, original not repeated in prompt."""
        llm = _mock_llm("{}")
        llm_extract_metadata(
            llm, "text", "T", "A",
            filename="same.pdf",
            original_filename="same.pdf",
        )
        prompt = llm.complete.call_args[0][0][0]["content"]
        assert "same.pdf" in prompt
        # Should not say "ORIGINAL-DATEINAME" when they are identical
        assert "ORIGINAL-DATEINAME" not in prompt


# ===========================================================================
# llm_suggest_category
# ===========================================================================

class TestLlmSuggestCategory:
    def test_returns_empty_when_client_is_none(self):
        assert llm_suggest_category(None, "Machine Learning", []) == {}

    def test_parses_description_and_keywords(self):
        payload = {
            "description": "Methods for teaching machines.",
            "keywords": "neural network, deep learning, gradient descent",
        }
        llm = _mock_llm(json.dumps(payload))
        result = llm_suggest_category(llm, "Machine Learning", [])
        assert result == payload

    def test_returns_empty_on_invalid_json(self):
        llm = _mock_llm("not-json")
        assert llm_suggest_category(llm, "ML", []) == {}

    def test_returns_empty_on_llm_exception(self):
        llm = _mock_llm("")
        llm.complete.side_effect = RuntimeError("timeout")
        assert llm_suggest_category(llm, "ML", []) == {}

    def test_prompt_contains_category_name(self):
        llm = _mock_llm("{}")
        llm_suggest_category(llm, "Quantum Computing", [])
        prompt = llm.complete.call_args[0][0][0]["content"]
        assert "Quantum Computing" in prompt

    def test_prompt_contains_existing_categories(self):
        llm = _mock_llm("{}")
        existing = [
            {"name": "ML", "description": "Machine Learning", "keywords": "ml, ai"},
            {"name": "CV", "description": "Computer Vision", "keywords": "image"},
        ]
        llm_suggest_category(llm, "NLP", existing)
        prompt = llm.complete.call_args[0][0][0]["content"]
        assert "ML" in prompt
        assert "Computer Vision" in prompt

    def test_prompt_contains_empty_category_when_no_existing(self):
        """No existing categories → prompt still works (empty section)."""
        llm = _mock_llm("{}")
        llm_suggest_category(llm, "New Cat", [])
        prompt = llm.complete.call_args[0][0][0]["content"]
        assert "New Cat" in prompt

    def test_passes_timeout_60_to_llm(self):
        llm = _mock_llm("{}")
        llm_suggest_category(llm, "Cat", [])
        _, kwargs = llm.complete.call_args
        assert kwargs.get("timeout") == 60

    def test_existing_category_fields_all_appear_in_prompt(self):
        """name, description, and keywords should all appear."""
        llm = _mock_llm("{}")
        existing = [{"name": "UniqueNameXYZ", "description": "UniqueDescABC", "keywords": "UniqueKWD"}]
        llm_suggest_category(llm, "TestCat", existing)
        prompt = llm.complete.call_args[0][0][0]["content"]
        assert "UniqueNameXYZ" in prompt
        assert "UniqueDescABC" in prompt
        assert "UniqueKWD" in prompt
