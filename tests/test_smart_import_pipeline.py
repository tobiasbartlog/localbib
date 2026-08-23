"""Unit tests for services.smart_import_pipeline (issue #87).

The service is the PURE metadata half of the smart-import pipeline: it reads
PDF text, discovers DOI + CrossRef metadata (with the plausibility check),
applies ISBN/year/title fallbacks, runs the optional LLM validation, and
returns a structured ``ImportResult``. It performs no DB or SSE work, so every
external boundary is mocked here.

``run_pipeline`` is a generator that yields progress dicts and ``return``s the
``ImportResult`` via ``StopIteration.value`` — ``_drain`` collects both.
"""
from __future__ import annotations

from unittest.mock import patch

import services.smart_import_pipeline as sip
from services.smart_import_pipeline import ImportResult


def _drain(gen):
    """Exhaust a run_pipeline generator → (progress_events, ImportResult)."""
    events = []
    try:
        while True:
            events.append(next(gen))
    except StopIteration as stop:
        return events, stop.value


def test_returns_import_result_dataclass():
    with patch.object(sip, "extract_text_from_pdf", return_value="A Clear Title\nbody"), \
         patch.object(sip, "discover_doi", return_value=None):
        events, result = _drain(
            sip.run_pipeline("/tmp/x.pdf", "x.pdf", do_doi=True, do_validate=False)
        )
    assert isinstance(result, ImportResult)
    assert result.text == "A Clear Title\nbody"
    # First non-empty line becomes the title fallback.
    assert result.metadata["title"] == "A Clear Title"
    assert result.title_signal == "A Clear Title"
    assert result.doi_signal == ""
    assert result.crossref_plausible is False


def test_yields_progress_events():
    with patch.object(sip, "extract_text_from_pdf", return_value="Title\nbody"), \
         patch.object(sip, "discover_doi", return_value=None):
        events, _ = _drain(
            sip.run_pipeline("/tmp/x.pdf", "x.pdf", do_doi=True, do_validate=False)
        )
    assert all(e["type"] == "progress" for e in events)
    steps = [e["step"] for e in events]
    # Text extraction always reports; a no-DOI run still emits the doi step.
    assert "text" in steps
    assert "doi" in steps
    assert "validate" in steps  # "uebersprungen" branch still emits one


def test_title_fallback_uses_filename_when_text_blank():
    with patch.object(sip, "extract_text_from_pdf", return_value="   \n  \n"), \
         patch.object(sip, "discover_doi", return_value=None):
        _, result = _drain(
            sip.run_pipeline("/tmp/p.pdf", "MyPaper.pdf", do_doi=False, do_validate=False)
        )
    assert result.metadata["title"] == "MyPaper"


def test_do_doi_false_skips_discovery():
    with patch.object(sip, "extract_text_from_pdf", return_value="Some Title\nbody"), \
         patch.object(sip, "discover_doi") as mock_doi, \
         patch.object(sip, "fetch_crossref_metadata") as mock_cr:
        events, result = _drain(
            sip.run_pipeline("/tmp/x.pdf", "x.pdf", do_doi=False, do_validate=False)
        )
    mock_doi.assert_not_called()
    mock_cr.assert_not_called()
    assert result.metadata["doi"] == ""
    assert "doi" not in [e["step"] for e in events]


def test_plausible_crossref_metadata_is_merged():
    cr_title = "Machine Learning For Climate Modelling"
    pdf_text = f"{cr_title}\nThis study about climate modelling and learning."
    crossref_data = {
        "title": cr_title, "authors": "Doe, J.", "year": 2021,
        "doi": "10.1/abc", "abstract": "abs", "journal": "J", "publisher": "P",
    }
    with patch.object(sip, "extract_text_from_pdf", return_value=pdf_text), \
         patch.object(sip, "discover_doi", return_value="10.1/abc"), \
         patch.object(sip, "fetch_crossref_metadata", return_value=crossref_data):
        _, result = _drain(
            sip.run_pipeline("/tmp/x.pdf", "x.pdf", do_doi=True, do_validate=False)
        )
    assert result.crossref_plausible is True
    assert result.metadata["title"] == cr_title
    assert result.metadata["doi"] == "10.1/abc"
    assert result.metadata["authors"] == "Doe, J."
    assert result.metadata["year"] == 2021
    assert result.doi_signal == "10.1/abc"


def test_implausible_crossref_discards_doi():
    crossref_data = {
        "title": "Bananen Gurken Zucchini Tomaten Paprika",
        "authors": "Wrong, W.", "year": 2020,
        "doi": "10.9/mismatch", "abstract": "", "journal": "", "publisher": "",
    }
    pdf_text = "Quantum Mechanics And Superposition\nby Smith"
    with patch.object(sip, "extract_text_from_pdf", return_value=pdf_text), \
         patch.object(sip, "discover_doi", return_value="10.9/mismatch"), \
         patch.object(sip, "fetch_crossref_metadata", return_value=crossref_data):
        _, result = _drain(
            sip.run_pipeline("/tmp/x.pdf", "x.pdf", do_doi=True, do_validate=False)
        )
    # CrossRef title words absent from the PDF → overlap < 0.5 → DOI discarded.
    assert result.crossref_plausible is False
    assert result.metadata["doi"] == ""
    # Title falls back to the first PDF line, NOT the wrong CrossRef title.
    assert result.metadata["title"] == "Quantum Mechanics And Superposition"


def test_crossref_returns_nothing_emits_no_data_message():
    with patch.object(sip, "extract_text_from_pdf", return_value="Title\nbody"), \
         patch.object(sip, "discover_doi", return_value="10.1/x"), \
         patch.object(sip, "fetch_crossref_metadata", return_value=None):
        events, result = _drain(
            sip.run_pipeline("/tmp/x.pdf", "x.pdf", do_doi=True, do_validate=False)
        )
    # DOI is kept (only an implausible CrossRef title clears it).
    assert result.metadata["doi"] == "10.1/x"
    crossref_events = [e for e in events if e["step"] == "crossref"]
    assert crossref_events and "keine Daten" in crossref_events[-1]["message"]


def test_isbn_and_year_fallbacks_fill_empty_fields():
    with patch.object(sip, "extract_text_from_pdf", return_value="Title\nbody"), \
         patch.object(sip, "discover_doi", return_value=None), \
         patch.object(sip, "extract_isbn_from_text", return_value="978-3-16-148410-0"), \
         patch.object(sip, "extract_year_from_text", return_value=1999):
        _, result = _drain(
            sip.run_pipeline("/tmp/x.pdf", "x.pdf", do_doi=True, do_validate=False)
        )
    assert result.metadata["isbn"] == "978-3-16-148410-0"
    assert result.metadata["year"] == 1999
