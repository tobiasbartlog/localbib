"""Tests for the semantic search endpoint (#99).

Covers:
  * services/semantic_search.py -- pure cosine + top-k ranking, fixed vector
    fixtures, no DB/HTTP.
  * routers/search.py -- GET/POST /api/search/semantic with a mocked
    llm_client.embed_texts (no real HTTP calls).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import services.semantic_search as semantic_search
from embedding_index import pack_vector
from literature_manager import Config


# ---------------------------------------------------------------------------
# Pure ranking (services/semantic_search.py)
# ---------------------------------------------------------------------------

class TestCosine:
    def test_identical_vectors_score_one(self):
        assert semantic_search.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_score_zero(self):
        assert semantic_search.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors_score_minus_one(self):
        assert semantic_search.cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_mismatched_length_is_zero(self):
        assert semantic_search.cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0

    def test_empty_vector_is_zero(self):
        assert semantic_search.cosine([], []) == 0.0

    def test_zero_norm_vector_is_zero(self):
        assert semantic_search.cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


class TestRankByCosine:
    def test_orders_by_similarity_descending(self):
        query = [1.0, 0.0]
        candidates = [
            {"paper_id": 1, "vector": [0.0, 1.0]},   # orthogonal -> 0.0
            {"paper_id": 2, "vector": [1.0, 0.0]},   # identical -> 1.0
            {"paper_id": 3, "vector": [0.7, 0.7]},   # -> ~0.707
        ]
        ranked = semantic_search.rank_by_cosine(query, candidates, top_k=10)
        assert [r["paper_id"] for r in ranked] == [2, 3, 1]
        assert ranked[0]["score"] == pytest.approx(1.0)

    def test_top_k_truncates(self):
        query = [1.0, 0.0]
        candidates = [{"paper_id": i, "vector": [1.0, 0.0]} for i in range(5)]
        ranked = semantic_search.rank_by_cosine(query, candidates, top_k=2)
        assert len(ranked) == 2

    def test_vector_key_removed_other_fields_pass_through(self):
        query = [1.0, 0.0]
        candidates = [{"paper_id": 1, "citekey": "smith2024", "vector": [1.0, 0.0]}]
        ranked = semantic_search.rank_by_cosine(query, candidates, top_k=10)
        assert "vector" not in ranked[0]
        assert ranked[0]["citekey"] == "smith2024"
        assert ranked[0]["paper_id"] == 1

    def test_empty_candidates_returns_empty(self):
        assert semantic_search.rank_by_cosine([1.0, 0.0], [], top_k=10) == []


# ---------------------------------------------------------------------------
# Router: GET/POST /api/search/semantic
# ---------------------------------------------------------------------------

def _seed_second_paper(db, cite_key="doe2023", title="Second Paper", abstract="Another abstract"):
    conn = db._connect()
    try:
        cur = conn.execute(
            """INSERT INTO papers (file_hash, filename, original_filename, title,
               authors, year, doi, abstract, cite_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("hash-2", "p2.pdf", "p2.pdf", title, "Doe, Jane", 2023, "10.1000/p2", abstract, cite_key),
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


def _seed_chunk(db, paper_id, chunk_text, chunk_index=0, page_start=1):
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO paper_chunks (paper_id, chunk_index, page_start, page_end, chunk_text) "
            "VALUES (?, ?, ?, ?, ?)",
            (paper_id, chunk_index, page_start, page_start, chunk_text),
        )
        chunk_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return chunk_id


def _seed_chunk_embedding(db, chunk_id, model, vector):
    conn = db._connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO chunk_embeddings (chunk_id, model, dim, vector) VALUES (?, ?, ?, ?)",
            (chunk_id, model, len(vector), pack_vector(vector)),
        )
        conn.commit()
    finally:
        conn.close()


class TestSemanticSearchEndpoint:
    def test_semantic_mode_with_mocked_embed(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        second = _seed_second_paper(db)
        _seed_embedding(db, seed_paper, "test-embed-model", [1.0, 0.0])
        _seed_embedding(db, second, "test-embed-model", [0.0, 1.0])

        with patch("routers.search.embed_texts", return_value=[[1.0, 0.0]]) as mock_embed:
            resp = client.get("/api/search/semantic", params={"q": "Lehm Schichthaftung", "top_k": 5})

        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "semantic"
        assert body["results"]
        assert body["results"][0]["paper_id"] == seed_paper
        assert body["results"][0]["score"] == pytest.approx(1.0)
        assert "citekey" in body["results"][0]
        assert "title" in body["results"][0]
        assert "year" in body["results"][0]
        assert "abstract_excerpt" in body["results"][0]
        mock_embed.assert_called_once()
        assert mock_embed.call_args.kwargs.get("mode") == "query"

    def test_post_variant_matches_get(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        _seed_embedding(db, seed_paper, "test-embed-model", [1.0, 0.0])
        with patch("routers.search.embed_texts", return_value=[[1.0, 0.0]]):
            resp = client.post("/api/search/semantic", json={"q": "test query", "top_k": 5})
        assert resp.status_code == 200
        assert resp.json()["mode"] == "semantic"

    def test_no_embed_model_configured_falls_back_to_bm25(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "")
        with patch("routers.search.embed_texts") as mock_embed:
            resp = client.get("/api/search/semantic", params={"q": "Test Paper Title"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "bm25"
        mock_embed.assert_not_called()

    def test_bm25_fallback_finds_lexical_match(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "")
        resp = client.get("/api/search/semantic", params={"q": "Test Paper Title"})
        body = resp.json()
        assert body["mode"] == "bm25"
        assert any(r["paper_id"] == seed_paper for r in body["results"])

    def test_model_mismatch_falls_back_without_calling_embed(self, client, db, seed_paper, monkeypatch):
        """Index was built with model 'qwen3', config now points elsewhere --
        must never silently compare across embedding spaces (PRD Entscheidung 7)."""
        _seed_embedding(db, seed_paper, "qwen3", [1.0, 0.0])
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "some-other-model")

        with patch("routers.search.embed_texts") as mock_embed:
            resp = client.get("/api/search/semantic", params={"q": "Test Paper Title"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "bm25"
        assert body.get("note")
        mock_embed.assert_not_called()

    def test_embed_http_failure_degrades_to_bm25(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        _seed_embedding(db, seed_paper, "test-embed-model", [1.0, 0.0])
        with patch("routers.search.embed_texts", side_effect=RuntimeError("gateway down")):
            resp = client.get("/api/search/semantic", params={"q": "Test Paper Title"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "bm25"
        assert body.get("note")

    def test_empty_query_returns_empty_results_no_error(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        resp = client.get("/api/search/semantic", params={"q": ""})
        assert resp.status_code == 200
        assert resp.json() == {"mode": "bm25", "results": []}

    def test_top_k_limits_results(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        second = _seed_second_paper(db)
        _seed_embedding(db, seed_paper, "test-embed-model", [1.0, 0.0])
        _seed_embedding(db, second, "test-embed-model", [0.9, 0.1])
        with patch("routers.search.embed_texts", return_value=[[1.0, 0.0]]):
            resp = client.get("/api/search/semantic", params={"q": "test", "top_k": 1})
        body = resp.json()
        assert body["mode"] == "semantic"
        assert len(body["results"]) == 1


# ---------------------------------------------------------------------------
# Hybrid fusion (#100): BM25 over real paper_chunks + cosine, via RRF
# ---------------------------------------------------------------------------

class TestHybridSearch:
    def test_hybrid_mode_when_bm25_and_cosine_both_contribute(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        second = _seed_second_paper(db)
        _seed_embedding(db, seed_paper, "test-embed-model", [1.0, 0.0])
        _seed_embedding(db, second, "test-embed-model", [0.0, 1.0])
        _seed_chunk(db, seed_paper, "adhesive bonding strength between printed clay layers")

        with patch("routers.search.embed_texts", return_value=[[1.0, 0.0]]):
            resp = client.get(
                "/api/search/semantic",
                params={"q": "adhesive bonding clay layers", "top_k": 5},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "hybrid"
        result_ids = [r["paper_id"] for r in body["results"]]
        assert seed_paper in result_ids

    def test_papers_without_chunks_still_appear_in_hybrid_results(self, client, db, seed_paper, monkeypatch):
        """Acceptance #100: a paper with no paper_chunks stays visible via its
        cosine contribution alone, even once BM25 contributes for another paper."""
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        second = _seed_second_paper(db)
        # seed_paper has real chunks matching the query lexically -> BM25 contributes.
        _seed_embedding(db, seed_paper, "test-embed-model", [0.0, 1.0])
        _seed_chunk(db, seed_paper, "adhesive bonding strength between printed clay layers")
        # `second` has an embedding but NO chunks at all, and the mocked query
        # vector matches it exactly -> only cosine can ever surface it.
        _seed_embedding(db, second, "test-embed-model", [1.0, 0.0])

        with patch("routers.search.embed_texts", return_value=[[1.0, 0.0]]):
            resp = client.get(
                "/api/search/semantic",
                params={"q": "adhesive bonding clay layers", "top_k": 5},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "hybrid"
        result_ids = [r["paper_id"] for r in body["results"]]
        assert second in result_ids

    def test_german_query_finds_english_paper_bm25_misses(self, client, db, seed_paper, monkeypatch):
        """Acceptance #100: a German query finds an English paper that a
        lexical-only (BM25) search never would -- the target paper has no
        chunks and no lexical overlap with the query at all; it is findable
        purely through the (mocked) embedding, while a lexically-matching
        distractor paper (no embedding, only chunks) pulls the response into
        `hybrid` mode without out-ranking the semantically relevant target."""
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        distractor = _seed_second_paper(db, cite_key="distractor2023", title="Distractor Paper")
        # Target: embedding only, no chunks, title has zero lexical overlap
        # with the German query -- unreachable by any BM25 path.
        _seed_embedding(db, seed_paper, "test-embed-model", [1.0, 0.0])
        # Distractor: real chunk containing the literal German query terms,
        # but deliberately NOT embedded (no paper_embeddings row) so it can
        # only ever be reached via BM25.
        _seed_chunk(db, distractor, "Diese Studie behandelt Lehm Schichthaftung im 3D-Druck")

        with patch("routers.search.embed_texts", return_value=[[1.0, 0.0]]):
            resp = client.get(
                "/api/search/semantic",
                params={"q": "Lehm Schichthaftung", "top_k": 5},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "hybrid"
        assert body["results"], "expected the semantically relevant paper to be found"
        assert body["results"][0]["paper_id"] == seed_paper


# ---------------------------------------------------------------------------
# two_stage_paper_then_chunk_ranking (#102, PRD Phase 3, pure ranking math)
# ---------------------------------------------------------------------------

class TestTwoStagePaperThenChunkRanking:
    def test_restricts_chunks_to_top_ranked_papers(self):
        query = [1.0, 0.0]
        paper_candidates = [
            {"paper_id": 1, "vector": [1.0, 0.0]},   # best match -> top-1
            {"paper_id": 2, "vector": [-1.0, 0.0]},  # opposite -> excluded when top_n_papers=1
        ]
        chunk_candidates = [
            {"chunk_id": 10, "paper_id": 1, "vector": [0.9, 0.1]},
            # Chunk 20's OWN vector is a perfect match, but its paper (2) does
            # not make the top-1 paper cut -- it must never surface.
            {"chunk_id": 20, "paper_id": 2, "vector": [1.0, 0.0]},
        ]
        ranked = semantic_search.two_stage_paper_then_chunk_ranking(
            query, paper_candidates, chunk_candidates, top_n_papers=1, top_k_chunks=10,
        )
        assert [c["chunk_id"] for c in ranked] == [10]

    def test_top_k_chunks_truncates(self):
        query = [1.0, 0.0]
        paper_candidates = [{"paper_id": 1, "vector": [1.0, 0.0]}]
        chunk_candidates = [
            {"chunk_id": i, "paper_id": 1, "vector": [1.0, 0.0]} for i in range(5)
        ]
        ranked = semantic_search.two_stage_paper_then_chunk_ranking(
            query, paper_candidates, chunk_candidates, top_n_papers=10, top_k_chunks=2,
        )
        assert len(ranked) == 2

    def test_empty_paper_candidates_returns_empty(self):
        ranked = semantic_search.two_stage_paper_then_chunk_ranking(
            [1.0, 0.0], [], [{"chunk_id": 1, "paper_id": 1, "vector": [1.0, 0.0]}],
        )
        assert ranked == []

    def test_empty_chunk_candidates_returns_empty(self):
        ranked = semantic_search.two_stage_paper_then_chunk_ranking(
            [1.0, 0.0], [{"paper_id": 1, "vector": [1.0, 0.0]}], [],
        )
        assert ranked == []

    def test_vector_key_removed_score_added(self):
        query = [1.0, 0.0]
        paper_candidates = [{"paper_id": 1, "vector": [1.0, 0.0]}]
        chunk_candidates = [{"chunk_id": 1, "paper_id": 1, "page_start": 3, "vector": [1.0, 0.0]}]
        ranked = semantic_search.two_stage_paper_then_chunk_ranking(
            query, paper_candidates, chunk_candidates,
        )
        assert "vector" not in ranked[0]
        assert ranked[0]["page_start"] == 3
        assert ranked[0]["score"] == pytest.approx(1.0)


class TestTwoStageRankingPerformance:
    def test_completes_well_under_one_second_at_library_scale(self):
        """PRD acceptance criterion (docs/PRD-semantische-suche.md §5, #102 AC):
        'Suche ueber die ganze Bibliothek < 1s nach Matrix-Warm-up'. Exercises
        the pure two-stage ranking directly with synthetic vectors at roughly
        real library scale (offene Frage 2: ~13.6k chunks) -- no benchmark
        harness needed, just a generous synthetic-scale timing assertion."""
        import random
        import time

        rng = random.Random(42)
        dim = 128
        n_papers = 500
        n_chunks = 15000

        def rand_vec():
            return [rng.uniform(-1.0, 1.0) for _ in range(dim)]

        paper_candidates = [{"paper_id": i, "vector": rand_vec()} for i in range(n_papers)]
        chunk_candidates = [
            {"chunk_id": i, "paper_id": i % n_papers, "vector": rand_vec()}
            for i in range(n_chunks)
        ]
        query_vector = rand_vec()

        start = time.perf_counter()
        results = semantic_search.two_stage_paper_then_chunk_ranking(
            query_vector, paper_candidates, chunk_candidates,
            top_n_papers=20, top_k_chunks=10,
        )
        elapsed = time.perf_counter() - start

        assert len(results) <= 10
        assert elapsed < 1.0


# ---------------------------------------------------------------------------
# Router: GET/POST /api/search/passages (#102, PRD Phase 3)
# ---------------------------------------------------------------------------

class TestPassageSearchEndpoint:
    def test_semantic_mode_returns_page_start(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        _seed_embedding(db, seed_paper, "test-embed-model", [1.0, 0.0])
        chunk_id = _seed_chunk(db, seed_paper, "unrelated filler text about nothing", page_start=7)
        _seed_chunk_embedding(db, chunk_id, "test-embed-model", [1.0, 0.0])

        with patch("routers.search.embed_texts", return_value=[[1.0, 0.0]]) as mock_embed:
            resp = client.get("/api/search/passages", params={"q": "some semantic query", "top_k": 5})

        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "semantic"
        assert body["results"]
        hit = body["results"][0]
        assert hit["paper_id"] == seed_paper
        assert hit["page_start"] == 7
        assert "chunk_excerpt" in hit
        assert "citekey" in hit
        mock_embed.assert_called_once()
        assert mock_embed.call_args.kwargs.get("mode") == "query"

    def test_post_variant_matches_get(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        _seed_embedding(db, seed_paper, "test-embed-model", [1.0, 0.0])
        chunk_id = _seed_chunk(db, seed_paper, "filler text", page_start=1)
        _seed_chunk_embedding(db, chunk_id, "test-embed-model", [1.0, 0.0])
        with patch("routers.search.embed_texts", return_value=[[1.0, 0.0]]):
            resp = client.post("/api/search/passages", json={"q": "test query", "top_k": 5})
        assert resp.status_code == 200
        assert resp.json()["mode"] == "semantic"

    def test_hybrid_mode_when_bm25_and_cosine_both_contribute(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        _seed_embedding(db, seed_paper, "test-embed-model", [1.0, 0.0])
        chunk_id = _seed_chunk(
            db, seed_paper, "adhesive bonding strength between printed clay layers", page_start=2,
        )
        _seed_chunk_embedding(db, chunk_id, "test-embed-model", [1.0, 0.0])

        with patch("routers.search.embed_texts", return_value=[[1.0, 0.0]]):
            resp = client.get(
                "/api/search/passages",
                params={"q": "adhesive bonding clay layers", "top_k": 5},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "hybrid"
        assert any(r["paper_id"] == seed_paper for r in body["results"])

    def test_two_stage_excludes_chunks_from_papers_outside_top_n(self, client, db, seed_paper, monkeypatch):
        """The paper-level stage runs first: a chunk whose OWN vector matches
        perfectly must not surface if its paper doesn't make the paper-level
        top-N cut."""
        import routers.search as search_router

        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        monkeypatch.setattr(search_router, "PASSAGE_TOP_N_PAPERS", 1)

        second = _seed_second_paper(db)
        # seed_paper: strong paper-level match -> makes the top-1 cut.
        _seed_embedding(db, seed_paper, "test-embed-model", [1.0, 0.0])
        # second: paper-level vector orthogonal (excluded from top-1), but its
        # OWN chunk vector is a perfect match to the query.
        _seed_embedding(db, second, "test-embed-model", [0.0, 1.0])
        excluded_chunk = _seed_chunk(db, second, "irrelevant text for bm25", page_start=1)
        _seed_chunk_embedding(db, excluded_chunk, "test-embed-model", [1.0, 0.0])
        included_chunk = _seed_chunk(db, seed_paper, "irrelevant text for bm25 too", page_start=1)
        _seed_chunk_embedding(db, included_chunk, "test-embed-model", [1.0, 0.0])

        with patch("routers.search.embed_texts", return_value=[[1.0, 0.0]]):
            resp = client.get("/api/search/passages", params={"q": "some query", "top_k": 5})

        assert resp.status_code == 200
        body = resp.json()
        result_paper_ids = {r["paper_id"] for r in body["results"]}
        assert seed_paper in result_paper_ids
        assert second not in result_paper_ids

    def test_no_embed_model_configured_falls_back_to_bm25(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "")
        _seed_chunk(db, seed_paper, "Test Paper Title appears verbatim here")
        with patch("routers.search.embed_texts") as mock_embed:
            resp = client.get("/api/search/passages", params={"q": "Test Paper Title"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "bm25"
        mock_embed.assert_not_called()
        assert any(r["paper_id"] == seed_paper for r in body["results"])

    def test_no_chunk_embeddings_falls_back_to_bm25(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        _seed_embedding(db, seed_paper, "test-embed-model", [1.0, 0.0])
        _seed_chunk(db, seed_paper, "some chunk text, never embedded")
        with patch("routers.search.embed_texts") as mock_embed:
            resp = client.get("/api/search/passages", params={"q": "some chunk text"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "bm25"
        assert body.get("note")
        mock_embed.assert_not_called()

    def test_embed_http_failure_degrades_to_bm25(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        _seed_embedding(db, seed_paper, "test-embed-model", [1.0, 0.0])
        chunk_id = _seed_chunk(db, seed_paper, "some chunk text")
        _seed_chunk_embedding(db, chunk_id, "test-embed-model", [1.0, 0.0])
        with patch("routers.search.embed_texts", side_effect=RuntimeError("gateway down")):
            resp = client.get("/api/search/passages", params={"q": "some chunk text"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "bm25"
        assert body.get("note")

    def test_empty_query_returns_empty_results_no_error(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        resp = client.get("/api/search/passages", params={"q": ""})
        assert resp.status_code == 200
        assert resp.json() == {"mode": "bm25", "results": []}

    def test_top_k_limits_results(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        _seed_embedding(db, seed_paper, "test-embed-model", [1.0, 0.0])
        c1 = _seed_chunk(db, seed_paper, "chunk one filler", chunk_index=0, page_start=1)
        c2 = _seed_chunk(db, seed_paper, "chunk two filler", chunk_index=1, page_start=2)
        _seed_chunk_embedding(db, c1, "test-embed-model", [1.0, 0.0])
        _seed_chunk_embedding(db, c2, "test-embed-model", [0.9, 0.1])
        with patch("routers.search.embed_texts", return_value=[[1.0, 0.0]]):
            resp = client.get("/api/search/passages", params={"q": "test", "top_k": 1})
        body = resp.json()
        assert len(body["results"]) == 1
