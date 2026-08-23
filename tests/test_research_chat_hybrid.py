"""Research-Chat hybrid retrieval tests (#103, ``docs/PRD-semantische-suche.md``
Phase 3, Entscheidung 5/7).

Covers:
1. With a configured embedding model + chunk vectors, ``/api/research-chat/ask``
   retrieves via cosine+BM25 (RRF) and cites a chunk that BM25 alone could
   never find -- proof the hybrid path is actually driving retrieval.
2. Without ``LLM_EMBED_MODEL`` configured, retrieval is untouched: no
   ``embed_texts`` call is made at all, and the chosen chunk/answer is
   identical to the pre-#103 BM25-only behavior (regression test).
3. ``embed_texts`` failure degrades cleanly to BM25 (no 500, no missing
   answer) -- same degradation ladder as ``routers/search.py``.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import webapp
from embedding_index import pack_vector
from literature_manager import Config


def _seed_chunk(db, paper_id: int, text: str, chunk_index: int = 0, page_start: int = 1) -> int:
    conn = db._connect()
    try:
        cur = conn.execute(
            """INSERT INTO paper_chunks
               (paper_id, chunk_index, page_start, page_end, chunk_text, token_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (paper_id, chunk_index, page_start, page_start, text, len(text.split())),
        )
        chunk_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return chunk_id


def _seed_chunk_embedding(db, chunk_id: int, model: str, vector: list) -> None:
    conn = db._connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO chunk_embeddings (chunk_id, model, dim, vector) VALUES (?, ?, ?, ?)",
            (chunk_id, model, len(vector), pack_vector(vector)),
        )
        conn.commit()
    finally:
        conn.close()


class TestHybridRetrievalUsed:
    def test_answer_cites_chunk_found_only_via_embedding(self, client, seed_paper, db, monkeypatch):
        """A chunk with zero lexical overlap with the question, but a perfect
        cosine match to the (mocked) query embedding, must be surfaced -- BM25
        alone would never find it. A second chunk matches the question
        lexically but has no embedding, pulling the response into hybrid mode."""
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")

        semantic_chunk_id = _seed_chunk(
            db, seed_paper, "Der Ofen brennt bei sehr hohen Temperaturen stundenlang.",
            chunk_index=0, page_start=3,
        )
        _seed_chunk_embedding(db, semantic_chunk_id, "test-embed-model", [1.0, 0.0])

        _seed_chunk(
            db, seed_paper, "machine learning methods for materials science",
            chunk_index=1, page_start=1,
        )

        captured: list[dict] = []

        def fake_complete(messages, **kw):
            captured.extend(messages)
            return "Antwort basierend auf Quelle 1"

        with patch("routers.research_chat.embed_texts", return_value=[[1.0, 0.0]]) as mock_embed, \
             patch.object(webapp.LLMClient, "complete", side_effect=fake_complete):
            resp = client.post(
                "/api/research-chat/ask",
                json={
                    "question": "machine learning methods for materials science",
                    "paper_ids": [seed_paper],
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "hybrid"
        mock_embed.assert_called_once()
        assert mock_embed.call_args.kwargs.get("mode") == "query"

        # The embedding-only chunk (page 3) must appear in the assembled
        # context sent to the LLM -- proof the cosine signal contributed.
        user_msg = next((m["content"] for m in captured if m.get("role") == "user"), "")
        assert "Der Ofen brennt bei sehr hohen Temperaturen" in user_msg

        pages_referenced = {p for s in data["sources"] for p in s["pages_referenced"]}
        assert 3 in pages_referenced


class TestRegressionWithoutEmbedModel:
    def test_no_embed_model_behaves_exactly_like_bm25_only(self, client, seed_paper, db, monkeypatch):
        """Without LLM_EMBED_MODEL configured, no embedding call is attempted
        and the answer/sources are identical to the pre-#103 BM25-only path."""
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "")

        _seed_chunk(db, seed_paper, "machine learning methods for materials science", page_start=1)

        captured: list[dict] = []

        def fake_complete(messages, **kw):
            captured.extend(messages)
            return "Antwort ohne Embedding-Modell"

        with patch("routers.research_chat.embed_texts") as mock_embed, \
             patch.object(webapp.LLMClient, "complete", side_effect=fake_complete):
            resp = client.post(
                "/api/research-chat/ask",
                json={
                    "question": "What machine learning methods are used?",
                    "paper_ids": [seed_paper],
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"] == "Antwort ohne Embedding-Modell"
        assert data["mode"] == "bm25"
        mock_embed.assert_not_called()

    def test_no_chunk_vectors_for_selected_chunks_falls_back_to_bm25(self, client, seed_paper, db, monkeypatch):
        """Embedding model IS configured, but none of the chunks scoped to
        this chat have a stored vector -- must degrade to BM25 without
        calling embed_texts (mirrors routers/search.py's model-mismatch
        fallback)."""
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        _seed_chunk(db, seed_paper, "machine learning methods for materials science", page_start=1)

        with patch("routers.research_chat.embed_texts") as mock_embed, \
             patch.object(webapp.LLMClient, "complete", return_value="Antwort"):
            resp = client.post(
                "/api/research-chat/ask",
                json={
                    "question": "What machine learning methods are used?",
                    "paper_ids": [seed_paper],
                },
            )

        assert resp.status_code == 200
        assert resp.json()["mode"] == "bm25"
        mock_embed.assert_not_called()


class TestEmbedFailureDegrades:
    def test_embed_http_failure_degrades_to_bm25_answer_still_returned(self, client, seed_paper, db, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        chunk_id = _seed_chunk(db, seed_paper, "machine learning methods for materials science", page_start=1)
        _seed_chunk_embedding(db, chunk_id, "test-embed-model", [1.0, 0.0])

        with patch("routers.research_chat.embed_texts", side_effect=RuntimeError("gateway down")), \
             patch.object(webapp.LLMClient, "complete", return_value="Antwort trotz Embedding-Fehler"):
            resp = client.post(
                "/api/research-chat/ask",
                json={
                    "question": "What machine learning methods are used?",
                    "paper_ids": [seed_paper],
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "bm25"
        assert data["answer"] == "Antwort trotz Embedding-Fehler"
