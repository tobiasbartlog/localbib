"""Integration tests for the new Metadata Validation HITL endpoints.

POST /api/papers/{id}/validate/propose — pure read; returns a Proposal.
POST /api/papers/{id}/validate/apply — persists accepted changes.

These exercise the wiring between the FastAPI route, the
``metadata_validation.propose()`` function, and the DB persistence — not
the policy itself, which is covered by ``test_metadata_validation.py``.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import metadata_validation
import webapp
from literature_manager import Config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_propose_to(monkeypatch, **kwargs):
    """Replace metadata_validation.propose with a stub that returns a
    Proposal built from the given kwargs."""

    def _fake_propose(paper, pdf_text, **_):
        return metadata_validation.Proposal(
            changes=kwargs.get("changes", {}),
            current=kwargs.get("current", {}),
            source_per_field=kwargs.get("source_per_field", {}),
            category_suggestions=kwargs.get("category_suggestions", []),
            warnings=kwargs.get("warnings", []),
            confidence=kwargs.get("confidence", "high"),
            diagnostics=kwargs.get("diagnostics", {"crossref_data": None, "llm_data": {}}),
        )

    monkeypatch.setattr(webapp.metadata_validation, "propose", _fake_propose)
    monkeypatch.setattr(
        webapp, "extract_text_from_pdf", lambda *a, **kw: "some pdf text"
    )


# ---------------------------------------------------------------------------
# /validate/propose
# ---------------------------------------------------------------------------

class TestValidatePropose:
    def test_returns_proposal_shape(self, client, seed_paper, monkeypatch):
        _stub_propose_to(
            monkeypatch,
            changes={"title": "Corrected Title"},
            current={"title": "Test Paper Title"},
            source_per_field={"title": "crossref"},
            warnings=["existing DOI checked"],
            confidence="high",
        )

        resp = client.post(
            f"/api/papers/{seed_paper}/validate/propose",
            json={"use_llm": False, "pages": 5},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["paper_id"] == seed_paper
        assert body["changes"] == {"title": "Corrected Title"}
        assert body["current"] == {"title": "Test Paper Title"}
        assert body["source_per_field"] == {"title": "crossref"}
        assert body["warnings"] == ["existing DOI checked"]
        assert body["confidence"] == "high"
        assert "diagnostics" in body
        assert "category_suggestions" in body

    def test_does_not_persist(self, client, seed_paper, db, monkeypatch):
        """The /propose endpoint must not write to the database."""
        _stub_propose_to(
            monkeypatch,
            changes={"title": "Should NOT be persisted by propose"},
            current={"title": "Test Paper Title"},
        )

        # Snapshot the title before the call
        conn = db._connect()
        try:
            before = conn.execute(
                "SELECT title FROM papers WHERE id = ?", (seed_paper,)
            ).fetchone()["title"]
        finally:
            conn.close()

        resp = client.post(
            f"/api/papers/{seed_paper}/validate/propose",
            json={"use_llm": False, "pages": 5},
        )
        assert resp.status_code == 200

        conn = db._connect()
        try:
            after = conn.execute(
                "SELECT title FROM papers WHERE id = ?", (seed_paper,)
            ).fetchone()["title"]
        finally:
            conn.close()
        assert before == after

    def test_404_when_paper_missing(self, client, db):
        resp = client.post(
            "/api/papers/999999/validate/propose",
            json={"use_llm": False},
        )
        assert resp.status_code == 404

    def test_404_when_pdf_missing(self, client, db, monkeypatch):
        """Paper row exists but the file on disk does not."""
        conn = db._connect()
        try:
            cur = conn.execute(
                "INSERT INTO papers (file_hash, filename, original_filename, title) "
                "VALUES (?, ?, ?, ?)",
                ("ghost", "does_not_exist.pdf", "does_not_exist.pdf", "Ghost"),
            )
            pid = cur.lastrowid
            conn.commit()
        finally:
            conn.close()

        resp = client.post(
            f"/api/papers/{pid}/validate/propose",
            json={"use_llm": False},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /validate/apply
# ---------------------------------------------------------------------------

class TestValidateApply:
    def test_writes_accepted_changes(self, client, seed_paper, db):
        resp = client.post(
            f"/api/papers/{seed_paper}/validate/apply",
            json={
                "changes": {"title": "Brand New Title", "abstract": "New abstract."},
                "category_assignments": [],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["title"] == "Brand New Title"
        assert body["abstract"] == "New abstract."

        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT title, abstract FROM papers WHERE id = ?", (seed_paper,)
            ).fetchone()
        finally:
            conn.close()
        assert row["title"] == "Brand New Title"
        assert row["abstract"] == "New abstract."

    def test_ignores_fields_not_in_request(self, client, seed_paper, db):
        """Apply must only touch fields present in `changes`; other fields stay."""
        resp = client.post(
            f"/api/papers/{seed_paper}/validate/apply",
            json={"changes": {"abstract": "Just the abstract"}},
        )
        assert resp.status_code == 200, resp.text

        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT title, authors, abstract FROM papers WHERE id = ?",
                (seed_paper,),
            ).fetchone()
        finally:
            conn.close()
        # seed_paper had title "Test Paper Title", authors "Doe, John"
        assert row["title"] == "Test Paper Title"
        assert row["authors"] == "Doe, John"
        assert row["abstract"] == "Just the abstract"

    def test_empty_changes_returns_paper_unchanged(self, client, seed_paper, db):
        resp = client.post(
            f"/api/papers/{seed_paper}/validate/apply",
            json={"changes": {}},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["title"] == "Test Paper Title"
        assert body["id"] == seed_paper

    def test_renames_file_when_title_changes(self, client, seed_paper, db):
        """Changing the title should rename the PDF on disk via generate_filename."""
        old_filepath = Path(Config.ALL_DIR) / "test_paper.pdf"
        assert old_filepath.exists()

        resp = client.post(
            f"/api/papers/{seed_paper}/validate/apply",
            json={"changes": {
                "title": "Distinctive New Title For Renaming",
                "authors": "Renamer, Test",
                "year": 2025,
            }},
        )
        assert resp.status_code == 200, resp.text

        conn = db._connect()
        try:
            new_name = conn.execute(
                "SELECT filename FROM papers WHERE id = ?", (seed_paper,)
            ).fetchone()["filename"]
        finally:
            conn.close()

        assert new_name != "test_paper.pdf"
        assert (Path(Config.ALL_DIR) / new_name).exists()
        assert not old_filepath.exists()

    def test_persists_category_assignments(self, client, seed_paper, db):
        # Insert a category to assign
        conn = db._connect()
        try:
            cur = conn.execute(
                "INSERT INTO categories (name, description, keywords) VALUES (?, ?, ?)",
                ("TestCat", "", ""),
            )
            cat_id = cur.lastrowid
            conn.commit()
        finally:
            conn.close()

        resp = client.post(
            f"/api/papers/{seed_paper}/validate/apply",
            json={
                "changes": {},
                "category_assignments": [
                    {"category_id": cat_id, "confidence": 0.87}
                ],
            },
        )
        assert resp.status_code == 200, resp.text

        conn = db._connect()
        try:
            rows = conn.execute(
                "SELECT category_id, confidence FROM paper_categories WHERE paper_id = ?",
                (seed_paper,),
            ).fetchall()
        finally:
            conn.close()
        assigned = {r["category_id"]: r["confidence"] for r in rows}
        assert cat_id in assigned
        assert assigned[cat_id] == pytest.approx(0.87)

    def test_404_when_paper_missing(self, client, db):
        resp = client.post(
            "/api/papers/999999/validate/apply",
            json={"changes": {"title": "X"}},
        )
        assert resp.status_code == 404
