"""Issue #52 / ADR-0004: core Projects feature removed.

The former /api/projects CRUD and paper-project assignment endpoints are gone;
the paper list filters via a generic ``cite_keys`` query param instead. The SPA
feeds it with the refs of a plugin-side research project (the core stays
plugin-agnostic); the filter is only offered while the plugin is enabled.
The projects/paper_projects tables stay in the DB as dead data.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import webapp


def _insert_paper(db, cite_key: str, title: str = "Paper", year: int = 2024,
                  authors: str = "Doe, John") -> int:
    conn = db._connect()
    try:
        cur = conn.execute(
            """INSERT INTO papers (file_hash, filename, original_filename,
               title, authors, year, cite_key)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (f"hash-{cite_key or title}", f"{cite_key or title}.pdf",
             f"{cite_key or title}.pdf", title, authors, year, cite_key),
        )
        paper_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return paper_id


# --- core project endpoints are gone -----------------------------------------

@pytest.mark.parametrize("method,path", [
    ("GET", "/api/projects"),
    ("POST", "/api/projects"),
    ("PUT", "/api/projects/1"),
    ("DELETE", "/api/projects/1"),
    ("POST", "/api/papers/1/projects/1"),
    ("DELETE", "/api/papers/1/projects/1"),
    ("POST", "/api/papers/bulk-assign-project"),
    ("POST", "/api/papers/bulk-remove-project"),
])
def test_core_project_endpoints_removed(client, method, path):
    resp = client.request(method, path, json={})
    # 404 when no route matches at all, 405 when only another method's route
    # still shares the path pattern (e.g. GET /api/papers/{id}).
    assert resp.status_code in (404, 405)


def test_stats_no_longer_reports_project_count(client, db):
    stats = client.get("/api/stats").json()
    assert "project_count" not in stats


def test_paper_payload_carries_no_core_projects(client, db):
    pid = _insert_paper(db, "Doe2024")
    listed = client.get("/api/papers").json()
    assert "projects" not in listed[0]
    detail = client.get(f"/api/papers/{pid}").json()
    assert "projects" not in detail


# --- cite_keys filter (backs the plugin-side project filter) -----------------

def test_papers_filter_by_cite_keys(client, db):
    _insert_paper(db, "Smith2020", title="A")
    _insert_paper(db, "Doe2021", title="B")
    _insert_paper(db, "Roe2022", title="C")

    resp = client.get("/api/papers", params={"cite_keys": "Smith2020,Roe2022"})
    assert resp.status_code == 200
    assert sorted(p["cite_key"] for p in resp.json()) == ["Roe2022", "Smith2020"]


def test_cite_keys_tolerates_whitespace_and_unknown_keys(client, db):
    _insert_paper(db, "Smith2020")
    resp = client.get(
        "/api/papers", params={"cite_keys": " Smith2020 , DanglingRef1999 "}
    )
    # Hanging refs match nothing — they only warn in the plugin UI, never break.
    assert [p["cite_key"] for p in resp.json()] == ["Smith2020"]


def test_empty_cite_keys_returns_no_papers(client, db):
    # A research project without refs filters down to an empty list.
    _insert_paper(db, "Smith2020")
    resp = client.get("/api/papers", params={"cite_keys": ""})
    assert resp.json() == []


def test_without_cite_keys_param_all_papers_return(client, db):
    _insert_paper(db, "Smith2020")
    _insert_paper(db, "Doe2021")
    assert len(client.get("/api/papers").json()) == 2


def test_cite_keys_uses_legacy_fallback_for_unbackfilled_rows(client, db):
    # Rows from before the cite-key backfill have cite_key='' — the filter
    # falls back to the legacy authors+year scheme.
    _insert_paper(db, "", title="Legacy", authors="Miller, Jane", year=2019)
    resp = client.get("/api/papers", params={"cite_keys": "Miller2019"})
    assert [p["title"] for p in resp.json()] == ["Legacy"]


def test_cite_keys_combines_with_category_filter(client, db):
    pid = _insert_paper(db, "Smith2020", title="In both")
    _insert_paper(db, "Doe2021", title="Wrong key")
    conn = db._connect()
    try:
        cur = conn.execute("INSERT INTO categories (name) VALUES ('Kat')")
        cat_id = cur.lastrowid
        conn.execute(
            "INSERT INTO paper_categories (paper_id, category_id) VALUES (?, ?)",
            (pid, cat_id),
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get(
        "/api/papers",
        params={"category_id": cat_id, "cite_keys": "Smith2020,Doe2021"},
    )
    assert [p["title"] for p in resp.json()] == ["In both"]


