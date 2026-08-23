"""Tests for host_services._CoreLibraryApi.search_references (#104,
docs/PRD-semantische-suche.md Phase 4): plugin_api.LibraryApi.search_references
gets a semantic ranking backing, additive over its existing lexical
(db.search_papers) behaviour -- hosts/plugins without an embedding model
(e.g. today's plugins) must keep working unchanged (contract allows lexical
degradation, PRD Entscheidung 5).
"""
from __future__ import annotations

from unittest.mock import patch

import host_services
from embedding_index import pack_vector
from literature_manager import Config


def _seed_second_paper(db, authors="Doe, Jane", year=2023, title="Second Paper"):
    conn = db._connect()
    try:
        cur = conn.execute(
            """INSERT INTO papers (file_hash, filename, original_filename, title, authors, year, doi)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("hash-2", "p2.pdf", "p2.pdf", title, authors, year, "10.1000/p2"),
        )
        pid = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return pid


def _seed_embedding(db, paper_id, model, vector):
    conn = db._connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO paper_embeddings (paper_id, model, dim, vector) VALUES (?, ?, ?, ?)",
            (paper_id, model, len(vector), pack_vector(vector)),
        )
        conn.commit()
    finally:
        conn.close()


class TestSearchReferencesSemanticBacking:
    def test_semantic_ranking_used_when_model_and_vectors_available(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        second = _seed_second_paper(db)
        _seed_embedding(db, seed_paper, "test-embed-model", [1.0, 0.0])
        _seed_embedding(db, second, "test-embed-model", [0.0, 1.0])

        with patch("embedding_index.embed_texts", return_value=[[1.0, 0.0]]) as mock_embed:
            results = host_services._CoreLibraryApi().search_references("some query", limit=5)

        assert results
        assert results[0]["id"] == seed_paper
        mock_embed.assert_called_once()
        assert mock_embed.call_args.kwargs.get("mode") == "query"

    def test_no_embed_model_degrades_to_lexical(self, db, seed_paper, monkeypatch):
        """Regression (#104 AC 'Plugin-Verhalten unveraendert'): with no
        embedding model configured search_references behaves exactly like
        before this issue -- plain db.search_papers."""
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "")
        with patch("embedding_index.embed_texts") as mock_embed:
            results = host_services._CoreLibraryApi().search_references("Test Paper Title")
        assert any(r["id"] == seed_paper for r in results)
        mock_embed.assert_not_called()

    def test_no_indexed_vectors_degrades_to_lexical(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        with patch("embedding_index.embed_texts") as mock_embed:
            results = host_services._CoreLibraryApi().search_references("Test Paper Title")
        assert any(r["id"] == seed_paper for r in results)
        mock_embed.assert_not_called()

    def test_embed_http_failure_degrades_to_lexical(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        _seed_embedding(db, seed_paper, "test-embed-model", [1.0, 0.0])
        with patch("embedding_index.embed_texts", side_effect=RuntimeError("gateway down")):
            results = host_services._CoreLibraryApi().search_references("Test Paper Title")
        assert any(r["id"] == seed_paper for r in results)

    def test_model_mismatch_degrades_to_lexical(self, db, seed_paper, monkeypatch):
        """Vectors indexed with a different model than the currently
        configured one must never be compared (PRD Entscheidung 7)."""
        _seed_embedding(db, seed_paper, "qwen3", [1.0, 0.0])
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "some-other-model")
        with patch("embedding_index.embed_texts") as mock_embed:
            results = host_services._CoreLibraryApi().search_references("Test Paper Title")
        assert any(r["id"] == seed_paper for r in results)
        mock_embed.assert_not_called()

    def test_returns_wired_reference_shape(self, db, seed_paper, monkeypatch):
        """search_references keeps the LibraryApi wire shape (citekey, title,
        authors, year, ...) regardless of which ranking path produced it."""
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "")
        results = host_services._CoreLibraryApi().search_references("Test Paper Title")
        assert results
        hit = results[0]
        assert set(["citekey", "id", "title", "authors", "year", "journal", "doi", "abstract"]) <= set(hit.keys())
