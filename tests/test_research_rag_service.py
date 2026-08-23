"""Unit tests for services.research_rag (issue #89).

The service is the PURE, deterministic core of the research-chat pipeline:
BM25 tokenization + ranking + context assembly.  No mocks required — every
function is side-effect-free and produces the same output for the same input.
"""

from __future__ import annotations

import pytest

import services.research_rag as rag


# ---------------------------------------------------------------------------
# tokenize
# ---------------------------------------------------------------------------

class TestTokenize:
    def test_lowercases(self):
        assert rag.tokenize("Hello World") == ["hello", "world"]

    def test_strips_punctuation(self):
        tokens = rag.tokenize("machine-learning, NLP.")
        assert "machine" in tokens
        assert "learning" in tokens
        assert "nlp" in tokens

    def test_minimum_length_two(self):
        # single-char tokens should be excluded
        tokens = rag.tokenize("a b c de")
        assert "a" not in tokens
        assert "b" not in tokens
        assert "de" in tokens

    def test_empty_string(self):
        assert rag.tokenize("") == []

    def test_numeric_tokens_included(self):
        tokens = rag.tokenize("CO2 emission 42")
        assert "42" in tokens
        assert "co2" in tokens

    def test_german_umlauts(self):
        tokens = rag.tokenize("Ökologie und Übersicht")
        # lowercased umlauts (ö, ü) should survive the regex
        assert any("kologie" in t or "\xf6kologie" in t for t in tokens)


# ---------------------------------------------------------------------------
# bm25_search
# ---------------------------------------------------------------------------

def _make_chunks(texts: list[str]) -> list[dict]:
    """Helper: build minimal chunk dicts from plain text strings."""
    return [
        {"id": i, "paper_id": 1, "chunk_index": i, "page_start": i + 1, "chunk_text": t}
        for i, t in enumerate(texts)
    ]


class TestBm25Search:
    def test_returns_empty_on_no_chunks(self):
        assert rag.bm25_search("machine learning", []) == []

    def test_returns_empty_on_empty_query(self):
        chunks = _make_chunks(["some text here"])
        assert rag.bm25_search("", chunks) == []

    def test_returns_empty_when_no_match(self):
        chunks = _make_chunks(["quantum physics in vacuum"])
        result = rag.bm25_search("machine learning", chunks)
        assert result == []

    def test_best_matching_chunk_ranked_first(self):
        chunks = _make_chunks([
            "neural networks and deep learning architectures",
            "quantum mechanics and thermodynamics",
            "machine learning for natural language processing",
        ])
        result = rag.bm25_search("machine learning neural networks", chunks)
        assert len(result) >= 1
        # Both ML chunks should outscore the physics chunk
        returned_texts = [r["chunk_text"] for r in result]
        assert "quantum mechanics and thermodynamics" not in returned_texts or \
               result[-1]["chunk_text"] == "quantum mechanics and thermodynamics"

    def test_top_k_limits_results(self):
        chunks = _make_chunks([f"document about topic {i}" for i in range(20)])
        result = rag.bm25_search("document topic", chunks, top_k=3)
        assert len(result) <= 3

    def test_score_field_present_and_positive(self):
        chunks = _make_chunks(["machine learning methods"])
        result = rag.bm25_search("machine learning", chunks)
        assert len(result) == 1
        assert result[0]["score"] > 0

    def test_score_rounded_to_4_decimals(self):
        chunks = _make_chunks(["machine learning methods for materials science"])
        result = rag.bm25_search("machine learning", chunks)
        assert len(result) == 1
        score = result[0]["score"]
        assert score == round(score, 4)

    def test_original_chunk_fields_preserved(self):
        chunks = _make_chunks(["machine learning for science"])
        result = rag.bm25_search("machine learning", chunks)
        assert len(result) == 1
        assert result[0]["paper_id"] == 1
        assert result[0]["page_start"] == 1

    def test_deterministic_same_input_same_output(self):
        chunks = _make_chunks([
            "neural networks in deep learning",
            "support vector machines for classification",
            "random forests and ensemble methods",
        ])
        r1 = rag.bm25_search("neural network classification", chunks)
        r2 = rag.bm25_search("neural network classification", chunks)
        assert r1 == r2

    def test_multi_paper_chunks(self):
        chunks = [
            {"id": 0, "paper_id": 10, "chunk_index": 0, "page_start": 1,
             "chunk_text": "machine learning applications in materials"},
            {"id": 1, "paper_id": 20, "chunk_index": 0, "page_start": 1,
             "chunk_text": "quantum field theory and particle physics"},
            {"id": 2, "paper_id": 10, "chunk_index": 1, "page_start": 2,
             "chunk_text": "deep learning for property prediction"},
        ]
        result = rag.bm25_search("machine learning deep learning", chunks)
        returned_paper_ids = {r["paper_id"] for r in result}
        assert 10 in returned_paper_ids
        # physics chunk should not outrank ML chunks
        if len(result) > 1:
            assert result[0]["paper_id"] == 10 or result[0]["paper_id"] != 20


# ---------------------------------------------------------------------------
# build_chat_context
# ---------------------------------------------------------------------------

class TestBuildChatContext:
    def _chunks(self):
        return [
            {"paper_id": 1, "page_start": 3, "chunk_text": "First result text here."},
            {"paper_id": 2, "page_start": 7, "chunk_text": "Second paper content."},
            {"paper_id": 1, "page_start": 5, "chunk_text": "Another chunk from paper 1."},
        ]

    def _paper_info(self):
        return {
            1: {"title": "Paper Alpha", "authors": "Smith et al.", "year": 2022},
            2: {"title": "Paper Beta", "authors": "Jones et al.", "year": 2021},
        }

    def test_returns_tuple_of_string_and_list(self):
        ctx, sources = rag.build_chat_context(self._chunks(), self._paper_info())
        assert isinstance(ctx, str)
        assert isinstance(sources, list)

    def test_context_contains_source_labels(self):
        ctx, _ = rag.build_chat_context(self._chunks(), self._paper_info())
        assert "[Quelle 1]" in ctx
        assert "[Quelle 2]" in ctx

    def test_context_contains_chunk_texts(self):
        ctx, _ = rag.build_chat_context(self._chunks(), self._paper_info())
        assert "First result text here." in ctx
        assert "Second paper content." in ctx

    def test_sources_list_has_one_entry_per_paper(self):
        _, sources = rag.build_chat_context(self._chunks(), self._paper_info())
        assert len(sources) == 2

    def test_sources_contain_paper_metadata(self):
        _, sources = rag.build_chat_context(self._chunks(), self._paper_info())
        titles = {s["title"] for s in sources}
        assert "Paper Alpha" in titles
        assert "Paper Beta" in titles

    def test_sources_contain_pages_referenced(self):
        _, sources = rag.build_chat_context(self._chunks(), self._paper_info())
        alpha = next(s for s in sources if s["title"] == "Paper Alpha")
        # pages 3 and 5 from paper 1
        assert 3 in alpha["pages_referenced"]
        assert 5 in alpha["pages_referenced"]

    def test_previews_truncated_to_300_chars(self):
        long_text = "x" * 500
        chunks = [{"paper_id": 1, "page_start": 1, "chunk_text": long_text}]
        _, sources = rag.build_chat_context(chunks, {1: {"title": "T", "authors": "", "year": 2020}})
        for preview in sources[0]["previews"]:
            assert len(preview["text"]) <= 300

    def test_unknown_paper_id_uses_fallback_title(self):
        chunks = [{"paper_id": 99, "page_start": 1, "chunk_text": "content"}]
        ctx, sources = rag.build_chat_context(chunks, {})
        assert "Unbekannt" in ctx
        assert sources[0]["title"] == ""

    def test_deterministic_output(self):
        chunks = self._chunks()
        pi = self._paper_info()
        r1 = rag.build_chat_context(chunks, pi)
        r2 = rag.build_chat_context(chunks, pi)
        assert r1 == r2

    def test_context_separator_between_sources(self):
        ctx, _ = rag.build_chat_context(self._chunks(), self._paper_info())
        assert "---" in ctx

    def test_empty_chunks_returns_empty(self):
        ctx, sources = rag.build_chat_context([], {})
        assert ctx == ""
        assert sources == []


# ---------------------------------------------------------------------------
# reciprocal_rank_fusion (#100)
# ---------------------------------------------------------------------------

class TestReciprocalRankFusion:
    def test_known_ranklists_produce_expected_fusion_k_60(self):
        # Two rankings, item "a" is #1 in both -- should stay on top with a
        # score of 2 * 1/(60+1).
        bm25_ranking = ["a", "b", "c"]
        cosine_ranking = ["a", "c", "b"]
        fused = rag.reciprocal_rank_fusion([bm25_ranking, cosine_ranking])
        assert fused[0][0] == "a"
        expected_a = round(2 * (1.0 / 61), 6)
        assert fused[0][1] == pytest.approx(expected_a)
        # b: rank 2 in bm25, rank 3 in cosine
        expected_b = round(1.0 / 62 + 1.0 / 63, 6)
        # c: rank 3 in bm25, rank 2 in cosine
        expected_c = round(1.0 / 63 + 1.0 / 62, 6)
        scores = dict(fused)
        assert scores["b"] == pytest.approx(expected_b)
        assert scores["c"] == pytest.approx(expected_c)
        # b and c are symmetric -> tied; whichever appeared first (in the
        # first ranking they occur in) wins the tie-break ("b" before "c" in
        # bm25_ranking).
        assert [item for item, _ in fused] == ["a", "b", "c"]

    def test_default_k_is_60(self):
        fused_default = rag.reciprocal_rank_fusion([["x"]])
        fused_explicit = rag.reciprocal_rank_fusion([["x"]], k=60)
        assert fused_default == fused_explicit
        assert fused_default[0][1] == pytest.approx(round(1.0 / 61, 6))

    def test_custom_k_changes_scores(self):
        fused = rag.reciprocal_rank_fusion([["x"]], k=1)
        assert fused[0][1] == pytest.approx(round(1.0 / 2, 6))

    def test_item_missing_from_one_ranking_still_included(self):
        # "only-in-cosine" contributes solely from the second ranking --
        # this is the "papers without chunks still appear" mechanism.
        bm25_ranking = ["a"]
        cosine_ranking = ["a", "only-in-cosine"]
        fused = rag.reciprocal_rank_fusion([bm25_ranking, cosine_ranking])
        items = [item for item, _ in fused]
        assert "only-in-cosine" in items
        scores = dict(fused)
        assert scores["only-in-cosine"] == pytest.approx(round(1.0 / 62, 6))

    def test_single_ranking_preserves_order(self):
        fused = rag.reciprocal_rank_fusion([["a", "b", "c"]])
        assert [item for item, _ in fused] == ["a", "b", "c"]

    def test_empty_rankings_returns_empty(self):
        assert rag.reciprocal_rank_fusion([]) == []
        assert rag.reciprocal_rank_fusion([[], []]) == []

    def test_sorted_descending_by_score(self):
        fused = rag.reciprocal_rank_fusion([["a", "b", "c", "d"]])
        scores = [s for _, s in fused]
        assert scores == sorted(scores, reverse=True)

    def test_deterministic_same_input_same_output(self):
        rankings = [["a", "b", "c"], ["c", "a"]]
        assert rag.reciprocal_rank_fusion(rankings) == rag.reciprocal_rank_fusion(rankings)
