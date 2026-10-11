"""One DOI rule for the server and the SPA (#227).

The import page's DOI list normalizes what the user pastes the way the
intake's identity ``paper_ingest.normalize_doi`` does — otherwise a DOI in
two spellings shows as two "new" rows, or one the server would find shows as
missing. Both sides read tests/fixtures/doi_vectors.json; this is the
server's half, tests/js/import_doi_vectors.test.js the SPA's.
"""
from __future__ import annotations

import json
from pathlib import Path

import paper_ingest

VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "doi_vectors.json").read_text(encoding="utf-8"))["vectors"]


def test_the_vector_file_is_not_empty():
    assert len(VECTORS) >= 10


def test_normalize_doi_matches_every_shared_vector():
    wrong = [(v["input"], paper_ingest.normalize_doi(v["input"]), v["normalized"]) for v in VECTORS
             if paper_ingest.normalize_doi(v["input"]) != v["normalized"]]
    assert not wrong
