"""Characterization tests for /api/analysis/thesis.

Freezes TODAY's behaviour of:
  POST /api/analysis/thesis

Covers: author-name extraction (_extract_author_names), citation-to-reference
matching (_match_citations_to_references), online-availability checking
(mocked), HTTP response shape, summary counts, and no-DB-side-effect guarantee.

External HTTP (doi.org, CrossRef, OpenAlex via http_requests and
_check_online_availability) is mocked via patch.object — no network calls,
fully deterministic.

Purpose: catch regressions when thesis_analyzer is extracted as a service
(issue #90). Assert on observable behaviour (HTTP response shapes, summary
counts, field presence), NOT on internal function names.
"""
from __future__ import annotations

import copy
from unittest.mock import patch, MagicMock

import pytest

import webapp
import routers.analysis as _analysis_router
from services.thesis_analyzer import (
    extract_author_names as _extract_author_names,
    match_citations_to_references as _match_citations_to_references,
)

# ---------------------------------------------------------------------------
# Shared fake data
# ---------------------------------------------------------------------------

FAKE_PDF = b"%PDF-1.4 fake content for thesis analysis characterization"

# Used in citation-matching endpoint tests where we want the body text to be
# cleanly separated from the reference section.
# The ref_section sentinel appears verbatim in page 2, so the endpoint's body
# extractor can locate it (ref_start_pos > 0) and strip it from body_text.
_REF_SENTINEL = "REFSECTION_SENTINEL_START\n"

FAKE_PAGES_WITH_BODY = [
    "(Mueller, 2020) is the primary reference cited in the body.\n",
    _REF_SENTINEL + "Mueller, Hans (2020). Climate Change. Nature.\n"
    "Smith, John (2019). Global Warming. Science.\n"
    "Doe, Bob (2021). Unknown Topic. Some Journal.",
]

FAKE_REF_TEXT = (
    _REF_SENTINEL
    + "Mueller, Hans (2020). Climate Change. Nature.\n"
    "Smith, John (2019). Global Warming. Science.\n"
    "Doe, Bob (2021). Unknown Topic. Some Journal."
)

# Generic pages where body_text ≈ full_text (sentinel not found → no stripping).
FAKE_PAGES_GENERIC = [
    "Introduction to the topic.",
    "Further discussion of various topics.",
    "Conclusion and final remarks.",
]

FAKE_REFS = [
    {
        "title": "Climate Change",
        "authors": "Mueller, Hans",
        "year": 2020,
        "journal": "Nature",
        "doi": "",
    },
    {
        "title": "Global Warming",
        "authors": "Smith, John",
        "year": 2019,
        "journal": "Science",
        "doi": "10.1038/warming2019",
    },
    {
        "title": "Unknown Topic",
        "authors": "Doe, Bob",
        "year": 2021,
        "journal": "Some Journal",
        "doi": "",
    },
]

_NO_AVAIL = {"online_found": False, "doi_found": None, "source": None, "openalex_id": None}
_YES_AVAIL = {"online_found": True, "doi_found": "10.1038/warming2019", "source": "doi", "openalex_id": None}


# ---------------------------------------------------------------------------
# Patch helpers
# ---------------------------------------------------------------------------

def _patch_pages(pages=None):
    return patch.object(
        _analysis_router,
        "_extract_all_pdf_pages",
        return_value=pages if pages is not None else FAKE_PAGES_GENERIC,
    )


def _patch_ref_section(text=None):
    return patch.object(
        _analysis_router,
        "_find_reference_section",
        return_value=text if text is not None else FAKE_REF_TEXT,
    )


def _patch_llm_refs(refs=None):
    """Return a fresh deep-copy per call so in-place mutation doesn't leak between tests."""
    source = refs if refs is not None else FAKE_REFS
    return patch.object(
        _analysis_router,
        "_llm_extract_references",
        side_effect=lambda *_: copy.deepcopy(source),
    )


def _patch_availability(result=None):
    return patch.object(
        _analysis_router,
        "_check_online_availability",
        return_value=result if result is not None else _NO_AVAIL,
    )


def _patch_sleep():
    """Suppress the 200 ms inter-ref rate-limiting delay."""
    return patch("routers.analysis.time.sleep")


def _upload(client, filename="thesis.pdf", content=None):
    return client.post(
        "/api/analysis/thesis",
        files={"file": (filename, content or FAKE_PDF, "application/pdf")},
    )


# ===========================================================================
# Unit tests: _extract_author_names (pure function — no fixtures needed)
# ===========================================================================


class TestExtractAuthorNames:
    """Characterization tests for _extract_author_names (pure, no I/O)."""

    def test_empty_string_returns_empty(self):
        assert _extract_author_names("") == []

    def test_none_returns_empty(self):
        assert _extract_author_names(None) == []  # type: ignore[arg-type]

    def test_lastname_comma_firstname(self):
        names = _extract_author_names("Mueller, Hans")
        assert names == ["Mueller"]

    def test_multiple_authors_semicolon(self):
        names = _extract_author_names("Mueller, Hans; Schmidt, Anna")
        assert "Mueller" in names
        assert "Schmidt" in names
        assert len(names) == 2

    def test_abbreviated_initials_extract_lastname(self):
        names = _extract_author_names("Mueller, H.; Doe, J.")
        assert "Mueller" in names
        assert "Doe" in names

    def test_firstname_lastname_format_uses_last_word(self):
        """'Hans Mueller' → last word 'Mueller' is the last name."""
        names = _extract_author_names("Hans Mueller")
        assert "Mueller" in names

    def test_and_separator(self):
        names = _extract_author_names("Mueller and Schmidt")
        assert "Mueller" in names
        assert "Schmidt" in names

    def test_ampersand_separator(self):
        names = _extract_author_names("Mueller & Schmidt")
        assert "Mueller" in names
        assert "Schmidt" in names

    def test_german_und_separator(self):
        names = _extract_author_names("Mueller und Schmidt")
        assert "Mueller" in names
        assert "Schmidt" in names

    def test_single_letter_names_excluded(self):
        """Names of length < 2 after cleanup are excluded — quirk frozen."""
        # "X, Y" → lastname from first part = "X" (1 char) → excluded
        names = _extract_author_names("X, Y")
        assert names == []

    def test_whitespace_only_returns_empty(self):
        assert _extract_author_names("   ") == []

    def test_three_authors(self):
        names = _extract_author_names("Alpha, A.; Beta, B.; Gamma, G.")
        assert len(names) == 3
        assert "Alpha" in names
        assert "Beta" in names
        assert "Gamma" in names


# ===========================================================================
# Unit tests: _match_citations_to_references (pure function — no fixtures)
# ===========================================================================


def _ref(title="Paper", authors="Mueller, Hans", year=2020, doi="", index=1) -> dict:
    return {
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
        "_ref_index": index,
    }


class TestMatchCitationsToReferences:
    """Characterization tests for _match_citations_to_references (pure, no I/O)."""

    def test_empty_refs_returns_empty_lists(self):
        used, unused = _match_citations_to_references([], "some body text")
        assert used == []
        assert unused == []

    def test_harvard_round_parens_detected(self):
        """(Mueller, 2020) in body text → ref is used."""
        refs = [_ref(authors="Mueller, Hans", year=2020)]
        used, unused = _match_citations_to_references(refs, "(Mueller, 2020) showed …")
        assert len(used) == 1
        assert unused == []

    def test_narrative_style_detected(self):
        """Mueller (2020) (no comma) → ref is used."""
        refs = [_ref(authors="Mueller, Hans", year=2020)]
        used, unused = _match_citations_to_references(refs, "Mueller (2020) argued that …")
        assert len(used) == 1
        assert unused == []

    def test_uncited_ref_is_unused(self):
        refs = [_ref(authors="Doe, Bob", year=2021, title="Uncited Paper")]
        used, unused = _match_citations_to_references(refs, "No mention of anything relevant.")
        assert used == []
        assert len(unused) == 1

    def test_numbered_citation_triggers_via_multi_element_bracket(self):
        """[1, 2, 3] (multi-element bracket) → numbered matching engaged for matching indices.

        Quirk frozen: the numbered-citation regex requires >= 2 chars inside the brackets,
        so single-digit lone citations like [1], [2], [3] do NOT match individually.
        Only multi-element '[1, 2, 3]' or range '[1-3]' forms trigger the path."""
        refs = [
            _ref(authors="Alpha, A.", year=2020, index=1),
            _ref(authors="Beta, B.", year=2021, index=2),
            _ref(authors="Gamma, G.", year=2022, index=3),
        ]
        body = "See [1, 2, 3] for details."
        used, unused = _match_citations_to_references(refs, body)
        assert len(used) == 3
        assert unused == []

    def test_single_digit_lone_brackets_not_matched_by_regex(self):
        """Quirk frozen: bare [1] [2] [3] each have only 1 char inside and do NOT
        satisfy the numbered-citation regex (which needs >= 2 chars inside brackets),
        so numbered matching is never triggered for them — refs fall through to
        author/title heuristics instead."""
        refs = [
            _ref(authors="Alpha, A.", year=2020, index=1),
            _ref(authors="Beta, B.", year=2021, index=2),
            _ref(authors="Gamma, G.", year=2022, index=3),
        ]
        body = "See [1], [2] and [3] for details."
        used, unused = _match_citations_to_references(refs, body)
        # Single-digit brackets do not trigger numbered path.
        # These refs also have no author/year/title patterns in body → all unused.
        assert len(used) == 0
        assert len(unused) == 3

    def test_fewer_than_three_numbered_markers_no_numbered_path(self):
        """Quirk frozen: numbered path only activates when >= 3 distinct
        numbered markers appear. With only 1 marker, refs may still be found
        via author/title fallback, but the numbered assignment is not used."""
        refs = [_ref(authors="Alpha, A.", year=2020, index=1)]
        # Only [1] → fewer than 3 markers → numbered path does NOT assign.
        used, unused = _match_citations_to_references(refs, "Only [1] here.")
        # Function must not crash; result must be a partition of refs.
        assert isinstance(used, list)
        assert isinstance(unused, list)
        assert len(used) + len(unused) == 1

    def test_used_in_text_flag_set_true(self):
        """_match_citations_to_references sets '_used_in_text' = True on used refs."""
        refs = [_ref(authors="Mueller, Hans", year=2020)]
        used, _ = _match_citations_to_references(refs, "(Mueller, 2020) key study.")
        assert used[0]["_used_in_text"] is True

    def test_used_in_text_flag_set_false(self):
        """_match_citations_to_references sets '_used_in_text' = False on unused refs."""
        refs = [_ref(authors="Doe, Bob", year=2021, title="Uncited Work")]
        _, unused = _match_citations_to_references(refs, "No mention.")
        assert unused[0]["_used_in_text"] is False

    def test_mixed_used_and_unused(self):
        refs = [
            _ref(authors="Mueller, Hans", year=2020, index=1),
            _ref(authors="Smith, John", year=2019, index=2),
            _ref(authors="Doe, Bob", year=2021, title="Never Cited Paper X Y Z", index=3),
        ]
        body = "(Mueller, 2020) is cited. Smith (2019) also mentioned."
        used, unused = _match_citations_to_references(refs, body)
        assert len(used) == 2
        assert len(unused) == 1
        assert unused[0]["authors"] == "Doe, Bob"

    def test_range_numbered_citations_expand(self):
        """[1-3] expands to markers {1, 2, 3}."""
        refs = [
            _ref(authors="Alpha, A.", year=2020, index=1),
            _ref(authors="Beta, B.", year=2021, index=2),
            _ref(authors="Gamma, G.", year=2022, index=3),
        ]
        body = "These works [1-3] are crucial."
        used, unused = _match_citations_to_references(refs, body)
        assert len(used) == 3

    def test_empty_body_text_all_unused(self):
        refs = [
            _ref(authors="Alpha, A.", year=2020, index=1),
            _ref(authors="Beta, B.", year=2021, index=2),
        ]
        used, unused = _match_citations_to_references(refs, "")
        assert used == []
        assert len(unused) == 2


# ===========================================================================
# POST /api/analysis/thesis — error paths (HTTP-level)
# ===========================================================================


class TestAnalysisThesisErrorPaths:
    """Characterization tests for error paths in POST /api/analysis/thesis."""

    def test_400_non_pdf_filename(self, client, db):
        resp = client.post(
            "/api/analysis/thesis",
            files={"file": ("essay.docx", b"content", "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert "PDF" in resp.json()["detail"]

    def test_400_no_pages_extracted(self, client, db):
        """When _extract_all_pdf_pages returns [] → 400."""
        with _patch_pages([]):
            resp = _upload(client)
        assert resp.status_code == 400
        assert "gelesen" in resp.json()["detail"]

    def test_400_no_reference_section(self, client, db):
        """When _find_reference_section returns '' → 400."""
        with _patch_pages(), patch.object(_analysis_router, "_find_reference_section", return_value=""):
            resp = _upload(client)
        assert resp.status_code == 400
        assert "Literaturverzeichnis" in resp.json()["detail"]

    def test_400_reference_section_too_short(self, client, db):
        """Quirk frozen: ref sections with < 50 chars trigger the same 400 as empty."""
        with _patch_pages(), patch.object(_analysis_router, "_find_reference_section",
                                          return_value="Too short."):
            resp = _upload(client)
        assert resp.status_code == 400
        assert "Literaturverzeichnis" in resp.json()["detail"]

    def test_400_llm_returns_no_references(self, client, db):
        """When _llm_extract_references returns [] → 400."""
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs([]):
            resp = _upload(client)
        assert resp.status_code == 400
        assert "Referenzen" in resp.json()["detail"]


# ===========================================================================
# POST /api/analysis/thesis — response shape
# ===========================================================================


class TestAnalysisThesisResponseShape:
    """Characterization tests for the response shape of POST /api/analysis/thesis."""

    def test_200_ok_on_valid_upload(self, client, db):
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(), \
             _patch_availability(), _patch_sleep():
            resp = _upload(client)
        assert resp.status_code == 200

    def test_top_level_keys_present(self, client, db):
        """Freeze the top-level keys of the JSON response."""
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(), \
             _patch_availability(), _patch_sleep():
            resp = _upload(client)
        body = resp.json()
        expected = {
            "filename", "total_pages", "total_references", "references",
            "summary", "not_found_online", "not_used_in_text",
        }
        assert expected.issubset(body.keys()), f"Missing keys: {expected - body.keys()}"

    def test_filename_reflects_upload_name(self, client, db):
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(), \
             _patch_availability(), _patch_sleep():
            resp = _upload(client, filename="my_thesis.pdf")
        assert resp.json()["filename"] == "my_thesis.pdf"

    def test_total_pages_matches_page_count(self, client, db):
        pages = ["Page A.", "Page B.", "Page C."]
        with _patch_pages(pages), _patch_ref_section(), _patch_llm_refs([FAKE_REFS[0]]), \
             _patch_availability(), _patch_sleep():
            resp = _upload(client)
        assert resp.json()["total_pages"] == 3

    def test_total_references_matches_llm_output(self, client, db):
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(FAKE_REFS), \
             _patch_availability(), _patch_sleep():
            resp = _upload(client)
        assert resp.json()["total_references"] == len(FAKE_REFS)

    def test_references_list_length_equals_total_references(self, client, db):
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(FAKE_REFS), \
             _patch_availability(), _patch_sleep():
            resp = _upload(client)
        body = resp.json()
        assert len(body["references"]) == body["total_references"]

    def test_reference_item_keys(self, client, db):
        """Freeze the per-item keys in the 'references' list."""
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs([FAKE_REFS[0]]), \
             _patch_availability(), _patch_sleep():
            resp = _upload(client)
        ref_item = resp.json()["references"][0]
        expected = {
            "index", "title", "authors", "year", "journal", "doi",
            "used_in_text", "online_found", "online_source", "online_doi",
        }
        assert expected.issubset(ref_item.keys()), (
            f"Missing keys: {expected - ref_item.keys()}"
        )

    def test_reference_indices_start_at_1(self, client, db):
        """Quirk frozen: reference indices are 1-based."""
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(FAKE_REFS), \
             _patch_availability(), _patch_sleep():
            resp = _upload(client)
        indices = [r["index"] for r in resp.json()["references"]]
        assert indices[0] == 1
        assert indices == list(range(1, len(FAKE_REFS) + 1))

    def test_summary_keys_present(self, client, db):
        """Freeze the keys inside the 'summary' object."""
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(), \
             _patch_availability(), _patch_sleep():
            resp = _upload(client)
        summary = resp.json()["summary"]
        expected = {"total", "found_online", "not_found_online", "used_in_text", "not_used_in_text"}
        assert expected.issubset(summary.keys())

    def test_summary_total_equals_total_references(self, client, db):
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(FAKE_REFS), \
             _patch_availability(), _patch_sleep():
            resp = _upload(client)
        body = resp.json()
        assert body["summary"]["total"] == body["total_references"]

    def test_not_found_online_item_keys(self, client, db):
        """Freeze the per-item keys in 'not_found_online'."""
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs([FAKE_REFS[0]]), \
             _patch_availability(_NO_AVAIL), _patch_sleep():
            resp = _upload(client)
        not_found = resp.json()["not_found_online"]
        assert len(not_found) == 1
        expected_keys = {"index", "title", "authors", "year", "journal", "doi"}
        assert expected_keys.issubset(not_found[0].keys())

    def test_not_used_in_text_item_keys(self, client, db):
        """Freeze the per-item keys in 'not_used_in_text'."""
        # Use pages where body text has no citation patterns → all refs unused
        with _patch_pages(["No citations whatsoever.", "Conclusion."]), \
             _patch_ref_section(), _patch_llm_refs([FAKE_REFS[2]]), \
             _patch_availability(), _patch_sleep():
            resp = _upload(client)
        not_used = resp.json()["not_used_in_text"]
        assert len(not_used) >= 1
        expected_keys = {"index", "title", "authors", "year", "journal", "doi"}
        assert expected_keys.issubset(not_used[0].keys())


# ===========================================================================
# POST /api/analysis/thesis — online availability
# ===========================================================================


class TestAnalysisThesisOnlineAvailability:
    """Characterization tests for the online-availability behaviour."""

    def test_all_not_found_online_counted(self, client, db):
        """All refs return online_found=False → summary and list counts match."""
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(FAKE_REFS), \
             _patch_availability(_NO_AVAIL), _patch_sleep():
            resp = _upload(client)
        body = resp.json()
        assert body["summary"]["found_online"] == 0
        assert body["summary"]["not_found_online"] == len(FAKE_REFS)
        assert len(body["not_found_online"]) == len(FAKE_REFS)

    def test_found_online_reflected_in_summary(self, client, db):
        """When availability mock returns found for exactly 1 ref the summary matches."""
        call_count = {"n": 0}

        def avail_per_ref(ref):
            call_count["n"] += 1
            # Only the first ref (Mueller) is found online
            if ref.get("title") == "Climate Change":
                return _YES_AVAIL
            return _NO_AVAIL

        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(FAKE_REFS), \
             patch.object(_analysis_router, "_check_online_availability", side_effect=avail_per_ref), \
             _patch_sleep():
            resp = _upload(client)
        body = resp.json()
        assert body["summary"]["found_online"] == 1
        assert body["summary"]["not_found_online"] == len(FAKE_REFS) - 1

    def test_online_source_and_doi_set_on_reference_item(self, client, db):
        """When a ref is found online, online_source and online_doi appear in its item."""
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs([FAKE_REFS[1]]), \
             _patch_availability(_YES_AVAIL), _patch_sleep():
            resp = _upload(client)
        ref_item = resp.json()["references"][0]
        assert ref_item["online_found"] is True
        assert ref_item["online_source"] == "doi"
        assert ref_item["online_doi"] == "10.1038/warming2019"

    def test_availability_called_once_per_reference(self, client, db):
        """_check_online_availability is invoked exactly once per reference."""
        avail_mock = MagicMock(return_value=_NO_AVAIL)
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(FAKE_REFS), \
             patch.object(_analysis_router, "_check_online_availability", avail_mock), \
             _patch_sleep():
            _upload(client)
        assert avail_mock.call_count == len(FAKE_REFS)

    def test_exception_in_availability_counts_as_not_found(self, client, db):
        """Quirk frozen: if _check_online_availability raises, the ref is counted
        as not_found_online (exception is silently absorbed by the endpoint)."""
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs([FAKE_REFS[0]]), \
             patch.object(_analysis_router, "_check_online_availability",
                          side_effect=Exception("network timeout")), \
             _patch_sleep():
            resp = _upload(client)
        body = resp.json()
        assert resp.status_code == 200
        assert body["summary"]["not_found_online"] == 1
        assert body["summary"]["found_online"] == 0


# ===========================================================================
# POST /api/analysis/thesis — citation-to-reference matching
# ===========================================================================


class TestAnalysisThesisCitationMatching:
    """Characterization tests for citation↔reference matching via the endpoint."""

    def test_summary_used_plus_unused_equals_total(self, client, db):
        """Summary invariant: used_in_text + not_used_in_text == total."""
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(FAKE_REFS), \
             _patch_availability(), _patch_sleep():
            resp = _upload(client)
        s = resp.json()["summary"]
        assert s["used_in_text"] + s["not_used_in_text"] == s["total"]

    def test_harvard_citation_in_body_marks_ref_as_used(self, client, db):
        """A ref with '(Mueller, 2020)' in the body text is counted as used_in_text.

        Pages are crafted so the ref-section sentinel appears at pos > 0, allowing
        the endpoint to strip the ref list from body_text correctly.
        """
        with _patch_pages(FAKE_PAGES_WITH_BODY), \
             _patch_ref_section(FAKE_REF_TEXT), \
             _patch_llm_refs([FAKE_REFS[0]]), \
             _patch_availability(), _patch_sleep():
            resp = _upload(client)
        body = resp.json()
        assert body["summary"]["used_in_text"] == 1
        assert body["references"][0]["used_in_text"] is True

    def test_uncited_ref_appears_in_not_used_in_text_list(self, client, db):
        """A ref not cited in body text is in not_used_in_text list."""
        # Pages with no citation patterns for Doe (2021)
        pages = ["Introduction with no relevant citations.", "Conclusion."]
        with _patch_pages(pages), _patch_ref_section(), \
             _patch_llm_refs([FAKE_REFS[2]]), \
             _patch_availability(), _patch_sleep():
            resp = _upload(client)
        body = resp.json()
        assert body["summary"]["not_used_in_text"] >= 1
        titles_not_used = [r["title"] for r in body["not_used_in_text"]]
        assert "Unknown Topic" in titles_not_used

    def test_used_in_text_flag_on_reference_items(self, client, db):
        """Each item in 'references' has a boolean used_in_text field."""
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(FAKE_REFS), \
             _patch_availability(), _patch_sleep():
            resp = _upload(client)
        for ref_item in resp.json()["references"]:
            assert isinstance(ref_item["used_in_text"], bool)

    def test_body_contains_harvard_citation_sets_used(self, client, db):
        """When FAKE_PAGES_WITH_BODY is used (has '(Mueller, 2020)' in body)
        and Doe (2021) has no matching citation, the counts differ correctly."""
        with _patch_pages(FAKE_PAGES_WITH_BODY), \
             _patch_ref_section(FAKE_REF_TEXT), \
             _patch_llm_refs([FAKE_REFS[0], FAKE_REFS[2]]), \
             _patch_availability(), _patch_sleep():
            resp = _upload(client)
        refs_out = resp.json()["references"]
        mueller_item = next(r for r in refs_out if "Mueller" in r["authors"])
        doe_item = next(r for r in refs_out if "Doe" in r["authors"])
        assert mueller_item["used_in_text"] is True
        assert doe_item["used_in_text"] is False


# ===========================================================================
# POST /api/analysis/thesis — no DB side effects
# ===========================================================================


class TestAnalysisThesisNoDatabaseSideEffects:
    """The thesis endpoint analyses a PDF in-memory and must NOT write to the DB."""

    def test_no_paper_row_inserted(self, client, db):
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(FAKE_REFS), \
             _patch_availability(), _patch_sleep():
            resp = _upload(client)
        assert resp.status_code == 200
        conn = db._connect()
        count = conn.execute("SELECT COUNT(*) as cnt FROM papers").fetchone()["cnt"]
        conn.close()
        assert count == 0

    def test_no_paper_references_row_inserted(self, client, db):
        with _patch_pages(), _patch_ref_section(), _patch_llm_refs(FAKE_REFS), \
             _patch_availability(), _patch_sleep():
            resp = _upload(client)
        assert resp.status_code == 200
        conn = db._connect()
        count = conn.execute("SELECT COUNT(*) as cnt FROM paper_references").fetchone()["cnt"]
        conn.close()
        assert count == 0
