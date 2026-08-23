"""Unit tests for the metadata_validation module.

Covers:
  - The four legacy strategies (CrossRefStrategy, LLMStrategy,
    OpenAlexStrategy, ArXivStrategy) — preserved while webapp still imports
    them directly.
  - The plausibility helper crossref_title_matches_text and the abstract
    helpers (fetch_crossref_abstract, llm_find_abstract_in_text,
    llm_generate_abstract).
  - The new top-level propose() entry point that produces a Proposal — the
    surface that will eventually replace the inline policy in
    webapp.validate_paper_metadata. The Proposal-level tests document the
    live policy branches (CrossRef-match, CrossRef title-mismatch DOI wipe,
    LLM title-override rules, LLM-DOI re-fetch chain, existing-DOI
    plausibility wipe, Confidence gating, category suggestions).

All HTTP calls are mocked via injectable callables or monkeypatched globals
on the metadata_validation module — no patching of literature_manager
internals is needed.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import metadata_validation
from metadata_validation import (
    ArXivStrategy,
    CrossRefStrategy,
    LLMStrategy,
    OpenAlexStrategy,
    Proposal,
    crossref_title_matches_text,
    fetch_crossref_abstract,
    llm_find_abstract_in_text,
    llm_generate_abstract,
    propose,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PDF_TEXT = (
    "Quantum Entanglement in Photon Pairs\n"
    "An exhaustive study of quantum entanglement in photon pairs across "
    "various experimental setups, including BBO crystals and SPDC."
)

CR_HIT_TITLE = "Quantum Entanglement in Photon Pairs"

CR_FULL = {
    "title": CR_HIT_TITLE,
    "authors": "Smith, Jane",
    "year": 2023,
    "abstract": "We study entanglement.",
    "journal": "Phys. Rev. Lett.",
    "publisher": "APS",
    "isbn": "",
}


def _stub_all_network(monkeypatch, **overrides):
    """Default every network function on metadata_validation to a no-op.

    Callers override individual hooks via kwargs, e.g.
    ``_stub_all_network(monkeypatch, discover_doi=lambda *a, **k: "10.1/x")``.
    """
    defaults = {
        "discover_doi": lambda *a, **k: None,
        "fetch_crossref_metadata": lambda d: None,
        "search_openalex_for_doi": lambda *a, **k: None,
        "extract_arxiv_id_from_text": lambda t: None,
        "resolve_doi_from_arxiv": lambda aid: None,
        "extract_isbn_from_text": lambda t: None,
        "extract_year_from_text": lambda t: None,
        "categorize_with_llm": lambda **kw: [],
    }
    defaults.update(overrides)
    for name, fn in defaults.items():
        monkeypatch.setattr(metadata_validation, name, fn)


# ---------------------------------------------------------------------------
# CrossRefStrategy
# ---------------------------------------------------------------------------

class TestCrossRefStrategy:
    def test_match_returns_fields_when_title_matches_pdf(self):
        cr = CrossRefStrategy(
            discover=lambda *a, **kw: "10.1/foo",
            fetch=lambda d: CR_FULL,
        )
        changes = cr.run(PDF_TEXT, {"title": "", "authors": "", "year": None})
        assert changes["doi"] == "10.1/foo"
        assert changes["title"] == CR_HIT_TITLE
        assert changes["authors"] == "Smith, Jane"
        assert changes["year"] == 2023
        assert changes["journal"] == "Phys. Rev. Lett."
        assert cr.last_matches_pdf is True
        assert cr.last_crossref_data == CR_FULL

    def test_mismatch_discards_crossref_and_blanks_doi(self):
        bad = {**CR_FULL, "title": "Treatise on Marine Biology of the Pacific"}
        cr = CrossRefStrategy(
            discover=lambda *a, **kw: "10.1/wrong",
            fetch=lambda d: bad,
        )
        changes = cr.run(PDF_TEXT, {})
        assert changes.get("doi") == ""
        assert "title" not in changes
        assert cr.last_matches_pdf is False
        assert cr.last_crossref_data is None

    def test_no_doi_discovered_returns_empty(self):
        cr = CrossRefStrategy(discover=lambda *a, **kw: None, fetch=lambda d: None)
        assert cr.run(PDF_TEXT, {}) == {}
        assert cr.last_doi is None

    def test_doi_without_crossref_metadata(self):
        cr = CrossRefStrategy(
            discover=lambda *a, **kw: "10.5281/zenodo.123",
            fetch=lambda d: None,
        )
        changes = cr.run(PDF_TEXT, {})
        assert changes == {"doi": "10.5281/zenodo.123"}

    def test_respects_manual_doi_verification(self):
        cr = CrossRefStrategy(
            discover=lambda *a, **kw: "10.1/foo",
            fetch=lambda d: CR_FULL,
        )
        changes = cr.run(
            PDF_TEXT,
            {"doi": "10.MANUAL/x", "doi_manually_verified": 1},
        )
        assert "doi" not in changes
        assert changes["title"] == CR_HIT_TITLE


# ---------------------------------------------------------------------------
# LLMStrategy
# ---------------------------------------------------------------------------

class TestLLMStrategy:
    def test_extract_calls_client_with_prompt_containing_text(self):
        client = MagicMock()
        client.complete.return_value = json.dumps(
            {"title": "LLM Title", "authors": "LLM Authors", "year": 2022}
        )
        long_text = "some pdf body text here that is plenty long enough to count as a real PDF body"
        strat = LLMStrategy(client)
        data = strat.extract(long_text, "old title", "old authors")
        assert data == {"title": "LLM Title", "authors": "LLM Authors", "year": 2022}
        prompt = client.complete.call_args[0][0][0]["content"]
        assert "some pdf body text here" in prompt
        assert "old title" in prompt

    def test_extract_returns_empty_on_invalid_json(self):
        client = MagicMock()
        client.complete.return_value = "not-json-at-all"
        assert LLMStrategy(client).extract("text", "", "") == {}

    def test_extract_returns_empty_without_client(self):
        assert LLMStrategy(None).extract("text", "", "") == {}

    def test_run_only_fills_missing_paper_fields(self):
        client = MagicMock()
        client.complete.return_value = json.dumps(
            {"title": "LLM Title", "authors": "LLM Authors", "doi": "10.x/llm"}
        )
        out = LLMStrategy(client).run(
            "some text body that the strategy will pass in",
            {"title": "Existing"},
        )
        assert "title" not in out
        assert out["authors"] == "LLM Authors"
        assert out["doi"] == "10.x/llm"

    def test_run_no_client_yields_empty(self):
        assert LLMStrategy(None).run("text", {}) == {}

    def test_hints_appear_in_prompt(self):
        client = MagicMock()
        client.complete.return_value = "{}"
        LLMStrategy(client, hints={"filename": "foo.pdf"}).extract("text", "", "")
        prompt = client.complete.call_args[0][0][0]["content"]
        assert "foo.pdf" in prompt


# ---------------------------------------------------------------------------
# OpenAlexStrategy / ArXivStrategy
# ---------------------------------------------------------------------------

class TestOpenAlexStrategy:
    def test_returns_doi_when_search_succeeds(self):
        strat = OpenAlexStrategy(search=lambda t, a, y: "10.5/openalex")
        out = strat.run("text", {"title": "Some title with enough words"})
        assert out == {"doi": "10.5/openalex"}

    def test_skipped_when_paper_already_has_doi(self):
        called = []
        strat = OpenAlexStrategy(search=lambda *a: called.append(a) or "10.x/y")
        assert strat.run("text", {"doi": "10.1/existing", "title": "T"}) == {}
        assert called == []

    def test_skipped_when_no_title(self):
        strat = OpenAlexStrategy(search=lambda *a: "10.x/y")
        assert strat.run("text", {}) == {}


class TestArXivStrategy:
    def test_resolves_arxiv_id_to_doi(self):
        strat = ArXivStrategy(
            extract=lambda t: "1803.10122",
            resolve=lambda aid: "10.48550/arXiv.1803.10122",
        )
        out = strat.run("...arXiv:1803.10122...", {})
        assert out == {"doi": "10.48550/arXiv.1803.10122"}

    def test_no_arxiv_id_returns_empty(self):
        strat = ArXivStrategy(extract=lambda t: None, resolve=lambda aid: "x")
        assert strat.run("text without ids", {}) == {}

    def test_skipped_when_paper_already_has_doi(self):
        strat = ArXivStrategy(extract=lambda t: "1803.10122", resolve=lambda aid: "x")
        assert strat.run("text", {"doi": "10.1/existing"}) == {}


# ---------------------------------------------------------------------------
# crossref_title_matches_text
# ---------------------------------------------------------------------------

class TestTitleMatch:
    def test_match_significant_overlap(self):
        assert crossref_title_matches_text(
            "Quantum Entanglement in Photon Pairs",
            PDF_TEXT,
        )

    def test_no_match_unrelated(self):
        assert not crossref_title_matches_text(
            "Treatise on Marine Biology of the Pacific Region",
            PDF_TEXT,
        )

    def test_only_stopwords_returns_true(self):
        assert crossref_title_matches_text("The of the in", PDF_TEXT)


# ---------------------------------------------------------------------------
# Abstract helpers
# ---------------------------------------------------------------------------

class TestAbstractHelpers:
    def test_fetch_crossref_abstract_returns_field(self):
        out = fetch_crossref_abstract("10.1/x", fetch=lambda d: {"abstract": "  Hello.  "})
        assert out == "Hello."

    def test_fetch_crossref_abstract_empty_on_missing(self):
        assert fetch_crossref_abstract("10.1/x", fetch=lambda d: None) == ""
        assert fetch_crossref_abstract("10.1/x", fetch=lambda d: {"abstract": ""}) == ""
        assert fetch_crossref_abstract("", fetch=lambda d: {"abstract": "x"}) == ""

    def test_llm_find_returns_text_when_present(self):
        client = MagicMock()
        client.complete.return_value = "This is the abstract."
        assert llm_find_abstract_in_text(client, "T", "A", "body") == "This is the abstract."

    def test_llm_find_returns_empty_on_kein_abstract_signal(self):
        client = MagicMock()
        client.complete.return_value = "KEIN_ABSTRACT"
        assert llm_find_abstract_in_text(client, "T", "A", "body") == ""

    def test_llm_generate_returns_completion(self):
        client = MagicMock()
        client.complete.return_value = "Generated summary."
        assert llm_generate_abstract(client, "T", "A", "body") == "Generated summary."

    def test_llm_generate_swallows_errors(self):
        client = MagicMock()
        client.complete.side_effect = RuntimeError("network down")
        assert llm_generate_abstract(client, "T", "A", "body") == ""


# ---------------------------------------------------------------------------
# propose() — the new entry point
# ---------------------------------------------------------------------------

class TestProposeCrossRef:
    def test_crossref_match_high_confidence(self, monkeypatch):
        _stub_all_network(
            monkeypatch,
            discover_doi=lambda *a, **k: "10.1/foo",
            fetch_crossref_metadata=lambda d: CR_FULL,
        )
        prop = propose({"title": "", "authors": "", "doi": ""}, PDF_TEXT)
        assert prop.changes["title"] == CR_HIT_TITLE
        assert prop.changes["doi"] == "10.1/foo"
        assert prop.source_per_field["title"] == "crossref"
        assert prop.confidence == "high"
        assert prop.warnings == []

    def test_crossref_mismatch_low_confidence_and_warning(self, monkeypatch):
        # Paper already has a (different) DOI so the mismatch-wipe is a real
        # diff. If the Paper had no DOI to begin with, "clearing" it is a
        # no-op and the diff trim correctly strips it from changes.
        bad = {**CR_FULL, "title": "Treatise on Marine Biology of the Pacific"}
        _stub_all_network(
            monkeypatch,
            discover_doi=lambda *a, **k: "10.1/wrong",
            fetch_crossref_metadata=lambda d: bad,
        )
        prop = propose({"title": "", "authors": "", "doi": "10.OLD/keep"}, PDF_TEXT)
        assert prop.changes.get("doi") == ""
        assert prop.confidence == "low"
        assert any("did not match PDF" in w for w in prop.warnings)
        assert prop.source_per_field["doi"] == "doi-discovery"

    def test_no_doi_resolved_is_low_confidence(self, monkeypatch):
        _stub_all_network(monkeypatch)
        prop = propose({"title": "Existing", "authors": "Doe, Jane", "doi": ""}, PDF_TEXT)
        assert prop.confidence == "low"
        assert prop.changes == {}


class TestProposeFallbacks:
    def test_isbn_fallback_from_text(self, monkeypatch):
        _stub_all_network(monkeypatch, extract_isbn_from_text=lambda t: "978-3-16-148410-0")
        prop = propose({"title": "T", "authors": "A", "doi": "10.1/x"}, PDF_TEXT)
        assert prop.changes["isbn"] == "978-3-16-148410-0"
        assert prop.source_per_field["isbn"] == "text"

    def test_year_fallback_from_text(self, monkeypatch):
        _stub_all_network(monkeypatch, extract_year_from_text=lambda t: 2021)
        prop = propose({"title": "T", "authors": "A", "doi": "10.1/x"}, PDF_TEXT)
        assert prop.changes["year"] == 2021
        assert prop.source_per_field["year"] == "text"


class TestProposeLLMOverride:
    @staticmethod
    def _client(llm_payload):
        client = MagicMock()
        client.complete.return_value = json.dumps(llm_payload)
        return client

    def test_llm_override_when_current_title_short(self, monkeypatch):
        _stub_all_network(monkeypatch)
        client = self._client({"title": "Quantum Entanglement in Photon Pairs"})
        prop = propose(
            {"title": "abc", "authors": "", "doi": "10.1/x"},
            PDF_TEXT,
            llm_client=client,
        )
        assert prop.changes["title"] == "Quantum Entanglement in Photon Pairs"
        assert prop.source_per_field["title"] == "llm"
        assert any("overriding existing title" in w for w in prop.warnings)
        assert prop.confidence == "low"

    def test_llm_override_when_current_title_has_underscores(self, monkeypatch):
        _stub_all_network(monkeypatch)
        client = self._client({"title": "Real Title"})
        prop = propose(
            {"title": "SIA_D0111_Lehmbauregeln", "authors": "", "doi": "10.1/x"},
            PDF_TEXT,
            llm_client=client,
        )
        assert prop.changes["title"] == "Real Title"
        assert prop.source_per_field["title"] == "llm"

    def test_no_llm_override_when_crossref_matched_and_title_ok(self, monkeypatch):
        _stub_all_network(
            monkeypatch,
            discover_doi=lambda *a, **k: "10.1/foo",
            fetch_crossref_metadata=lambda d: CR_FULL,
        )
        client = self._client({"title": "Different Title From LLM"})
        prop = propose({"title": "Existing Long Title", "doi": ""}, PDF_TEXT, llm_client=client)
        assert prop.changes["title"] == CR_HIT_TITLE
        assert prop.source_per_field["title"] == "crossref"
        assert prop.confidence == "high"

    def test_llm_doi_chain_refetches_crossref(self, monkeypatch):
        calls = []

        def _fetch(d):
            calls.append(d)
            if d == "10.1/from-llm":
                return {
                    "title": "Chained Title",
                    "abstract": "Chained abstract.",
                    "journal": "Chained Journal",
                }
            return None

        _stub_all_network(monkeypatch, fetch_crossref_metadata=_fetch)
        client = self._client({"doi": "10.1/from-llm"})
        prop = propose({"title": "A long enough title", "doi": ""}, PDF_TEXT, llm_client=client)
        assert prop.changes["doi"] == "10.1/from-llm"
        assert prop.source_per_field["doi"] == "llm"
        assert prop.changes["abstract"] == "Chained abstract."
        assert prop.source_per_field["abstract"] == "llm+crossref"
        assert "10.1/from-llm" in calls


class TestProposeExistingDOIPlausibility:
    def test_existing_doi_wiped_when_pdf_disagrees(self, monkeypatch):
        bad = {"title": "Totally Unrelated Paper About Geology"}
        _stub_all_network(monkeypatch, fetch_crossref_metadata=lambda d: bad)
        prop = propose(
            {"title": "Quantum Entanglement in Photon Pairs", "doi": "10.1/old"},
            PDF_TEXT,
        )
        assert prop.changes.get("doi") == ""
        assert prop.source_per_field["doi"] == "rejected"
        assert any("did not match PDF" in w for w in prop.warnings)
        assert prop.confidence == "low"

    def test_existing_doi_preserved_when_manually_verified(self, monkeypatch):
        bad = {"title": "Totally Unrelated Paper About Geology"}
        _stub_all_network(monkeypatch, fetch_crossref_metadata=lambda d: bad)
        prop = propose(
            {
                "title": "Quantum Entanglement in Photon Pairs",
                "doi": "10.1/manual",
                "doi_manually_verified": 1,
            },
            PDF_TEXT,
        )
        assert "doi" not in prop.changes


class TestProposeCategorySuggestions:
    def test_categories_returned_when_inputs_supplied(self, monkeypatch):
        called = {}

        def _cat(**kw):
            called.update(kw)
            return [{"category_id": 7, "confidence": 0.91}]

        _stub_all_network(
            monkeypatch,
            discover_doi=lambda *a, **k: "10.1/foo",
            fetch_crossref_metadata=lambda d: CR_FULL,
            categorize_with_llm=_cat,
        )
        client = MagicMock()
        client.complete.return_value = "{}"
        prop = propose(
            {"title": "", "doi": ""},
            PDF_TEXT,
            llm_client=client,
            category_tree="root/leaf",
            categories_json=[{"id": 7, "name": "X"}],
        )
        assert prop.category_suggestions == [{"category_id": 7, "confidence": 0.91}]
        assert called["category_tree"] == "root/leaf"

    def test_categories_empty_without_tree(self, monkeypatch):
        _stub_all_network(
            monkeypatch,
            discover_doi=lambda *a, **k: "10.1/foo",
            fetch_crossref_metadata=lambda d: CR_FULL,
        )
        client = MagicMock()
        client.complete.return_value = "{}"
        prop = propose({"title": "", "doi": ""}, PDF_TEXT, llm_client=client)
        assert prop.category_suggestions == []

    def test_categories_empty_without_llm_client(self, monkeypatch):
        _stub_all_network(
            monkeypatch,
            discover_doi=lambda *a, **k: "10.1/foo",
            fetch_crossref_metadata=lambda d: CR_FULL,
        )
        prop = propose(
            {"title": "", "doi": ""},
            PDF_TEXT,
            category_tree="root/leaf",
            categories_json=[{"id": 7, "name": "X"}],
        )
        assert prop.category_suggestions == []


class TestProposeDiffTrimming:
    def test_unchanged_fields_dropped_from_changes(self, monkeypatch):
        _stub_all_network(
            monkeypatch,
            discover_doi=lambda *a, **k: "10.1/foo",
            fetch_crossref_metadata=lambda d: {"title": CR_HIT_TITLE, "authors": "Smith, Jane"},
        )
        prop = propose(
            {"title": CR_HIT_TITLE, "authors": "", "doi": ""},
            PDF_TEXT,
        )
        assert "title" not in prop.changes
        assert prop.changes.get("authors") == "Smith, Jane"
        assert prop.changes.get("doi") == "10.1/foo"
        assert "title" not in prop.source_per_field
        assert set(prop.current.keys()) == set(prop.changes.keys())

    def test_diagnostics_carry_raw_payloads(self, monkeypatch):
        _stub_all_network(
            monkeypatch,
            discover_doi=lambda *a, **k: "10.1/foo",
            fetch_crossref_metadata=lambda d: CR_FULL,
        )
        client = MagicMock()
        client.complete.return_value = json.dumps({"title": "X"})
        prop = propose({"title": "", "doi": ""}, PDF_TEXT, llm_client=client)
        assert prop.diagnostics["crossref_data"] == CR_FULL
        assert prop.diagnostics["llm_data"] == {"title": "X"}


class TestProposeNoLLM:
    def test_no_llm_client_skips_llm_step(self, monkeypatch):
        _stub_all_network(monkeypatch)
        prop = propose({"title": "T", "doi": ""}, PDF_TEXT, llm_client=None)
        assert prop.diagnostics["llm_data"] == {}
