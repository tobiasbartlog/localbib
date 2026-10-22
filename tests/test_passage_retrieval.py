"""Contract 2.5.0: passages with page numbers.

  * ``passage_retrieval.printed_pages`` -- the pure rule (label -> metadata
    offset when the range matches the PDF -> None; never invent a page);
  * ``PageLabelCache`` -- reading labels never fails the caller;
  * ``host_services._CoreLibraryApi.search_passages`` / ``get_passages`` over
    seeded ``paper_chunks`` (BM25 without embeddings, hybrid with them);
  * ``plugin_api.testing.InMemoryLibrary`` answers the same shape.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import host_services
import passage_retrieval
from embedding_index import pack_vector
from literature_manager import Config
from passage_retrieval import PageLabelCache, labels_from_rules, printed_pages
from plugin_api.testing import InMemoryLibrary
from tests.llm_helpers import configure_llm

PASSAGE_KEYS = {"citekey", "title", "year", "text", "page_start", "page_end",
                "printed_page_start", "printed_page_end", "score"}


# ---------------------------------------------------------------------------
# printed_pages: the rule
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pdf_page, kwargs, expected", [
    # 1. the PDF's own label wins
    (2, {"page_labels": ["iii", "iv"], "pages_field": "345-346", "pdf_page_count": 2}, "iv"),
    # 2. metadata offset when the range length equals the PDF page count
    (3, {"pages_field": "345-360", "pdf_page_count": 16}, "347"),
    (1, {"pages_field": "345-360", "pdf_page_count": 16}, "345"),
    # en-dash, BibTeX double hyphen, spaces
    (3, {"pages_field": "345–360", "pdf_page_count": 16}, "347"),
    (3, {"pages_field": "345 -- 360", "pdf_page_count": 16}, "347"),
    # mismatch -> None (a 20-page PDF of a 16-page article: cover/appendix)
    (3, {"pages_field": "345-360", "pdf_page_count": 20}, None),
    # missing pages
    (3, {"pages_field": None, "pdf_page_count": 16}, None),
    (3, {"pages_field": "", "pdf_page_count": 16}, None),
    # unknown page count, nonsense, reversed range
    (3, {"pages_field": "345-360", "pdf_page_count": None}, None),
    (3, {"pages_field": "e12345", "pdf_page_count": 1}, None),
    (1, {"pages_field": "360-345", "pdf_page_count": 16}, None),
    # empty labels fall through to the metadata rule
    (2, {"page_labels": ["", ""], "pages_field": "10-11", "pdf_page_count": 2}, "11"),
    # unknown PDF page / beyond the PDF
    (None, {"pages_field": "345-360", "pdf_page_count": 16}, None),
    (0, {"page_labels": ["1"]}, None),
    (17, {"pages_field": "345-360", "pdf_page_count": 16}, None),
])
def test_printed_pages_rule(pdf_page, kwargs, expected):
    assert printed_pages(pdf_page, **kwargs) == expected


class _FakeDoc:
    def __init__(self, labels):
        self._labels = labels
        self.page_count = len(labels)
        self.closed = False

    def __getitem__(self, i):
        label = self._labels[i]

        class _Page:
            def get_label(self):
                return label
        return _Page()

    def close(self):
        self.closed = True


def test_label_cache_reads_once_and_reports_the_count(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")
    calls = []

    def opener(path):
        calls.append(path)
        return _FakeDoc(["345", "346"])

    cache = PageLabelCache(opener=opener)
    assert cache.labels(str(pdf)) == (["345", "346"], 2)
    assert cache.labels(str(pdf)) == (["345", "346"], 2)
    assert len(calls) == 1


def test_label_cache_without_labels_keeps_the_count(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")
    cache = PageLabelCache(opener=lambda p: _FakeDoc(["", "", ""]))
    assert cache.labels(str(pdf)) == (None, 3)


def test_label_cache_never_fails_the_caller(tmp_path):
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"not a pdf")

    def opener(path):
        raise RuntimeError("cannot open")

    assert PageLabelCache(opener=opener).labels(str(pdf)) == (None, None)
    # the real PyMuPDF opener on a broken file as well
    assert PageLabelCache().labels(str(pdf)) == (None, None)
    assert PageLabelCache().labels(None) == (None, None)


def test_labels_from_rules_without_loading_pages():
    rules = [
        {"startpage": 2, "prefix": "", "style": "r", "firstpagenum": 1},
        {"startpage": 5, "prefix": "", "style": "D", "firstpagenum": 1},
        {"startpage": 7, "prefix": "A-", "style": "A", "firstpagenum": 27},
        {"startpage": 8, "prefix": "Cover", "style": "", "firstpagenum": 1},
    ]
    assert labels_from_rules(rules, 9) == [
        "", "",               # before the first rule: no label, never an invented one
        "i", "ii", "iii",
        "1", "2",
        "A-AA",               # PDF 32000: 27 -> "AA"
        "Cover",              # prefix only
    ]
    assert labels_from_rules([], 3) == ["", "", ""]


def test_label_cache_reads_real_pdf_labels(tmp_path):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for _ in range(3):
        doc.new_page()
    doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "D", "firstpagenum": 345}])
    path = tmp_path / "labelled.pdf"
    doc.save(str(path))
    doc.close()
    assert PageLabelCache().labels(str(path)) == (["345", "346", "347"], 3)


# ---------------------------------------------------------------------------
# host_services: search_passages / get_passages
# ---------------------------------------------------------------------------

def _seed_chunk(db, paper_id, text, chunk_index=0, page_start=1, page_end=None):
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO paper_chunks (paper_id, chunk_index, page_start, page_end, chunk_text) "
            "VALUES (?, ?, ?, ?, ?)",
            (paper_id, chunk_index, page_start, page_end or page_start, text),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _set_paper(db, paper_id, **values):
    conn = db._connect()
    try:
        for key, value in values.items():
            conn.execute(f"UPDATE papers SET {key} = ? WHERE id = ?", (value, paper_id))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def library():
    host_services._PAGE_LABELS._cache.clear()
    return host_services._CoreLibraryApi()


class TestSearchPassages:
    def test_bm25_without_embeddings_returns_full_text_and_pages(self, db, seed_paper, library, monkeypatch):
        configure_llm(monkeypatch, embedding="")
        long_text = "adhesive bonding between printed clay layers " * 20
        _seed_chunk(db, seed_paper, long_text, page_start=3, page_end=4)
        with patch("host_services.embed_texts") as mock_embed:
            hits = library.search_passages("adhesive bonding", limit=5)
        mock_embed.assert_not_called()
        assert len(hits) == 1
        hit = hits[0]
        assert set(hit) == PASSAGE_KEYS
        assert hit["citekey"] == "Doe2024"
        assert hit["text"] == long_text           # the whole chunk, no excerpt
        assert (hit["page_start"], hit["page_end"]) == (3, 4)
        # the seeded PDF is fake and the Item has no ``pages`` -> no printed page
        assert hit["printed_page_start"] is None and hit["printed_page_end"] is None

    def test_metadata_pages_give_the_printed_page(self, db, seed_paper, library, monkeypatch):
        configure_llm(monkeypatch, embedding="")
        _set_paper(db, seed_paper, raw_metadata=json.dumps({"pages": "345–360"}), page_count=16)
        _seed_chunk(db, seed_paper, "unique passage about rheology", page_start=3)
        (hit,) = library.search_passages("rheology")
        assert hit["page_start"] == 3
        assert hit["printed_page_start"] == "347"

    def test_mismatched_page_count_gives_no_printed_page(self, db, seed_paper, library, monkeypatch):
        configure_llm(monkeypatch, embedding="")
        _set_paper(db, seed_paper, raw_metadata=json.dumps({"pages": "345-360"}), page_count=20)
        _seed_chunk(db, seed_paper, "unique passage about rheology", page_start=3)
        (hit,) = library.search_passages("rheology")
        assert hit["printed_page_start"] is None

    def test_same_ranking_as_the_http_endpoint(self, client, db, seed_paper, library, monkeypatch):
        configure_llm(monkeypatch, embedding="")
        _seed_chunk(db, seed_paper, "clay printing layer adhesion", chunk_index=0, page_start=1)
        _seed_chunk(db, seed_paper, "clay clay clay printing", chunk_index=1, page_start=2)
        http = client.get("/api/search/passages", params={"q": "clay printing"}).json()
        contract = library.search_passages("clay printing")
        assert [r["page_start"] for r in http["results"]] == [r["page_start"] for r in contract]
        assert [r["score"] for r in http["results"]] == [r["score"] for r in contract]

    def test_hybrid_with_embeddings(self, db, seed_paper, library, monkeypatch):
        configure_llm(monkeypatch, embedding="test-embed-model")
        conn = db._connect()
        try:
            conn.execute(
                "INSERT INTO paper_embeddings (paper_id, model, dim, vector) VALUES (?, ?, ?, ?)",
                (seed_paper, "test-embed-model", 2, pack_vector([1.0, 0.0])))
            conn.commit()
        finally:
            conn.close()
        chunk_id = _seed_chunk(db, seed_paper, "adhesive bonding clay layers", page_start=2)
        conn = db._connect()
        try:
            conn.execute(
                "INSERT INTO chunk_embeddings (chunk_id, model, dim, vector) VALUES (?, ?, ?, ?)",
                (chunk_id, "test-embed-model", 2, pack_vector([1.0, 0.0])))
            conn.commit()
        finally:
            conn.close()
        with patch("host_services.embed_texts", return_value=[[1.0, 0.0]]) as mock_embed:
            hits = library.search_passages("adhesive bonding", limit=3)
        mock_embed.assert_called_once()
        assert hits and hits[0]["page_start"] == 2

    def test_empty_query(self, db, seed_paper, library):
        assert library.search_passages("   ") == []


class TestGetPassages:
    def test_reading_order_and_paging(self, db, seed_paper, library):
        for i in range(5):
            _seed_chunk(db, seed_paper, f"chunk {i}", chunk_index=i, page_start=i + 1)
        out = library.get_passages("Doe2024", offset=2, limit=2)
        assert out["citekey"] == "Doe2024"
        assert out["total"] == 5
        assert [p["text"] for p in out["passages"]] == ["chunk 2", "chunk 3"]
        assert [p["index"] for p in out["passages"]] == [2, 3]
        assert out["passages"][0]["page_start"] == 3
        assert set(out["passages"][0]) == {"index", "text", "page_start", "page_end",
                                           "printed_page_start", "printed_page_end"}

    def test_no_text_is_total_zero(self, db, seed_paper, library):
        assert library.get_passages("Doe2024") == {"citekey": "Doe2024", "total": 0, "passages": []}

    def test_unknown_key_is_none(self, db, library):
        assert library.get_passages("NoSuchKey9999") is None

    def test_real_pdf_labels_win(self, db, seed_paper, library):
        fitz = pytest.importorskip("fitz")
        doc = fitz.open()
        for _ in range(2):
            doc.new_page()
        doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1}])
        doc.save(str(Path(Config.ALL_DIR) / "test_paper.pdf"))
        doc.close()
        _seed_chunk(db, seed_paper, "front matter", page_start=2)
        (passage,) = library.get_passages("Doe2024")["passages"]
        assert passage["printed_page_start"] == "ii"


def test_addon_library_forwards_the_new_reads():
    """plugin_loader.AddonLibrary passes every read through untouched."""
    import plugin_loader
    from plugin_api import PluginManifest

    class Host:
        def search_passages(self, q, limit=10):
            return [q, limit]

        def get_passages(self, key, offset=0, limit=20):
            return {"citekey": key}

    manifest = PluginManifest(id="x", name="X", version="0.1.0", api_version=2)
    lib = plugin_loader.AddonLibrary(Host(), "x", manifest)
    assert lib.search_passages("q", 3) == ["q", 3]
    assert lib.get_passages("k") == {"citekey": "k"}


# ---------------------------------------------------------------------------
# plugin_api.testing fake
# ---------------------------------------------------------------------------

def test_in_memory_library_passages():
    lib = InMemoryLibrary([
        {"citekey": "a", "title": "A", "year": 2020, "passages": [
            {"text": "clay layers bond", "page_start": 3, "printed_page_start": "347"},
            {"text": "nothing here", "page_start": 4},
        ]},
        {"citekey": "b", "title": "B"},
    ])
    (hit,) = lib.search_passages("clay")
    assert set(hit) == PASSAGE_KEYS
    assert hit["printed_page_start"] == "347" and hit["page_end"] == 3
    out = lib.get_passages("a", offset=1)
    assert out["total"] == 2 and out["passages"][0]["index"] == 1
    assert out["passages"][0]["printed_page_start"] is None
    assert lib.get_passages("b") == {"citekey": "b", "total": 0, "passages": []}
    assert lib.get_passages("zzz") is None
    assert lib.search_passages("") == []


def test_module_is_neutral():
    """passage_retrieval imports no router and no webapp."""
    source = Path(passage_retrieval.__file__).read_text(encoding="utf-8")
    assert "routers" not in source.split('"""', 2)[2].split("def ")[0]
    assert "import webapp" not in source
