"""Unit tests for network_orchestrator.build_network().

Tests use a mock OpenAlexClient + an in-memory SQLite DB so they exercise
the orchestrator's business logic without hitting the network or the
production schema.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import network_orchestrator
from openalex_client import Work


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _seed_db(conn: sqlite3.Connection, papers: list[dict]) -> None:
    """Set up a minimal schema mirroring the production tables touched by the
    orchestrator, then insert the given paper rows."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS papers (
            id INTEGER PRIMARY KEY,
            title TEXT,
            doi TEXT DEFAULT '',
            authors TEXT DEFAULT '',
            year INTEGER,
            openalex_id TEXT DEFAULT '',
            cited_by_count INTEGER DEFAULT 0,
            openalex_updated_at TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_references (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_paper_id INTEGER,
            doi TEXT DEFAULT '',
            title TEXT DEFAULT '',
            authors TEXT DEFAULT '',
            year INTEGER,
            journal TEXT DEFAULT '',
            matched_paper_id INTEGER
        )
        """
    )
    for p in papers:
        conn.execute(
            "INSERT INTO papers (id, title, doi, authors, year, openalex_id, cited_by_count) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                p["id"],
                p["title"],
                p.get("doi", ""),
                p.get("authors", ""),
                p.get("year"),
                p.get("openalex_id", ""),
                p.get("cited_by_count", 0),
            ),
        )
    conn.commit()


def _mock_client(
    *,
    by_doi: list[Work] | None = None,
    by_id: dict[str, Work] | None = None,
    by_title: Work | None = None,
) -> MagicMock:
    client = MagicMock()
    client.fetch_works_by_doi.return_value = by_doi or []
    client.fetch_works_by_id.return_value = by_id or {}
    client.fetch_work_by_title.return_value = by_title
    return client


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_own_papers_appear_as_own_nodes(monkeypatch):
    """A paper in the library should show up as a node of type 'own'."""
    monkeypatch.setattr(network_orchestrator.time, "sleep", lambda *a, **kw: None)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _seed_db(conn, [
        {"id": 1, "title": "Paper A", "doi": "10.1/a"},
        {"id": 2, "title": "Paper B", "doi": "10.1/b"},
    ])

    own_work = Work(
        id="W1", doi="10.1/a", title="Paper A", authors=["Doe, J."],
        year=2020, cited_by_count=5, referenced_works=[],
    )
    client = _mock_client(by_doi=[own_work])

    result = network_orchestrator.build_network(conn, client, depth=1)
    payload = result.payload

    own_nodes = [n for n in payload["nodes"] if n["type"] == "own"]
    assert len(own_nodes) >= 1
    own_ids = {n["paper_id"] for n in own_nodes}
    assert 1 in own_ids


def test_depth_1_excludes_level2_references(monkeypatch):
    """At depth=1 the response should not include W3 (a reference of a reference)."""
    monkeypatch.setattr(network_orchestrator.time, "sleep", lambda *a, **kw: None)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _seed_db(conn, [{"id": 1, "title": "Paper A", "doi": "10.1/a"}])

    own_work = Work(
        id="W1", doi="10.1/a", title="Paper A", authors=[], year=2020,
        cited_by_count=5, referenced_works=["W2"],
    )
    w2 = Work(
        id="W2", doi="10.1/b", title="Paper B", authors=[], year=2019,
        cited_by_count=2, referenced_works=["W3"],
    )
    client = _mock_client(by_doi=[own_work], by_id={"W2": w2})

    result = network_orchestrator.build_network(conn, client, depth=1)
    node_ids = {n["id"] for n in result.payload["nodes"]}

    # W2 is a direct reference and should be in the graph
    assert any("W2" in nid for nid in node_ids)
    # W3 is two levels deep and should NOT be present at depth=1
    assert not any(nid.endswith("W3") for nid in node_ids)


def test_stats_dict_has_required_keys(monkeypatch):
    """The 'stats' block must match the wire contract the frontend expects."""
    monkeypatch.setattr(network_orchestrator.time, "sleep", lambda *a, **kw: None)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _seed_db(conn, [{"id": 1, "title": "Paper A", "doi": "10.1/a"}])

    client = _mock_client(by_doi=[Work(
        id="W1", doi="10.1/a", title="Paper A", authors=[], year=2020,
        cited_by_count=5, referenced_works=[],
    )])

    result = network_orchestrator.build_network(conn, client, depth=2)
    stats = result.payload["stats"]
    expected_keys = {
        "own_papers", "papers_found_in_openalex", "total_references",
        "shared_references", "missing_sources", "pdf_reference_edges",
        "depth",
    }
    assert expected_keys <= set(stats.keys())
    assert stats["depth"] == 2


def test_paper_updates_collected_for_persistence(monkeypatch):
    """When OpenAlex returns metadata, the orchestrator should queue
    paper-update records for the endpoint to persist (it must not write to
    the DB itself)."""
    monkeypatch.setattr(network_orchestrator.time, "sleep", lambda *a, **kw: None)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _seed_db(conn, [{"id": 1, "title": "Paper A", "doi": "10.1/a"}])

    client = _mock_client(by_doi=[Work(
        id="W1", doi="10.1/a", title="Paper A", authors=[], year=2020,
        cited_by_count=42, referenced_works=[],
    )])

    result = network_orchestrator.build_network(conn, client, depth=1)

    # No DB write happened
    cur = conn.execute("SELECT openalex_id, cited_by_count FROM papers WHERE id=1")
    row = cur.fetchone()
    assert row["openalex_id"] == ""
    assert row["cited_by_count"] == 0

    # But the update is queued
    updates_for_p1 = [u for u in result.paper_updates if u.paper_id == 1]
    assert any(u.openalex_id == "W1" and u.cited_by_count == 42 for u in updates_for_p1)


def test_persist_updates_writes_to_db():
    """persist_updates should apply queued DOI + metadata changes to the DB."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _seed_db(conn, [{"id": 1, "title": "Paper A", "doi": "10.1/old"}])

    updates = [
        network_orchestrator._PaperUpdate(paper_id=1, new_doi="10.1/new"),
        network_orchestrator._PaperUpdate(paper_id=1, openalex_id="W1", cited_by_count=7),
    ]
    network_orchestrator.persist_updates(conn, updates)

    row = conn.execute(
        "SELECT doi, openalex_id, cited_by_count FROM papers WHERE id=1"
    ).fetchone()
    assert row["doi"] == "10.1/new"
    assert row["openalex_id"] == "W1"
    assert row["cited_by_count"] == 7
