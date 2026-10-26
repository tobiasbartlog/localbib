"""Library Diagnosis: the pure Findings, the router's fixes, and the two
refresh bugs it surfaced (an item without a PDF resolved to the ``all/``
folder itself; a missing source file failed once per category link)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from crossref import fetch_crossref_metadata
from doi_discovery import canonical_arxiv_doi, resolve_doi_from_arxiv
from filenames import create_symlinks, stored_pdf_path
from literature_manager import Config
from services import library_diagnosis as svc

PDF = b"%PDF-1.4 fake"


@pytest.fixture
def clean_all_dir():
    """``all/`` is shared by the whole suite; a stray file from another test
    would show up here as an orphan."""
    for f in Path(Config.ALL_DIR).glob("*.pdf"):
        f.unlink()
    yield
    for f in Path(Config.ALL_DIR).glob("*.pdf"):
        f.unlink()


def _seed(db, title="A Title", filename="", doi="", file_bytes=None) -> int:
    if file_bytes is not None:
        (Path(Config.ALL_DIR) / filename).write_bytes(file_bytes)
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title, authors, year, doi) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"h-{title}-{filename}", filename, filename, title, "Doe, J.", 2020, doi),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _row(db, pid):
    conn = db._connect()
    try:
        r = conn.execute("SELECT * FROM papers WHERE id = ?", (pid,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# services/library_diagnosis — pure
# ---------------------------------------------------------------------------

class TestDiagnose:
    def test_missing_file_is_an_error_with_orphans_as_best_first_candidates(self):
        papers = [{"id": 1, "title": "First RILEM Conference on Concrete", "filename": "gone.pdf", "doi": ""}]
        files = ["2021_Other_Unrelated_Topic.pdf", "2019_Wangler_First_RILEM_Conference.pdf"]
        findings = svc.diagnose(papers, files, [])
        missing = next(f for f in findings if f["kind"] == "missing_pdf")
        assert missing["severity"] == "error"
        assert missing["fixes"] == ["assign_file", "clear_pdf", "delete_item"]
        assert missing["candidates"][0] == "2019_Wangler_First_RILEM_Conference.pdf"
        assert missing["suggestion"] == "2019_Wangler_First_RILEM_Conference.pdf"

    def test_an_unrelated_orphan_is_offered_but_never_suggested(self):
        papers = [{"id": 1, "title": "First RILEM Conference on Concrete", "filename": "gone.pdf", "doi": ""}]
        findings = svc.diagnose(papers, ["2026_Cano_Autonomous_Navigation_Underground.pdf"], [])
        missing = next(f for f in findings if f["kind"] == "missing_pdf")
        orphan = next(f for f in findings if f["kind"] == "orphan_pdf")
        assert missing["candidates"] and missing["suggestion"] is None
        assert orphan["targets"] and orphan["suggestion"] is None

    def test_missing_file_without_orphans_offers_no_assign(self):
        findings = svc.diagnose([{"id": 1, "title": "T", "filename": "gone.pdf", "doi": ""}], [], [])
        assert findings[0]["fixes"] == ["clear_pdf", "delete_item"]
        assert "candidates" not in findings[0]

    def test_item_without_pdf_is_info_and_offers_oa_only_with_doi(self):
        findings = svc.diagnose([
            {"id": 1, "title": "With DOI", "filename": "", "doi": "10.1/x"},
            {"id": 2, "title": "No DOI", "filename": "", "doi": ""},
        ], [], [])
        by_id = {f["paper_id"]: f for f in findings}
        assert by_id[1]["severity"] == "info" and by_id[1]["fixes"] == ["fetch_oa"]
        assert by_id[2]["fixes"] == []

    def test_healthy_item_yields_nothing(self):
        assert svc.diagnose([{"id": 1, "title": "T", "filename": "a.pdf", "doi": "10.1/x"}], ["a.pdf"], []) == []

    def test_orphan_file_targets_items_lacking_a_pdf(self):
        papers = [
            {"id": 1, "title": "Healthy", "filename": "a.pdf", "doi": ""},
            {"id": 2, "title": "Duckweed Diversity", "filename": "", "doi": ""},
        ]
        findings = svc.diagnose(papers, ["a.pdf", "2022_Duckweed_Diversity.pdf"], [])
        orphan = next(f for f in findings if f["kind"] == "orphan_pdf")
        assert orphan["fixes"] == ["assign_file", "to_inbox"]
        assert [t["paper_id"] for t in orphan["targets"]] == [2]
        assert orphan["suggestion"] == 2

    def test_versioned_arxiv_doi(self):
        findings = svc.diagnose(
            [{"id": 1, "title": "T", "filename": "a.pdf", "doi": "10.48550/arXiv.1803.10122v4"}], ["a.pdf"], [])
        assert findings[0]["kind"] == "arxiv_doi_version"
        assert findings[0]["params"]["fixed"] == "10.48550/arXiv.1803.10122"

    def test_llm_probes_and_ordering(self):
        findings = svc.diagnose(
            [{"id": 1, "title": "T", "filename": "", "doi": ""}], [],
            [{"connection_id": "default", "label": "OpenAI", "tiers": ["reasoning", "fast"], "status": "auth"},
             {"connection_id": "local", "tiers": ["embedding"], "status": "unreachable"},
             {"connection_id": "ok", "tiers": ["fast"], "status": "ok"}])
        assert [f["kind"] for f in findings] == ["llm_auth", "llm_unreachable", "no_pdf"]
        assert findings[0]["params"] == {"connection": "OpenAI", "tiers": "reasoning, fast"}
        assert svc.counts(findings) == {"error": 1, "warning": 1, "info": 1}

    def test_llm_model_missing(self):
        findings = svc.diagnose([], [], [
            {"connection_id": "oai", "label": "OpenAI", "tiers": ["reasoning", "fast"],
             "status": "model_missing", "model": "gpt-4o"}])
        assert findings == [{
            "id": "llm_model_missing:oai:gpt-4o", "kind": "llm_model_missing", "severity": "error",
            "params": {"connection": "OpenAI", "tiers": "reasoning, fast", "model": "gpt-4o"},
            "fixes": ["open_llm_settings"],
        }]


# ---------------------------------------------------------------------------
# The refresh bugs
# ---------------------------------------------------------------------------

def test_stored_pdf_path_never_resolves_to_the_folder(clean_all_dir):
    assert stored_pdf_path("") is None
    assert stored_pdf_path("missing.pdf") is None
    (Path(Config.ALL_DIR) / "here.pdf").write_bytes(PDF)
    assert stored_pdf_path("here.pdf") == str(Path(Config.ALL_DIR) / "here.pdf")


def test_create_symlinks_skips_a_missing_source_quietly(db, clean_all_dir, caplog):
    pid = _seed(db, filename="gone.pdf")
    conn = db._connect()
    try:
        cid = conn.execute("INSERT INTO categories (name, description, keywords) VALUES ('Cat', '', '')").lastrowid
        conn.commit()
    finally:
        conn.close()
    db.assign_category(pid, cid, 0.9, "manual")
    create_symlinks(db, pid, "gone.pdf")
    assert "Symlink-Fehler" not in caplog.text
    assert not (Path(Config.CATEGORIES_DIR) / "Cat" / "gone.pdf").exists()


def test_arxiv_doi_has_no_version_and_skips_crossref():
    assert resolve_doi_from_arxiv("2501.03575v1") == "10.48550/arXiv.2501.03575"
    assert canonical_arxiv_doi("10.48550/arXiv.2501.03575v1") == "10.48550/arXiv.2501.03575"
    assert canonical_arxiv_doi("10.48550/arXiv.2501.03575") is None
    assert canonical_arxiv_doi("10.1000/v2") is None
    with patch("crossref.httpx.get") as get:
        assert fetch_crossref_metadata("10.48550/arXiv.2108.08090") is None
    get.assert_not_called()


# ---------------------------------------------------------------------------
# routers/diagnosis — snapshot + fixes
# ---------------------------------------------------------------------------

def test_get_diagnosis_reports_the_library(client, db, clean_all_dir):
    _seed(db, title="Fine", filename="fine.pdf", file_bytes=PDF)
    gone = _seed(db, title="Gone", filename="gone.pdf")
    resp = client.get("/api/diagnosis", params={"llm": "false"})
    assert resp.status_code == 200
    body = resp.json()
    # Neither item has a category: one extra Finding, with the number. And no
    # backup folder is set up in the suite: a gentle note about that, too.
    assert [f["id"] for f in body["findings"]] == [f"missing_pdf:{gone}", "uncategorized", "backup_none"]
    assert body["counts"]["error"] == 1


@respx.mock
def test_llm_probe_flags_a_rejected_key(client, db, clean_all_dir, monkeypatch):
    from tests.llm_helpers import configure_llm
    configure_llm(monkeypatch)
    respx.get("http://llm.test/v1/models").mock(return_value=httpx.Response(401))
    body = client.get("/api/diagnosis").json()
    assert any(f["kind"] == "llm_auth" for f in body["findings"])


@respx.mock
def test_llm_findings_come_from_the_startup_check(client, db, clean_all_dir, monkeypatch):
    """One path for two places (#209): the same model-list probe and the same
    classification as the Startup check — a gone model is a finding of its
    own, an unreachable second connection another, a model the list offers
    none, and a provider without a model list is no false alarm."""
    from tests.llm_helpers import configure_llm
    configure_llm(monkeypatch, reasoning="gpt-gone", fast="gpt-mini", embedding="nomic",
                  embed_base_url="http://localhost:11434/v1", embed_api_key="")
    respx.get("http://llm.test/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "gpt-mini"}]}))
    respx.get("http://localhost:11434/v1/models").mock(side_effect=httpx.ConnectError("refused"))
    body = client.get("/api/diagnosis").json()
    by_kind = {f["kind"]: f for f in body["findings"]}
    assert by_kind["llm_model_missing"]["id"] == "llm_model_missing:test:gpt-gone"
    assert by_kind["llm_model_missing"]["params"] == {"connection": "Test", "tiers": "reasoning", "model": "gpt-gone"}
    assert by_kind["llm_model_missing"]["severity"] == "error"
    assert by_kind["llm_model_missing"]["fixes"] == ["open_llm_settings"]
    assert by_kind["llm_unreachable"]["params"] == {"connection": "Embed", "tiers": "embedding"}
    assert "llm_auth" not in by_kind

    respx.get("http://llm.test/v1/models").mock(return_value=httpx.Response(404))
    respx.get("http://localhost:11434/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "nomic:latest"}]}))
    body = client.get("/api/diagnosis").json()
    assert not [f for f in body["findings"] if f["kind"].startswith("llm_")]


def test_fix_clear_pdf_and_refuses_an_item_that_has_its_file(client, db, clean_all_dir):
    gone = _seed(db, title="Gone", filename="gone.pdf")
    fine = _seed(db, title="Fine", filename="fine.pdf", file_bytes=PDF)
    assert client.post("/api/diagnosis/fix", json={"action": "clear_pdf", "paper_id": gone}).status_code == 200
    assert _row(db, gone)["filename"] == ""
    resp = client.post("/api/diagnosis/fix", json={"action": "clear_pdf", "paper_id": fine})
    assert resp.status_code == 409 and resp.json()["detail"] == "error.diagnosis.has_pdf"


def test_fix_delete_item_only_for_broken_entries(client, db, clean_all_dir):
    gone = _seed(db, title="Gone", filename="gone.pdf")
    fine = _seed(db, title="Fine", filename="fine.pdf", file_bytes=PDF)
    assert client.post("/api/diagnosis/fix", json={"action": "delete_item", "paper_id": gone}).status_code == 200
    assert _row(db, gone) is None
    assert client.post("/api/diagnosis/fix", json={"action": "delete_item", "paper_id": fine}).status_code == 409
    assert _row(db, fine) is not None


def test_fix_doi(client, db, clean_all_dir):
    pid = _seed(db, doi="10.48550/arXiv.1803.10122v4")
    resp = client.post("/api/diagnosis/fix", json={"action": "fix_doi", "paper_id": pid})
    assert resp.json()["doi"] == "10.48550/arXiv.1803.10122"
    assert _row(db, pid)["doi"] == "10.48550/arXiv.1803.10122"


def test_fix_assign_file_moves_the_orphan_onto_the_item(client, db, clean_all_dir):
    pid = _seed(db, title="Diversity of Duckweed", filename="old_name.pdf")
    orphan = Path(Config.ALL_DIR) / "renamed_by_hand.pdf"
    orphan.write_bytes(PDF)
    with patch("paper_ingest.index_paper_after_import", return_value={"chunks": 0}):
        resp = client.post("/api/diagnosis/fix",
                           json={"action": "assign_file", "paper_id": pid, "file": orphan.name})
    assert resp.status_code == 200, resp.text
    row = _row(db, pid)
    assert row["filename"] == resp.json()["filename"]
    assert (Path(Config.ALL_DIR) / row["filename"]).is_file()
    assert not orphan.exists()


def test_fix_assign_file_refuses_paths_and_taken_files(client, db, clean_all_dir):
    pid = _seed(db, title="T", filename="")
    _seed(db, title="Owner", filename="owned.pdf", file_bytes=PDF)
    bad = client.post("/api/diagnosis/fix", json={"action": "assign_file", "paper_id": pid, "file": "../x.pdf"})
    assert bad.json()["detail"] == "error.diagnosis.file_invalid"
    taken = client.post("/api/diagnosis/fix", json={"action": "assign_file", "paper_id": pid, "file": "owned.pdf"})
    assert taken.status_code == 409


def test_fix_to_inbox(client, db, clean_all_dir):
    orphan = Path(Config.ALL_DIR) / "stray.pdf"
    orphan.write_bytes(PDF)
    resp = client.post("/api/diagnosis/fix", json={"action": "to_inbox", "file": "stray.pdf"})
    assert resp.status_code == 200
    moved = Path(Config.INPUT_DIR) / "stray.pdf"
    assert moved.is_file() and not orphan.exists()
    moved.unlink()


def test_fix_fetch_oa_reports_when_no_open_access_copy(client, db, clean_all_dir):
    pid = _seed(db, doi="10.1/x")
    with patch("paper_ingest.fetch_openalex_works", return_value={}):
        resp = client.post("/api/diagnosis/fix", json={"action": "fetch_oa", "paper_id": pid})
    assert resp.status_code == 404 and resp.json()["detail"] == "error.diagnosis.no_oa_pdf"


def test_unknown_action(client):
    resp = client.post("/api/diagnosis/fix", json={"action": "nope"})
    assert resp.json()["detail"] == "error.diagnosis.unknown_action"

def test_uncategorized_is_one_finding_with_a_count(client, db, clean_all_dir):
    cat = db.add_category("Topic")
    sorted_in = _seed(db, title="Sorted", filename="s.pdf", file_bytes=PDF)
    _seed(db, title="Loose one", filename="l1.pdf", file_bytes=PDF)
    _seed(db, title="Loose two", filename="l2.pdf", file_bytes=PDF)
    db.assign_category(sorted_in, cat, 0.9, "manual")
    body = client.get("/api/diagnosis", params={"llm": "false"}).json()
    [finding] = [f for f in body["findings"] if f["kind"] == "uncategorized"]
    assert finding["params"] == {"count": 2}
    assert finding["severity"] == "info"
    assert finding["fixes"] == ["suggest_categories"]
    assert "paper_id" not in finding


def test_everything_categorised_means_no_uncategorized_finding():
    papers = [{"id": 1, "title": "T", "filename": "a.pdf", "doi": "", "categorized": True}]
    assert svc.diagnose(papers, ["a.pdf"], []) == []
    # A snapshot without the field (older callers) is not flagged either.
    assert svc.diagnose([{"id": 1, "title": "T", "filename": "a.pdf", "doi": ""}], ["a.pdf"], []) == []
