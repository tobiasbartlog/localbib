"""Shared pytest fixtures.

CRITICAL ordering note: this file imports `webapp`, which at module-import
time calls `Config.init_paths(BASE_DIR)` and creates a module-level
`db = Database(Config.DB_PATH)`. We must redirect `LITERATUR_BASE_DIR`
to a temp directory BEFORE that import happens — otherwise the test
suite would write into the user's real library.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_test_base = Path(tempfile.mkdtemp(prefix="lit-mgr-test-"))
os.environ["LITERATUR_BASE_DIR"] = str(_test_base)
os.environ.setdefault("KICONNECT_API_KEY", "test-key")
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("CROSSREF_MAILTO", "test@example.com")

_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import pytest
from fastapi.testclient import TestClient

import webapp
import routers.bibtex_import as _bibtex_router
from literature_manager import Config


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_enrichment: run the real BibTeX-import enrichment (network) instead "
        "of the offline stub",
    )


@pytest.fixture(autouse=True)
def _stub_import_enrichment(request, monkeypatch):
    """Auto-Anreicherung des BibTeX-Imports macht OpenAlex/CrossRef-Calls. Die
    Suite laeuft offline, also standardmaessig neutralisieren. Tests, die die
    Anreicherung selbst pruefen, markieren sich mit ``real_enrichment``."""
    if request.node.get_closest_marker("real_enrichment"):
        return
    # _enrich_imported_papers moved to routers/bibtex_import.py (#84) — patch there.
    monkeypatch.setattr(_bibtex_router, "_enrich_imported_papers", lambda created: 0)


@pytest.fixture
def client() -> TestClient:
    return TestClient(webapp.app)


@pytest.fixture
def db():
    """Yield the app's Database, with all tables truncated for isolation."""
    conn = webapp.db._connect()
    try:
        # Order matters because of FKs with CASCADE
        for table in [
            "paper_references",
            "paper_categories",
            "chunk_embeddings",
            "paper_chunks",
            "paper_embeddings",
            "paper_custom_values",
            "paper_projects",
            "papers",
            "categories",
            "custom_fields",
            "projects",
        ]:
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                pass  # table may not exist in older schema variants
        conn.commit()
    finally:
        conn.close()
    return webapp.db


@pytest.fixture
def seed_paper(db):
    """Insert one paper row + create a dummy PDF file at Config.ALL_DIR/<filename>.

    Returns the paper_id. PDF content is irrelevant because every test that
    uses this fixture monkey-patches the PDF text-extraction boundary.
    """
    filename = "test_paper.pdf"
    filepath = Path(Config.ALL_DIR) / filename
    filepath.write_bytes(b"%PDF-1.4 fake content for tests")

    conn = db._connect()
    try:
        cur = conn.execute(
            """INSERT INTO papers (file_hash, filename, original_filename,
               title, authors, year, doi)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "test-hash-1",
                filename,
                filename,
                "Test Paper Title",
                "Doe, John",
                2024,
                "10.1000/test",
            ),
        )
        paper_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return paper_id
