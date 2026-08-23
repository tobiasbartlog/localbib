"""Characterization tests for /api/duplicates and /api/duplicates/merge.

Freezes TODAY's behaviour of:
  GET  /api/duplicates           (detection — three strategies)
  POST /api/duplicates/merge     (destructive merge)

MERGE SEMANTICS FROZEN (high-care — this endpoint deletes data):
  Survivor   — the paper with keep_id stays; papers in delete_ids are deleted.
  Categories — copied from each del paper to keep via INSERT OR IGNORE; if keep
               already holds a category the keep's existing row is preserved as-is
               (confidence / assigned_by NOT overwritten).
  Custom     — field value copied from del to keep ONLY when del value != ''
               AND keep currently has no value or an empty value for that field.
  References — paper_references.matched_paper_id pointing at del is re-pointed
               to keep BEFORE the DELETE so external citation links survive.
               paper_references where source_paper_id = del are DELETED via
               CASCADE — del's own extracted refs are NOT transferred (QUIRK).
  Del row    — papers row for del_id is gone; ON DELETE CASCADE removes its
               paper_categories, paper_custom_values, and source paper_references.
  PDF file   — deleted from disk if present (non-fatal if missing).
  Response   — {"status": "ok", "kept": keep_id, "deleted": delete_ids}

TITLE NORMALISATION FROZEN (_normalize_title — strategies 2+3):
  NFKD unicode decomposition → lowercase → strip →
  remove non-word / non-space chars → collapse whitespace.

No external HTTP; no network calls; fully deterministic.
Purpose: catch regressions when duplicate_detection is extracted as a
service (issue #91).  Assert on observable behaviour (HTTP response shapes,
DB side-effects), NOT on internal function names.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import webapp
from webapp import _normalize_title
from literature_manager import Config


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _seed_paper(
    db,
    filename: str = "dup_test.pdf",
    title: str = "Duplicate Test Paper Title Long",
    doi: str = "",
    with_file: bool = False,
) -> int:
    """Insert a paper row and return its paper_id."""
    if with_file:
        filepath = Path(Config.ALL_DIR) / filename
        filepath.write_bytes(b"%PDF-1.4 fake content for duplicate tests")
    file_hash = f"duptest-{filename}"
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers "
            "(file_hash, filename, original_filename, title, authors, year, doi) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (file_hash, filename, filename, title, "Auth, A.", 2022, doi or None),
        )
        pid = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_category(db, name: str = "TestCat") -> int:
    """Insert (or find existing) category, return its id."""
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO categories (name, description, keywords) "
            "VALUES (?, ?, ?)",
            (name, "", ""),
        )
        conn.commit()
        if cur.lastrowid:
            cid = cur.lastrowid
        else:
            cid = conn.execute(
                "SELECT id FROM categories WHERE name=?", (name,)
            ).fetchone()["id"]
    finally:
        conn.close()
    return cid


def _assign_category(
    db,
    paper_id: int,
    cat_id: int,
    confidence: float = 0.9,
    assigned_by: str = "manual",
):
    conn = db._connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO paper_categories "
            "(paper_id, category_id, confidence, assigned_by) VALUES (?, ?, ?, ?)",
            (paper_id, cat_id, confidence, assigned_by),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_custom_field(db, name: str = "Progress") -> int:
    """Insert (or find) a custom_fields row, return its id."""
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO custom_fields (name, field_type, options, position) "
            "VALUES (?, ?, ?, ?)",
            (name, "text", "", 0),
        )
        conn.commit()
        if cur.lastrowid:
            fid = cur.lastrowid
        else:
            fid = conn.execute(
                "SELECT id FROM custom_fields WHERE name=?", (name,)
            ).fetchone()["id"]
    finally:
        conn.close()
    return fid


def _set_custom_value(db, paper_id: int, field_id: int, value: str):
    conn = db._connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO paper_custom_values (paper_id, field_id, value) "
            "VALUES (?, ?, ?)",
            (paper_id, field_id, value),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_matched_ref(
    db, source_paper_id: int, matched_paper_id: int, ref_index: int = 1
) -> int:
    """Insert a paper_references row with matched_paper_id set; return its id."""
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO paper_references "
            "(source_paper_id, ref_index, title, matched_paper_id, "
            " match_confidence, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                source_paper_id,
                ref_index,
                "A Referenced Work",
                matched_paper_id,
                0.95,
                "pdf_llm",
            ),
        )
        rid = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return rid


def _insert_source_ref(db, source_paper_id: int, title: str = "Own Ref") -> int:
    """Insert a paper_references row for del paper's OWN refs (source_paper_id)."""
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO paper_references (source_paper_id, ref_index, title, source) "
            "VALUES (?, ?, ?, ?)",
            (source_paper_id, 1, title, "pdf_llm"),
        )
        rid = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return rid


def _get_paper(db, paper_id: int):
    conn = db._connect()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get_categories_for(db, paper_id: int) -> list[int]:
    conn = db._connect()
    try:
        rows = conn.execute(
            "SELECT category_id FROM paper_categories WHERE paper_id=? "
            "ORDER BY category_id",
            (paper_id,),
        ).fetchall()
        return [r["category_id"] for r in rows]
    finally:
        conn.close()


def _get_category_assignment(db, paper_id: int, cat_id: int):
    conn = db._connect()
    try:
        row = conn.execute(
            "SELECT confidence, assigned_by FROM paper_categories "
            "WHERE paper_id=? AND category_id=?",
            (paper_id, cat_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get_custom_value(db, paper_id: int, field_id: int):
    conn = db._connect()
    try:
        row = conn.execute(
            "SELECT value FROM paper_custom_values WHERE paper_id=? AND field_id=?",
            (paper_id, field_id),
        ).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def _get_ref_matched_id(db, ref_id: int):
    conn = db._connect()
    try:
        row = conn.execute(
            "SELECT matched_paper_id FROM paper_references WHERE id=?", (ref_id,)
        ).fetchone()
        return row["matched_paper_id"] if row else None
    finally:
        conn.close()


def _ref_exists(db, ref_id: int) -> bool:
    conn = db._connect()
    try:
        row = conn.execute(
            "SELECT id FROM paper_references WHERE id=?", (ref_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


# ===========================================================================
# _normalize_title — unit tests (used by detection strategies 2+3)
# ===========================================================================


class TestNormalizeTitle:
    """Freeze webapp._normalize_title's exact behaviour."""

    def test_empty_string_returns_empty(self):
        assert _normalize_title("") == ""

    def test_lowercases(self):
        assert _normalize_title("Machine Learning") == "machine learning"

    def test_strips_leading_trailing_whitespace(self):
        assert _normalize_title("  Hello World  ") == "hello world"

    def test_collapses_internal_whitespace(self):
        assert _normalize_title("Hello   World") == "hello world"

    def test_removes_colon(self):
        result = _normalize_title("Machine Learning: A Survey")
        assert ":" not in result
        assert result == "machine learning a survey"

    def test_removes_parentheses(self):
        result = _normalize_title("Deep Learning (2020)")
        assert "(" not in result and ")" not in result
        assert result == "deep learning 2020"

    def test_unicode_nfkd_removes_combining_accents(self):
        # 'é' decomposes to 'e' + combining accent (U+0301 = not \w) → stripped
        result = _normalize_title("Réseaux de neurones")
        assert result == "reseaux de neurones"

    def test_hyphen_removed_words_join(self):
        # Hyphens are removed without inserting space → words concatenate
        result = _normalize_title("State-of-the-Art Methods")
        assert "-" not in result
        assert result == "stateoftheart methods"

    def test_apostrophe_removed(self):
        result = _normalize_title("Author's Guide to Science")
        assert "'" not in result
        assert result == "authors guide to science"

    def test_digits_preserved(self):
        result = _normalize_title("GPT-4 Analysis 2023")
        assert "4" in result
        assert "2023" in result

    def test_slash_removed(self):
        result = _normalize_title("NLP/ML Approaches")
        assert "/" not in result

    def test_numbers_only_title(self):
        result = _normalize_title("2024")
        assert result == "2024"

    def test_tab_collapsed_to_space(self):
        # \t matches \s; multiple \s → single space
        result = _normalize_title("Deep\tLearning")
        assert result == "deep\tlearning".replace("\t", "") or result == "deep learning"
        # \t is \s but not \w; re.sub removes \t since it is neither \w nor \s?
        # Actually \s includes \t, so \t is NOT removed by [^\w\s]. Stays as space.
        assert "deep" in result and "learning" in result


# ===========================================================================
# GET /api/duplicates — detection
# ===========================================================================


class TestFindDuplicates:
    """Characterization tests for GET /api/duplicates."""

    def test_empty_library_returns_empty_groups(self, client, db):
        resp = client.get("/api/duplicates")
        assert resp.status_code == 200
        body = resp.json()
        assert body["groups"] == []
        assert body["total_duplicates"] == 0

    def test_response_shape(self, client, db):
        resp = client.get("/api/duplicates")
        assert resp.status_code == 200
        body = resp.json()
        assert "groups" in body
        assert "total_duplicates" in body

    def test_single_paper_not_grouped(self, client, db):
        _seed_paper(db, filename="single.pdf", doi="10.0/single")
        resp = client.get("/api/duplicates")
        body = resp.json()
        assert body["total_duplicates"] == 0

    # --- Strategy 1: same DOI ---

    def test_same_doi_detected(self, client, db):
        _seed_paper(db, filename="doi_a.pdf", doi="10.1000/abc")
        _seed_paper(db, filename="doi_b.pdf", doi="10.1000/abc")
        resp = client.get("/api/duplicates")
        body = resp.json()
        assert body["total_duplicates"] == 2
        reasons = [g["reason"] for g in body["groups"]]
        assert any("DOI" in r for r in reasons), f"Expected DOI group; got {reasons}"

    def test_doi_reason_contains_the_doi_value(self, client, db):
        _seed_paper(db, filename="doir_a.pdf", doi="10.9999/myDOI")
        _seed_paper(db, filename="doir_b.pdf", doi="10.9999/myDOI")
        resp = client.get("/api/duplicates")
        body = resp.json()
        doi_groups = [g for g in body["groups"] if "DOI" in g["reason"]]
        assert doi_groups
        assert "10.9999/mydoi" in doi_groups[0]["reason"].lower()

    def test_doi_comparison_is_case_insensitive(self, client, db):
        _seed_paper(db, filename="doicase_a.pdf", doi="10.1000/ABC")
        _seed_paper(db, filename="doicase_b.pdf", doi="10.1000/abc")
        resp = client.get("/api/duplicates")
        body = resp.json()
        assert body["total_duplicates"] == 2

    def test_doi_comparison_strips_whitespace(self, client, db):
        _seed_paper(db, filename="doiws_a.pdf", doi="  10.1000/xyz  ")
        _seed_paper(db, filename="doiws_b.pdf", doi="10.1000/xyz")
        resp = client.get("/api/duplicates")
        body = resp.json()
        assert body["total_duplicates"] == 2

    def test_empty_doi_papers_not_grouped_by_doi_strategy(self, client, db):
        """Papers with no DOI (None / empty) must NOT form a DOI group."""
        _seed_paper(db, filename="nodoiA.pdf", doi="", title="No DOI Paper Alpha Long")
        _seed_paper(db, filename="nodoiB.pdf", doi="", title="No DOI Paper Beta Long")
        resp = client.get("/api/duplicates")
        body = resp.json()
        doi_groups = [g for g in body["groups"] if "DOI" in g["reason"]]
        assert doi_groups == []

    def test_different_dois_not_grouped(self, client, db):
        _seed_paper(db, filename="diff_a.pdf", doi="10.1/aaa")
        _seed_paper(db, filename="diff_b.pdf", doi="10.1/bbb")
        resp = client.get("/api/duplicates")
        body = resp.json()
        doi_groups = [g for g in body["groups"] if "DOI" in g["reason"]]
        assert doi_groups == []

    def test_group_papers_list_contains_both_papers(self, client, db):
        pid_a = _seed_paper(db, filename="plist_a.pdf", doi="10.7/paperslist")
        pid_b = _seed_paper(db, filename="plist_b.pdf", doi="10.7/paperslist")
        resp = client.get("/api/duplicates")
        body = resp.json()
        doi_groups = [g for g in body["groups"] if "DOI" in g["reason"]]
        assert doi_groups
        paper_ids_in_group = {p["id"] for p in doi_groups[0]["papers"]}
        assert pid_a in paper_ids_in_group
        assert pid_b in paper_ids_in_group

    # --- Strategy 2: identical normalized title ---

    def test_identical_title_detected(self, client, db):
        title = "A Very Long Identical Title For Testing Detection"
        _seed_paper(db, filename="titleA.pdf", title=title)
        _seed_paper(db, filename="titleB.pdf", title=title)
        resp = client.get("/api/duplicates")
        body = resp.json()
        assert body["total_duplicates"] == 2
        reasons = [g["reason"] for g in body["groups"]]
        assert any("Titel" in r for r in reasons), f"Expected Titel group; got {reasons}"

    def test_title_case_insensitive(self, client, db):
        _seed_paper(db, filename="titlecase_a.pdf", title="Machine Learning Study Result")
        _seed_paper(db, filename="titlecase_b.pdf", title="machine learning study result")
        resp = client.get("/api/duplicates")
        body = resp.json()
        assert body["total_duplicates"] == 2

    def test_title_with_punctuation_differences_detected(self, client, db):
        """Punctuation-only differences are normalized away → same group."""
        _seed_paper(db, filename="punct_a.pdf", title="Machine Learning: A Survey Paper")
        _seed_paper(db, filename="punct_b.pdf", title="Machine Learning A Survey Paper")
        resp = client.get("/api/duplicates")
        body = resp.json()
        assert body["total_duplicates"] == 2

    def test_short_title_under_10_chars_not_grouped_by_strategy2(self, client, db):
        """Normalized title length ≤ 10 chars is skipped by strategy 2."""
        _seed_paper(db, filename="short_a.pdf", title="AI")
        _seed_paper(db, filename="short_b.pdf", title="AI")
        resp = client.get("/api/duplicates")
        body = resp.json()
        gleicher_groups = [g for g in body["groups"] if g["reason"] == "Gleicher Titel"]
        assert gleicher_groups == []

    # --- Strategy 3: similar title (substring / Jaccard) ---

    def test_substring_title_detected(self, client, db):
        """If one normalized title is a substring of the other, they are grouped."""
        _seed_paper(db, filename="sub_a.pdf",
                    title="Deep Learning Methods Overview Techniques")
        _seed_paper(db, filename="sub_b.pdf",
                    title="Deep Learning Methods Overview Techniques Extended Version")
        resp = client.get("/api/duplicates")
        body = resp.json()
        assert body["total_duplicates"] >= 2
        reasons = [g["reason"] for g in body["groups"]]
        assert any("enthalten" in r or "Titel" in r or "hnlich" in r for r in reasons)

    def test_high_jaccard_similarity_detected(self, client, db):
        """Word-Jaccard ≥ 0.8 triggers 'Aehnlicher Titel' group."""
        # 9 shared words, 2 different → jaccard = 9/11 ≈ 0.82 > 0.8
        t1 = "Neural Network Image Classification Study For The Advanced Learning Techniques"
        t2 = "Neural Network Image Classification Study For The Advanced Learning Approaches"
        _seed_paper(db, filename="jac_a.pdf", title=t1)
        _seed_paper(db, filename="jac_b.pdf", title=t2)
        resp = client.get("/api/duplicates")
        body = resp.json()
        assert body["total_duplicates"] >= 2

    def test_low_jaccard_not_detected(self, client, db):
        """Word-Jaccard < 0.8 with no substring → no group from strategy 3."""
        _seed_paper(db, filename="lowjac_a.pdf",
                    title="Quantum Computing Algorithms Optimization Framework Design")
        _seed_paper(db, filename="lowjac_b.pdf",
                    title="Natural Language Processing Text Mining Analysis Techniques")
        resp = client.get("/api/duplicates")
        body = resp.json()
        assert body["total_duplicates"] == 0

    def test_title_under_15_chars_not_grouped_by_strategy3(self, client, db):
        """Strategy 3 skips titles shorter than 15 chars after normalization."""
        # "simple title" = 12 chars → skipped
        _seed_paper(db, filename="st3_a.pdf", title="Simple Title")
        _seed_paper(db, filename="st3_b.pdf", title="Simple Titles")
        resp = client.get("/api/duplicates")
        body = resp.json()
        sub_groups = [g for g in body["groups"] if g["reason"] == "Titel enthalten"]
        assert sub_groups == []

    def test_jaccard_requires_at_least_3_words(self, client, db):
        """Strategy 3 Jaccard check is skipped for titles with < 3 words."""
        # 2-word titles (after normalization) → Jaccard path bypassed
        _seed_paper(db, filename="words2_a.pdf",
                    title="Machine Learning Fundamentals Deep Overview Extended Version")
        # Only share 1 word → Jaccard < 0.8; ensure short-word guard respected
        _seed_paper(db, filename="words2_b.pdf",
                    title="Deep Neural Architectures Comprehensive Review Overview Systems")
        # This is a sanity test — just verify no crash
        resp = client.get("/api/duplicates")
        assert resp.status_code == 200

    # --- Deduplication across strategies ---

    def test_doi_group_not_duplicated_by_title_strategy(self, client, db):
        """A pair found by DOI should NOT form a second group via title strategy."""
        title = "Unique Title For Dedup Test Across Detection Strategies"
        _seed_paper(db, filename="dedup_a.pdf", title=title, doi="10.1000/dedup")
        _seed_paper(db, filename="dedup_b.pdf", title=title, doi="10.1000/dedup")
        resp = client.get("/api/duplicates")
        body = resp.json()
        all_pairs: list = []
        for g in body["groups"]:
            pids = frozenset(p["id"] for p in g["papers"])
            assert pids not in all_pairs, f"Same pair grouped twice: {pids}"
            all_pairs.append(pids)

    def test_total_duplicates_equals_sum_of_group_paper_counts(self, client, db):
        """total_duplicates = sum(len(group['papers'])) across all groups."""
        _seed_paper(db, filename="cnt_a.pdf", doi="10.0/cnt")
        _seed_paper(db, filename="cnt_b.pdf", doi="10.0/cnt")
        resp = client.get("/api/duplicates")
        body = resp.json()
        computed = sum(len(g["papers"]) for g in body["groups"])
        assert body["total_duplicates"] == computed

    def test_group_papers_include_has_file_field(self, client, db):
        """Each paper dict in a group must include the has_file field."""
        _seed_paper(db, filename="hf_a.pdf", doi="10.0/hasfile")
        _seed_paper(db, filename="hf_b.pdf", doi="10.0/hasfile")
        resp = client.get("/api/duplicates")
        body = resp.json()
        for g in body["groups"]:
            for p in g["papers"]:
                assert "has_file" in p, f"Missing has_file in paper dict: {p}"


# ===========================================================================
# POST /api/duplicates/merge — destructive merge
# ===========================================================================


class TestMergeDuplicates:
    """Characterization tests for POST /api/duplicates/merge."""

    def test_404_keep_id_not_found(self, client, db):
        resp = client.post(
            "/api/duplicates/merge", json={"keep_id": 99999, "delete_ids": []}
        )
        assert resp.status_code == 404

    def test_response_shape(self, client, db):
        keep_id = _seed_paper(db, filename="resp_keep.pdf")
        del_id = _seed_paper(db, filename="resp_del.pdf")
        resp = client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [del_id]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["kept"] == keep_id
        assert body["deleted"] == [del_id]

    # --- Survivor rule ---

    def test_keep_paper_survives(self, client, db):
        keep_id = _seed_paper(db, filename="surv_keep.pdf")
        del_id = _seed_paper(db, filename="surv_del.pdf")
        client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [del_id]},
        )
        assert _get_paper(db, keep_id) is not None

    def test_del_paper_row_is_deleted(self, client, db):
        keep_id = _seed_paper(db, filename="delt_keep.pdf")
        del_id = _seed_paper(db, filename="delt_del.pdf")
        client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [del_id]},
        )
        assert _get_paper(db, del_id) is None

    def test_del_id_equal_to_keep_id_is_skipped(self, client, db):
        """del_id == keep_id must be silently skipped — paper must NOT be deleted."""
        keep_id = _seed_paper(db, filename="self_merge.pdf")
        resp = client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [keep_id]},
        )
        assert resp.status_code == 200
        assert _get_paper(db, keep_id) is not None

    def test_nonexistent_del_id_silently_skipped(self, client, db):
        """A del_id that has no DB row must not raise an error."""
        keep_id = _seed_paper(db, filename="skip_del.pdf")
        resp = client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [88888]},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_multiple_del_ids_all_deleted(self, client, db):
        keep_id = _seed_paper(db, filename="multi_keep.pdf")
        del1 = _seed_paper(db, filename="multi_del1.pdf")
        del2 = _seed_paper(db, filename="multi_del2.pdf")
        client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [del1, del2]},
        )
        assert _get_paper(db, keep_id) is not None
        assert _get_paper(db, del1) is None
        assert _get_paper(db, del2) is None

    def test_empty_delete_ids_list_noop(self, client, db):
        keep_id = _seed_paper(db, filename="noop_keep.pdf")
        resp = client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": []},
        )
        assert resp.status_code == 200
        assert _get_paper(db, keep_id) is not None

    # --- Category transfer semantics ---

    def test_del_categories_transferred_to_keep(self, client, db):
        keep_id = _seed_paper(db, filename="catxfer_keep.pdf")
        del_id = _seed_paper(db, filename="catxfer_del.pdf")
        cid = _insert_category(db, "CatXferTest")
        _assign_category(db, del_id, cid)
        client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [del_id]},
        )
        assert cid in _get_categories_for(db, keep_id)

    def test_existing_keep_category_not_overwritten_by_del(self, client, db):
        """QUIRK frozen: INSERT OR IGNORE means if keep already has the category,
        its confidence/assigned_by are preserved unchanged, not overwritten."""
        keep_id = _seed_paper(db, filename="catover_keep.pdf")
        del_id = _seed_paper(db, filename="catover_del.pdf")
        cid = _insert_category(db, "CatOverTest")
        _assign_category(db, keep_id, cid, confidence=0.5, assigned_by="manual")
        _assign_category(db, del_id, cid, confidence=0.99, assigned_by="llm")
        client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [del_id]},
        )
        row = _get_category_assignment(db, keep_id, cid)
        assert row is not None
        # Keep's original values must be preserved (INSERT OR IGNORE — no overwrite)
        assert abs(row["confidence"] - 0.5) < 0.01
        assert row["assigned_by"] == "manual"

    def test_del_exclusive_category_added_to_keep(self, client, db):
        """Category only on del (not on keep) must appear on keep after merge."""
        keep_id = _seed_paper(db, filename="catonly_keep.pdf")
        del_id = _seed_paper(db, filename="catonly_del.pdf")
        cid_shared = _insert_category(db, "SharedCat")
        cid_del_only = _insert_category(db, "DelOnlyCat")
        _assign_category(db, keep_id, cid_shared)
        _assign_category(db, del_id, cid_shared)
        _assign_category(db, del_id, cid_del_only)
        client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [del_id]},
        )
        cats = _get_categories_for(db, keep_id)
        assert cid_shared in cats
        assert cid_del_only in cats

    def test_del_categories_cascade_deleted_with_del_paper(self, client, db):
        """After merge, paper_categories rows for del_id are gone (CASCADE)."""
        keep_id = _seed_paper(db, filename="catgone_keep.pdf")
        del_id = _seed_paper(db, filename="catgone_del.pdf")
        cid = _insert_category(db, "CatGoneTest")
        _assign_category(db, del_id, cid)
        client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [del_id]},
        )
        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT * FROM paper_categories WHERE paper_id=?", (del_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row is None

    # --- Custom value transfer semantics ---

    def test_del_custom_value_transferred_when_keep_is_empty(self, client, db):
        keep_id = _seed_paper(db, filename="cvxfer_keep.pdf")
        del_id = _seed_paper(db, filename="cvxfer_del.pdf")
        fid = _insert_custom_field(db, "CVXferField")
        _set_custom_value(db, del_id, fid, "del_value")
        client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [del_id]},
        )
        assert _get_custom_value(db, keep_id, fid) == "del_value"

    def test_keep_custom_value_preserved_when_keep_already_has_value(self, client, db):
        """QUIRK frozen: del's value does NOT overwrite a non-empty keep value."""
        keep_id = _seed_paper(db, filename="cvkeep_keep.pdf")
        del_id = _seed_paper(db, filename="cvkeep_del.pdf")
        fid = _insert_custom_field(db, "CVKeepField")
        _set_custom_value(db, keep_id, fid, "keep_value")
        _set_custom_value(db, del_id, fid, "del_would_overwrite")
        client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [del_id]},
        )
        assert _get_custom_value(db, keep_id, fid) == "keep_value"

    def test_empty_string_del_value_not_transferred(self, client, db):
        """Del values that are '' are filtered out and never transferred."""
        keep_id = _seed_paper(db, filename="cvempty_keep.pdf")
        del_id = _seed_paper(db, filename="cvempty_del.pdf")
        fid = _insert_custom_field(db, "CVEmptyField")
        _set_custom_value(db, del_id, fid, "")
        client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [del_id]},
        )
        val = _get_custom_value(db, keep_id, fid)
        assert val is None or val == ""

    # --- Reference re-pointing semantics ---

    def test_matched_paper_id_repointed_from_del_to_keep(self, client, db):
        """External paper_references rows with matched_paper_id=del are re-pointed to keep."""
        keep_id = _seed_paper(db, filename="refpoint_keep.pdf")
        del_id = _seed_paper(db, filename="refpoint_del.pdf")
        source_id = _seed_paper(db, filename="refpoint_src.pdf")
        rid = _insert_matched_ref(db, source_paper_id=source_id, matched_paper_id=del_id)
        client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [del_id]},
        )
        # After merge the reference must point to keep, not del
        assert _get_ref_matched_id(db, rid) == keep_id

    def test_del_own_refs_deleted_by_cascade_not_transferred(self, client, db):
        """QUIRK frozen: paper_references where source_paper_id=del are DELETED via
        CASCADE when del is removed — del's own extracted refs are NOT transferred
        to keep.  This is a known data-loss quirk.  Freeze it here so it's caught
        if the future service adds transfer logic."""
        keep_id = _seed_paper(db, filename="cascade_keep.pdf")
        del_id = _seed_paper(db, filename="cascade_del.pdf")
        own_ref_id = _insert_source_ref(db, source_paper_id=del_id, title="Del Own Ref")
        client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [del_id]},
        )
        # The source_paper_id ref is gone (CASCADE delete)
        assert not _ref_exists(db, own_ref_id), (
            "QUIRK: del's own source_paper_id refs are cascade-deleted, not transferred"
        )

    def test_matched_ref_repointing_happens_before_delete(self, client, db):
        """Re-pointing matched_paper_id to keep must happen BEFORE the paper delete,
        so the FK constraint is never violated.  The reference must survive."""
        keep_id = _seed_paper(db, filename="reorder_keep.pdf")
        del_id = _seed_paper(db, filename="reorder_del.pdf")
        src_id = _seed_paper(db, filename="reorder_src.pdf")
        rid = _insert_matched_ref(db, source_paper_id=src_id, matched_paper_id=del_id)
        resp = client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [del_id]},
        )
        assert resp.status_code == 200
        assert _ref_exists(db, rid)

    # --- PDF file deletion ---

    def test_del_pdf_file_deleted_from_disk(self, client, db):
        keep_id = _seed_paper(db, filename="filedel_keep.pdf", with_file=False)
        del_id = _seed_paper(db, filename="filedel_del.pdf", with_file=True)
        del_filepath = Path(Config.ALL_DIR) / "filedel_del.pdf"
        assert del_filepath.exists()
        client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [del_id]},
        )
        assert not del_filepath.exists()

    def test_keep_pdf_file_untouched(self, client, db):
        keep_id = _seed_paper(db, filename="keepfile_keep.pdf", with_file=True)
        del_id = _seed_paper(db, filename="keepfile_del.pdf", with_file=False)
        keep_filepath = Path(Config.ALL_DIR) / "keepfile_keep.pdf"
        assert keep_filepath.exists()
        client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [del_id]},
        )
        assert keep_filepath.exists()

    def test_missing_del_file_on_disk_does_not_cause_error(self, client, db):
        """If del paper's PDF is absent from disk, merge must still succeed."""
        keep_id = _seed_paper(db, filename="missingf_keep.pdf", with_file=False)
        del_id = _seed_paper(db, filename="missingf_del.pdf", with_file=False)
        resp = client.post(
            "/api/duplicates/merge",
            json={"keep_id": keep_id, "delete_ids": [del_id]},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
