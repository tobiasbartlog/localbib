"""Tests for embedding_index.py (#98): paper-level embedding indexer.

Covers the neutral module directly (pack/unpack, substitute-abstract text
assembly, embed_paper_to_db, reindex_papers, embedding_status) plus the two
Maintenance endpoints that expose it. ``llm_client.embed_texts`` is mocked —
no real HTTP calls.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import embedding_index
import import_indexing
from literature_manager import Config


# ---------------------------------------------------------------------------
# pack_vector / unpack_vector
# ---------------------------------------------------------------------------

class TestVectorPacking:
    def test_round_trip(self):
        vector = [0.1, -0.5, 3.25, 0.0]
        blob = embedding_index.pack_vector(vector)
        assert isinstance(blob, bytes)
        restored = embedding_index.unpack_vector(blob)
        assert restored == pytest.approx(vector, abs=1e-6)


# ---------------------------------------------------------------------------
# _document_text
# ---------------------------------------------------------------------------

class TestDocumentText:
    def test_title_and_abstract(self, db, seed_paper):
        conn = db._connect()
        try:
            conn.execute(
                "UPDATE papers SET abstract = ? WHERE id = ?",
                ("This is the abstract.", seed_paper),
            )
            conn.commit()
            paper = dict(conn.execute("SELECT * FROM papers WHERE id = ?", (seed_paper,)).fetchone())
            text = embedding_index._document_text(conn, paper)
        finally:
            conn.close()
        assert text == "Test Paper Title\nThis is the abstract."

    def test_substitute_abstract_from_chunks(self, db, seed_paper):
        conn = db._connect()
        try:
            # Kein Abstract, aber Chunks vorhanden.
            conn.execute(
                "INSERT INTO paper_chunks (paper_id, chunk_index, page_start, page_end, chunk_text) "
                "VALUES (?, 0, 1, 1, ?)",
                (seed_paper, "Chunk eins Text."),
            )
            conn.execute(
                "INSERT INTO paper_chunks (paper_id, chunk_index, page_start, page_end, chunk_text) "
                "VALUES (?, 1, 1, 1, ?)",
                (seed_paper, "Chunk zwei Text."),
            )
            conn.commit()
            paper = dict(conn.execute("SELECT * FROM papers WHERE id = ?", (seed_paper,)).fetchone())
            text = embedding_index._document_text(conn, paper)
        finally:
            conn.close()
        assert text.startswith("Test Paper Title\n")
        assert "Chunk eins Text." in text
        assert "Chunk zwei Text." in text

    def test_substitute_abstract_truncated_to_1500_chars(self, db, seed_paper):
        conn = db._connect()
        try:
            long_text = "x" * 5000
            conn.execute(
                "INSERT INTO paper_chunks (paper_id, chunk_index, page_start, page_end, chunk_text) "
                "VALUES (?, 0, 1, 1, ?)",
                (seed_paper, long_text),
            )
            conn.commit()
            paper = dict(conn.execute("SELECT * FROM papers WHERE id = ?", (seed_paper,)).fetchone())
            text = embedding_index._document_text(conn, paper)
        finally:
            conn.close()
        abstract_part = text.split("\n", 1)[1]
        assert len(abstract_part) <= embedding_index.SUBSTITUTE_ABSTRACT_CHARS

    def test_title_only_when_ungechunkt_and_no_abstract(self, db, seed_paper):
        conn = db._connect()
        try:
            paper = dict(conn.execute("SELECT * FROM papers WHERE id = ?", (seed_paper,)).fetchone())
            text = embedding_index._document_text(conn, paper)
        finally:
            conn.close()
        assert text == "Test Paper Title"

    def test_none_when_neither_title_nor_abstract(self, db, seed_paper):
        conn = db._connect()
        try:
            conn.execute("UPDATE papers SET title = '' WHERE id = ?", (seed_paper,))
            conn.commit()
            paper = dict(conn.execute("SELECT * FROM papers WHERE id = ?", (seed_paper,)).fetchone())
            text = embedding_index._document_text(conn, paper)
        finally:
            conn.close()
        assert text is None


# ---------------------------------------------------------------------------
# embed_paper_to_db
# ---------------------------------------------------------------------------

class TestEmbedPaperToDb:
    def test_no_model_configured_returns_false_without_http(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "")
        with patch.object(embedding_index, "embed_texts") as mock_embed:
            assert embedding_index.embed_paper_to_db(seed_paper) is False
        mock_embed.assert_not_called()

    def test_unknown_paper_returns_false(self, db, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        with patch.object(embedding_index, "embed_texts") as mock_embed:
            assert embedding_index.embed_paper_to_db(999999) is False
        mock_embed.assert_not_called()

    def test_success_stores_vector(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.1, 0.2, 0.3]]) as mock_embed:
            assert embedding_index.embed_paper_to_db(seed_paper) is True
        mock_embed.assert_called_once()
        assert mock_embed.call_args.kwargs.get("mode") == "document"

        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT model, dim, vector FROM paper_embeddings WHERE paper_id = ?", (seed_paper,)
            ).fetchone()
        finally:
            conn.close()
        assert row["model"] == "test-embed-model"
        assert row["dim"] == 3
        assert embedding_index.unpack_vector(row["vector"]) == pytest.approx([0.1, 0.2, 0.3], abs=1e-5)

    def test_idempotent_skips_second_call(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.1, 0.2]]) as mock_embed:
            assert embedding_index.embed_paper_to_db(seed_paper) is True
            assert embedding_index.embed_paper_to_db(seed_paper) is True
        assert mock_embed.call_count == 1

    def test_http_failure_degrades_to_false(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        with patch.object(embedding_index, "embed_texts", side_effect=RuntimeError("gateway down")):
            assert embedding_index.embed_paper_to_db(seed_paper) is False


# ---------------------------------------------------------------------------
# reindex_papers
# ---------------------------------------------------------------------------

class TestReindexPapers:
    def test_no_model_configured_degrades(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "")
        result = embedding_index.reindex_papers()
        assert result == {"indexed": 0, "skipped": 0, "errors": 0, "total": 0, "model": "", "dim": 0}

    def test_indexes_all_papers(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.1, 0.2]]):
            result = embedding_index.reindex_papers()
        assert result["indexed"] == 1
        assert result["skipped"] == 0
        assert result["total"] == 1
        assert result["model"] == "test-embed-model"
        assert result["dim"] == 2

    def test_second_run_is_noop(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.1, 0.2]]) as mock_embed:
            embedding_index.reindex_papers()
            result = embedding_index.reindex_papers()
        assert result["indexed"] == 0
        assert result["skipped"] == 1
        assert mock_embed.call_count == 1  # zweiter Lauf ruft embed_texts gar nicht mehr auf

    def test_paper_without_abstract_or_chunks_falls_back_to_title(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.5]]) as mock_embed:
            embedding_index.reindex_papers()
        texts_sent = mock_embed.call_args.args[0]
        assert texts_sent == ["Test Paper Title"]

    def test_model_switch_triggers_reembed(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "model-a")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.1]]):
            embedding_index.reindex_papers()

        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "model-b")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.2, 0.3]]) as mock_embed:
            result = embedding_index.reindex_papers()
        assert result["indexed"] == 1
        assert result["skipped"] == 0
        mock_embed.assert_called_once()

        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT model, dim FROM paper_embeddings WHERE paper_id = ?", (seed_paper,)
            ).fetchone()
        finally:
            conn.close()
        assert row["model"] == "model-b"
        assert row["dim"] == 2

    def test_batch_error_counts_as_errors_not_crash(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        with patch.object(embedding_index, "embed_texts", side_effect=RuntimeError("gateway down")):
            result = embedding_index.reindex_papers()
        assert result["errors"] == 1
        assert result["indexed"] == 0


# ---------------------------------------------------------------------------
# _chunk_document_text (#102, PRD Entscheidung 9: context-enriched chunk input)
# ---------------------------------------------------------------------------

class TestChunkDocumentText:
    def test_title_year_journal_header(self):
        paper = {"title": "Lehm-3D-Druck", "year": 2023, "journal": "Journal of X"}
        text = embedding_index._chunk_document_text(paper, "Chunk-Inhalt hier.")
        assert text == "Lehm-3D-Druck (2023, Journal of X)\nChunk-Inhalt hier."

    def test_year_only_no_journal(self):
        paper = {"title": "Lehm-3D-Druck", "year": 2023, "journal": ""}
        text = embedding_index._chunk_document_text(paper, "Chunk-Inhalt.")
        assert text == "Lehm-3D-Druck (2023)\nChunk-Inhalt."

    def test_no_year_no_journal_falls_back_to_title_only(self):
        paper = {"title": "Lehm-3D-Druck", "year": None, "journal": ""}
        text = embedding_index._chunk_document_text(paper, "Chunk-Inhalt.")
        assert text == "Lehm-3D-Druck\nChunk-Inhalt."

    def test_missing_title_uses_year_journal_only(self):
        paper = {"title": "", "year": 2023, "journal": "Journal of X"}
        text = embedding_index._chunk_document_text(paper, "Chunk-Inhalt.")
        assert text == "(2023, Journal of X)\nChunk-Inhalt."

    def test_chunk_text_passed_through_verbatim(self):
        paper = {"title": "T", "year": 2020, "journal": ""}
        chunk_text = "Ein Satz mit Sonderzeichen: äöüß, 100%."
        text = embedding_index._chunk_document_text(paper, chunk_text)
        assert text.endswith("\n" + chunk_text)


# ---------------------------------------------------------------------------
# reindex_chunks (#102, PRD Phase 3)
# ---------------------------------------------------------------------------

def _insert_chunk(db, paper_id, chunk_text, chunk_index=0, page_start=1):
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


class TestReindexChunks:
    def test_no_model_configured_degrades(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "")
        result = embedding_index.reindex_chunks()
        assert result == {"indexed": 0, "skipped": 0, "errors": 0, "total": 0, "model": "", "dim": 0}

    def test_indexes_all_chunks(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        _insert_chunk(db, seed_paper, "Erster Chunk.", chunk_index=0, page_start=1)
        _insert_chunk(db, seed_paper, "Zweiter Chunk.", chunk_index=1, page_start=2)
        with patch.object(embedding_index, "embed_texts", return_value=[[0.1, 0.2], [0.3, 0.4]]):
            result = embedding_index.reindex_chunks()
        assert result["indexed"] == 2
        assert result["skipped"] == 0
        assert result["total"] == 2
        assert result["model"] == "test-embed-model"
        assert result["dim"] == 2

    def test_second_run_is_noop(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        _insert_chunk(db, seed_paper, "Ein Chunk.")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.1, 0.2]]) as mock_embed:
            embedding_index.reindex_chunks()
            result = embedding_index.reindex_chunks()
        assert result["indexed"] == 0
        assert result["skipped"] == 1
        assert mock_embed.call_count == 1

    def test_resumable_only_embeds_new_chunks(self, db, seed_paper, monkeypatch):
        """Idempotent + wiederaufnehmbar: a chunk added AFTER a first run is
        picked up on the next run without re-embedding the already-indexed one."""
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        _insert_chunk(db, seed_paper, "Erster Chunk.")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.1, 0.2]]) as mock_embed:
            embedding_index.reindex_chunks()
        _insert_chunk(db, seed_paper, "Zweiter Chunk.", chunk_index=1, page_start=2)
        with patch.object(embedding_index, "embed_texts", return_value=[[0.3, 0.4]]) as mock_embed_2:
            result = embedding_index.reindex_chunks()
        assert result["indexed"] == 1
        assert result["skipped"] == 1
        mock_embed_2.assert_called_once()
        assert mock_embed_2.call_args.args[0] == ["Test Paper Title (2024)\nZweiter Chunk."]

    def test_context_enriched_input_sent_to_embed_stored_chunk_text_unchanged(self, db, seed_paper, monkeypatch):
        """#102 AC: embedding input carries the paper context; the STORED
        paper_chunks.chunk_text stays byte-identical."""
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        conn = db._connect()
        try:
            conn.execute(
                "UPDATE papers SET year = 2024, journal = 'Journal Y' WHERE id = ?", (seed_paper,)
            )
            conn.commit()
        finally:
            conn.close()
        original_text = "Rohtext eines Chunks, unveraendert."
        chunk_id = _insert_chunk(db, seed_paper, original_text)

        with patch.object(embedding_index, "embed_texts", return_value=[[0.1]]) as mock_embed:
            embedding_index.reindex_chunks()

        texts_sent = mock_embed.call_args.args[0]
        assert texts_sent == ["Test Paper Title (2024, Journal Y)\n" + original_text]

        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT chunk_text FROM paper_chunks WHERE id = ?", (chunk_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row["chunk_text"] == original_text

    def test_model_switch_triggers_reembed(self, db, seed_paper, monkeypatch):
        chunk_id = _insert_chunk(db, seed_paper, "Ein Chunk.")
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "model-a")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.1]]):
            embedding_index.reindex_chunks()

        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "model-b")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.2, 0.3]]) as mock_embed:
            result = embedding_index.reindex_chunks()
        assert result["indexed"] == 1
        assert result["skipped"] == 0
        mock_embed.assert_called_once()

        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT model, dim FROM chunk_embeddings WHERE chunk_id = ?", (chunk_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row["model"] == "model-b"
        assert row["dim"] == 2

    def test_batch_error_counts_as_errors_not_crash(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        _insert_chunk(db, seed_paper, "Ein Chunk.")
        with patch.object(embedding_index, "embed_texts", side_effect=RuntimeError("gateway down")):
            result = embedding_index.reindex_chunks()
        assert result["errors"] == 1
        assert result["indexed"] == 0

    def test_no_chunks_is_a_noop(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        with patch.object(embedding_index, "embed_texts") as mock_embed:
            result = embedding_index.reindex_chunks()
        assert result == {"indexed": 0, "skipped": 0, "errors": 0, "total": 0, "model": "test-embed-model", "dim": 0}
        mock_embed.assert_not_called()


# ---------------------------------------------------------------------------
# embed_chunks_for_paper (#153) — the chunk-level twin of embed_paper_to_db
# ---------------------------------------------------------------------------

class TestEmbedChunksForPaper:
    def test_no_model_configured_degrades_to_zero(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "")
        _insert_chunk(db, seed_paper, "Ein Chunk.")
        with patch.object(embedding_index, "embed_texts") as mock_embed:
            assert embedding_index.embed_chunks_for_paper(seed_paper) == 0
        mock_embed.assert_not_called()

    def test_embeds_only_this_papers_chunks(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        conn = db._connect()
        try:
            cur = conn.execute(
                """INSERT INTO papers (file_hash, filename, original_filename, title)
                   VALUES ('other-hash', 'other.pdf', 'other.pdf', 'Anderes Paper')"""
            )
            other_paper = cur.lastrowid
            conn.commit()
        finally:
            conn.close()
        _insert_chunk(db, seed_paper, "Chunk des Zielpapers.")
        other_chunk = _insert_chunk(db, other_paper, "Chunk des anderen Papers.")

        with patch.object(embedding_index, "embed_texts", return_value=[[0.1, 0.2]]) as mock_embed:
            assert embedding_index.embed_chunks_for_paper(seed_paper) == 1
        assert mock_embed.call_args.args[0] == ["Test Paper Title (2024)\nChunk des Zielpapers."]

        conn = db._connect()
        try:
            rows = conn.execute("SELECT chunk_id FROM chunk_embeddings").fetchall()
        finally:
            conn.close()
        assert [r["chunk_id"] for r in rows] != [other_chunk]
        assert len(rows) == 1

    def test_second_call_is_a_noop(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        _insert_chunk(db, seed_paper, "Ein Chunk.")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.1, 0.2]]) as mock_embed:
            assert embedding_index.embed_chunks_for_paper(seed_paper) == 1
            assert embedding_index.embed_chunks_for_paper(seed_paper) == 0
        assert mock_embed.call_count == 1

    def test_model_switch_reembeds(self, db, seed_paper, monkeypatch):
        _insert_chunk(db, seed_paper, "Ein Chunk.")
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "model-a")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.1]]):
            embedding_index.embed_chunks_for_paper(seed_paper)
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "model-b")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.2, 0.3]]):
            assert embedding_index.embed_chunks_for_paper(seed_paper) == 1

    def test_gateway_failure_degrades_to_zero(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        _insert_chunk(db, seed_paper, "Ein Chunk.")
        with patch.object(embedding_index, "embed_texts", side_effect=RuntimeError("gateway down")):
            assert embedding_index.embed_chunks_for_paper(seed_paper) == 0

    def test_unknown_paper_is_a_noop(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        with patch.object(embedding_index, "embed_texts") as mock_embed:
            assert embedding_index.embed_chunks_for_paper(999999) == 0
        mock_embed.assert_not_called()

    def test_reindex_chunks_afterwards_skips_what_the_import_hook_did(self, db, seed_paper, monkeypatch):
        """The import hook and the maintenance full run share one idempotency
        rule — a paper indexed at import time is 'skipped', not re-embedded."""
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        _insert_chunk(db, seed_paper, "Ein Chunk.")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.1, 0.2]]):
            embedding_index.embed_chunks_for_paper(seed_paper)
        with patch.object(embedding_index, "embed_texts") as mock_embed:
            result = embedding_index.reindex_chunks()
        assert result == {
            "indexed": 0, "skipped": 1, "errors": 0, "total": 1,
            "model": "test-embed-model", "dim": 2,
        }
        mock_embed.assert_not_called()


# ---------------------------------------------------------------------------
# embedding_status
# ---------------------------------------------------------------------------

class TestEmbeddingStatus:
    def test_no_model_configured(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "")
        status = embedding_index.embedding_status()
        assert status == {
            "indexed": 0, "total": 1, "model": "", "dim": 0,
            "chunks_indexed": 0, "chunks_total": 0,
        }

    def test_reports_n_of_m_and_dim(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.1, 0.2, 0.3, 0.4]]):
            embedding_index.embed_paper_to_db(seed_paper)
        status = embedding_index.embedding_status()
        assert status == {
            "indexed": 1, "total": 1, "model": "test-embed-model", "dim": 4,
            "chunks_indexed": 0, "chunks_total": 0,
        }

    def test_reports_chunk_counts(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        conn = db._connect()
        try:
            conn.execute(
                "INSERT INTO paper_chunks (paper_id, chunk_index, page_start, page_end, chunk_text) "
                "VALUES (?, 0, 1, 1, 'Chunk eins.')",
                (seed_paper,),
            )
            conn.execute(
                "INSERT INTO paper_chunks (paper_id, chunk_index, page_start, page_end, chunk_text) "
                "VALUES (?, 1, 1, 1, 'Chunk zwei.')",
                (seed_paper,),
            )
            conn.commit()
        finally:
            conn.close()
        with patch.object(embedding_index, "embed_texts", return_value=[[0.1, 0.2], [0.3, 0.4]]):
            embedding_index.reindex_chunks()
        status = embedding_index.embedding_status()
        assert status["chunks_total"] == 2
        assert status["chunks_indexed"] == 2


# ---------------------------------------------------------------------------
# Maintenance endpoints
# ---------------------------------------------------------------------------

class TestMaintenanceEmbeddingEndpoints:
    def test_status_endpoint(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.1, 0.2]]):
            embedding_index.embed_paper_to_db(seed_paper)
        resp = client.get("/api/maintenance/embeddings/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "indexed": 1, "total": 1, "model": "test-embed-model", "dim": 2,
            "chunks_indexed": 0, "chunks_total": 0,
        }

    def test_status_endpoint_without_model(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "")
        resp = client.get("/api/maintenance/embeddings/status")
        assert resp.status_code == 200
        assert resp.json() == {
            "indexed": 0, "total": 1, "model": "", "dim": 0,
            "chunks_indexed": 0, "chunks_total": 0,
        }

    def test_reindex_endpoint(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.1, 0.2]]):
            resp = client.post("/api/maintenance/embeddings/reindex")
        assert resp.status_code == 200
        body = resp.json()
        assert body["indexed"] == 1
        assert body["total"] == 1
        assert body["model"] == "test-embed-model"
        assert body["dim"] == 2

    def test_reindex_endpoint_without_model_degrades(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "")
        resp = client.post("/api/maintenance/embeddings/reindex")
        assert resp.status_code == 200
        assert resp.json() == {"indexed": 0, "skipped": 0, "errors": 0, "total": 0, "model": "", "dim": 0}

    def test_reindex_chunks_endpoint(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        _insert_chunk(db, seed_paper, "Ein Chunk.")
        with patch.object(embedding_index, "embed_texts", return_value=[[0.1, 0.2]]):
            resp = client.post("/api/maintenance/embeddings/reindex-chunks")
        assert resp.status_code == 200
        body = resp.json()
        assert body["indexed"] == 1
        assert body["total"] == 1
        assert body["model"] == "test-embed-model"
        assert body["dim"] == 2

    def test_reindex_chunks_endpoint_without_model_degrades(self, client, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "")
        resp = client.post("/api/maintenance/embeddings/reindex-chunks")
        assert resp.status_code == 200
        assert resp.json() == {"indexed": 0, "skipped": 0, "errors": 0, "total": 0, "model": "", "dim": 0}


# ---------------------------------------------------------------------------
# Import path: embedding failure must never block the import (#98 AC)
# ---------------------------------------------------------------------------

class TestImportPathDegradation:
    def test_upload_succeeds_even_when_embedding_gateway_is_down(self, client, db, monkeypatch):
        """AC: 'Import mit ausgefallenem Gateway laeuft durch (Embedding fehlt, Paper da)'."""
        import importlib

        import_router = importlib.import_module("routers.import")
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")

        with patch.object(import_indexing, "embed_paper_to_db", side_effect=RuntimeError("gateway down")), \
             patch.object(import_indexing, "embed_chunks_for_paper", side_effect=RuntimeError("gateway down")), \
             patch.object(import_router, "process_paper", return_value=1), \
             patch.object(import_router, "_rematch_references_for_paper", return_value=None), \
             patch.object(import_indexing, "chunk_paper_to_db", return_value=0):
            conn = db._connect()
            try:
                conn.execute(
                    """INSERT INTO papers (id, file_hash, filename, original_filename, title)
                       VALUES (1, 'h', 'f.pdf', 'f.pdf', 'T')"""
                )
                conn.commit()
            finally:
                conn.close()
            resp = client.post(
                "/api/import/upload",
                files={"file": ("paper.pdf", b"%PDF-1.4 fake", "application/pdf")},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["paper_id"] == 1


# ---------------------------------------------------------------------------
# import_indexing.index_paper_after_import (#153) — the shared post-import hook
# ---------------------------------------------------------------------------

class TestIndexPaperAfterImport:
    def test_builds_all_three_indexes(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        with patch.object(import_indexing, "chunk_paper_to_db", return_value=7), \
             patch.object(import_indexing, "embed_paper_to_db", return_value=True), \
             patch.object(import_indexing, "embed_chunks_for_paper", return_value=7) as mock_chunks:
            result = import_indexing.index_paper_after_import(seed_paper, "irrelevant.pdf")
        assert result == {"chunks": 7, "paper_embedded": True, "chunk_vectors": 7}
        mock_chunks.assert_called_once_with(seed_paper)

    def test_chunk_vectors_are_built_for_a_freshly_chunked_paper(self, db, seed_paper, monkeypatch):
        """The #153 regression itself: chunks WITHOUT chunk_embeddings meant new
        papers silently fell back to BM25 until someone ran the reindex."""
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        with patch.object(import_indexing, "chunk_paper_to_db", side_effect=lambda pid, path: _insert_chunk(db, pid, "Frischer Chunk.") and 1), \
             patch.object(embedding_index, "embed_texts", return_value=[[0.1, 0.2]]):
            result = import_indexing.index_paper_after_import(seed_paper, "irrelevant.pdf")

        assert result["chunk_vectors"] == 1
        conn = db._connect()
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM chunk_embeddings ce JOIN paper_chunks c ON c.id = ce.chunk_id "
                "WHERE c.paper_id = ?",
                (seed_paper,),
            ).fetchone()[0]
        finally:
            conn.close()
        assert n == 1

    @pytest.mark.parametrize(
        "failing",
        ["chunk_paper_to_db", "embed_paper_to_db", "embed_chunks_for_paper"],
    )
    def test_never_raises_when_a_step_blows_up(self, db, seed_paper, monkeypatch, failing):
        """AC (#98/#153): a broken step degrades, it never blocks an import."""
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "test-embed-model")
        defaults = {
            "chunk_paper_to_db": 3,
            "embed_paper_to_db": True,
            "embed_chunks_for_paper": 3,
        }
        patches = [
            patch.object(import_indexing, name,
                         side_effect=RuntimeError("boom") if name == failing else None,
                         return_value=None if name == failing else value)
            for name, value in defaults.items()
        ]
        for p in patches:
            p.start()
        try:
            result = import_indexing.index_paper_after_import(seed_paper, "irrelevant.pdf")
        finally:
            for p in patches:
                p.stop()

        assert set(result) == {"chunks", "paper_embedded", "chunk_vectors"}
        if failing == "chunk_paper_to_db":
            assert result["chunks"] == 0
        if failing == "embed_paper_to_db":
            assert result["paper_embedded"] is False
        if failing == "embed_chunks_for_paper":
            assert result["chunk_vectors"] == 0

    def test_without_model_only_chunks_are_built(self, db, seed_paper, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "")
        with patch.object(import_indexing, "chunk_paper_to_db", return_value=4), \
             patch.object(embedding_index, "embed_texts") as mock_embed:
            result = import_indexing.index_paper_after_import(seed_paper, "irrelevant.pdf")
        assert result == {"chunks": 4, "paper_embedded": False, "chunk_vectors": 0}
        mock_embed.assert_not_called()
