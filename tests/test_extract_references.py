"""Characterization snapshot for POST /api/papers/{id}/extract-references.

The endpoint streams Server-Sent Events; we consume the stream and
verify the final `complete` event carries the expected fields. Per-module
unit tests will cover specific extractor logic.

Mock targets repointed to services.reference_extraction (Backend-Modularisierung #88):
the endpoint now lives in routers/references.py which calls the service module object.
"""
import json

import services.reference_extraction as _ref_svc
import webapp


def _parse_sse_events(stream_response) -> list[dict]:
    events: list[dict] = []
    for line in stream_response.iter_lines():
        if not line:
            continue
        # httpx may yield bytes or str depending on transport
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return events


def test_extract_references_returns_complete_event(client, seed_paper, monkeypatch):
    paper_id = seed_paper

    monkeypatch.setattr(
        _ref_svc,
        "extract_all_pdf_pages",
        lambda *a, **kw: [
            "Body of the paper, page one.",
            "References\n[1] Doe, J. (2020). Foo. J. Bar.\n[2] Smith, A. (2021). Baz.\n",
        ],
    )
    monkeypatch.setattr(
        _ref_svc,
        "find_reference_section",
        lambda pages: (
            "[1] Doe, J. (2020). Foo. J. Bar.\n[2] Smith, A. (2021). Baz.\n"
            + "padding to clear the 50-char minimum threshold " * 2
        ),
    )
    monkeypatch.setattr(
        _ref_svc,
        "extract_references_from_chunk",
        lambda chunk: [
            {"title": "Foo", "authors": "Doe, J.", "year": 2020, "journal": "J. Bar", "doi": ""},
            {"title": "Baz", "authors": "Smith, A.", "year": 2021, "journal": "", "doi": "10.1000/baz"},
        ],
    )
    monkeypatch.setattr(_ref_svc, "crossref_doi_lookup", lambda title, authors="": "")
    # Skip the per-reference 0.1s sleep so the test stays fast
    monkeypatch.setattr(webapp.time, "sleep", lambda *a, **kw: None)

    with client.stream(
        "POST", f"/api/papers/{paper_id}/extract-references"
    ) as resp:
        assert resp.status_code == 200
        events = _parse_sse_events(resp)

    assert events, "expected at least one SSE event"
    final = events[-1]
    assert final["type"] == "complete"
    assert final["status"] == "ok"
    assert final["total_extracted"] == 2
    assert final["with_doi"] == 1
    assert isinstance(final["references"], list)
    assert len(final["references"]) == 2
