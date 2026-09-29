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
# The LLM connection document (llm.json) sits next to the developer's .env —
# redirect it too, or a test's PUT /api/llm/config would rewrite the real one.
os.environ["LLM_CONFIG_PATH"] = str(_test_base / "llm.json")
# No llm.json in the temp dir, so Config migrates these flat keys into one
# connection "default" (custom, http://llm.test/v1) with reasoning + fast bound
# to "test-model": the LLM gates are ON by default, embeddings OFF, and no
# test ever reaches a real endpoint. Per-test shapes: tests/llm_helpers.py.
os.environ["LLM_PROVIDER"] = "custom"
os.environ["LLM_BASE_URL"] = "http://llm.test/v1"
os.environ["KICONNECT_API_KEY"] = "test-key"
os.environ["LLM_MODEL"] = "test-model"
for _legacy in ("LLM_API_KEY", "LLM_MODEL_FAST", "LLM_EMBED_MODEL", "LLM_EMBED_URL"):
    os.environ.pop(_legacy, None)
os.environ.setdefault("CROSSREF_MAILTO", "test@example.com")
# The Add-on document (plugins.json) and the Bundle root likewise: a test that
# toggles a plugin writes the document, and no test may install into the
# developer's %LOCALAPPDATA%. Dev-Suchpfade from the shell are ignored.
os.environ["PLUGINS_CONFIG_PATH"] = str(_test_base / "plugins.json")
os.environ["LOCALBIB_PLUGIN_DIR"] = str(_test_base / "addon-bundles")
os.environ.pop("LOCALBIB_PLUGIN_DEV_PATHS", None)

_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import pytest
from fastapi.testclient import TestClient

import webapp
import paper_ingest
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
    # Both intake paths (BibTeX commit, Paper per DOI) call the neutral module
    # paper_ingest (ADR-0016) — patch there. The OpenAlex batch also feeds the
    # DOI intake's OA attempt, so an empty batch means "pdf=none" offline.
    monkeypatch.setattr(paper_ingest, "enrich_imported_papers",
                        lambda created, works=None: 0)
    monkeypatch.setattr(paper_ingest, "fetch_openalex_works", lambda dois: {})


@pytest.fixture(autouse=True)
def _no_real_marketplace_network(monkeypatch):
    """``GET /api/version-check`` piggybacks a Marketplace-Index refresh onto
    every poll (ADR-0021, #189, ``marketplace_client.refresh_if_stale``) — a
    test that has nothing to do with the Marketplace must never actually hit
    GitHub for it. Default: instant "offline", so the piggyback is a no-op in
    every test but the ones that explicitly exercise it (which monkeypatch
    ``marketplace_client.requests.get`` themselves, overriding this)."""
    import marketplace_client

    def _offline(*args, **kwargs):
        raise OSError("network disabled in tests")

    monkeypatch.setattr(marketplace_client.requests, "get", _offline)


@pytest.fixture(autouse=True)
def _isolate_plugins_document():
    """No test leaves plugins.json behind for the next one.

    Once the document exists it is the truth for the legacy plugin switches
    and Config mirrors them into the process environment — a stray file would
    override the next test's ``monkeypatch.setenv``. So: remove it after each
    test, put the legacy keys back as they were, and reload."""
    import plugins_config

    keys = list(plugins_config.LEGACY_ENV_KEYS) + [
        "PLUGINS_CONFIG_PATH", "LOCALBIB_PLUGIN_DIR", "LOCALBIB_PLUGIN_DEV_PATHS"]
    before = {k: os.environ.get(k) for k in keys}
    yield
    path = Path(os.environ.get("PLUGINS_CONFIG_PATH") or Config.PLUGINS_CONFIG_PATH)
    dirty = path.exists() or Config.PLUGINS_DOCUMENT_STORED
    for key, value in before.items():
        if os.environ.get(key) != value:
            dirty = True
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    if path.exists():
        path.unlink()
    if dirty:
        Config.reload_from_env()


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
