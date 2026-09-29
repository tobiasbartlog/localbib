"""Research Chat extra_context tests (issue #64).

Verifies three things:
1. Without extra_context the endpoint behaves identically to before (no
   'WEITERER KONTEXT' block in the assembled LLM prompt).
2. With extra_context the chapter text appears in the assembled LLM prompt
   (the SPA-injected Diss Context block is forwarded to the LLM).
3. The core (webapp.py) statically imports NO plugin package anywhere in the
   file — plugin-freedom is a hard constraint (ADR-0005 / P3).
"""
from __future__ import annotations

import ast
import pathlib
from unittest.mock import MagicMock, patch

import pytest

import webapp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_chunk(db, paper_id: int, text: str = "machine learning methods for materials science") -> None:
    """Insert one text chunk for paper_id so the endpoint finds content."""
    conn = db._connect()
    try:
        conn.execute(
            """INSERT INTO paper_chunks
               (paper_id, chunk_index, page_start, page_end, chunk_text, token_count)
               VALUES (?, 0, 1, 1, ?, ?)""",
            (paper_id, text, len(text.split())),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test 1: without extra_context — behavior unchanged
# ---------------------------------------------------------------------------

def test_ask_without_extra_context(client, seed_paper, db):
    """POST /api/research-chat/ask without extra_context sends NO extra block
    to the LLM.  Behavior is identical to the pre-#64 baseline.
    """
    # Chunk must share terms with the question so BM25 returns it.
    _seed_chunk(db, seed_paper, text="machine learning methods for materials science")

    captured: list[dict] = []

    def fake_complete(messages, **kw):
        captured.extend(messages)
        return "Antwort ohne Diss-Kontext"

    with patch.object(webapp.LLMClient, "complete", side_effect=fake_complete):
        resp = client.post(
            "/api/research-chat/ask",
            json={
                "question": "What machine learning methods are used?",
                "paper_ids": [seed_paper],
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "Antwort ohne Diss-Kontext"

    # The assembled user message must NOT contain the extra-context section.
    user_msg = next(
        (m["content"] for m in captured if m.get("role") == "user"), ""
    )
    assert "WEITERER KONTEXT" not in user_msg


# ---------------------------------------------------------------------------
# Test 2: with extra_context — chapter text appears in the LLM prompt
# ---------------------------------------------------------------------------

def test_ask_with_extra_context_included_in_prompt(client, seed_paper, db):
    """POST /api/research-chat/ask with extra_context injects the block into
    the user-turn before sending to the LLM.
    """
    # Chunk must share terms with the question so BM25 returns it.
    _seed_chunk(db, seed_paper, text="machine learning methods for materials science")

    captured: list[dict] = []

    def fake_complete(messages, **kw):
        captured.extend(messages)
        return "Kapitel 1 passt gut zu diesem Paper."

    chapter_block = (
        "### Kapitel 1: Einleitung\n"
        "Dieses Kapitel beschreibt die Motivation der Dissertation."
    )

    with patch.object(webapp.LLMClient, "complete", side_effect=fake_complete):
        resp = client.post(
            "/api/research-chat/ask",
            json={
                "question": "What machine learning methods are used?",
                "paper_ids": [seed_paper],
                "extra_context": [chapter_block],
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "Kapitel 1 passt gut zu diesem Paper."

    user_msg = next(
        (m["content"] for m in captured if m.get("role") == "user"), ""
    )
    assert "WEITERER KONTEXT" in user_msg
    assert "Kapitel 1: Einleitung" in user_msg
    assert "Motivation der Dissertation" in user_msg


# ---------------------------------------------------------------------------
# Test 3: core has no static plugin import
# ---------------------------------------------------------------------------

def test_core_has_no_static_plugin_import():
    """webapp.py must not contain a static ``import <addon>`` or
    ``from <addon> ...`` statement for any Add-on Bundle under ``plugins/`` —
    the plugin boundary is enforced (P3 / ADR-0005).  Only importlib-gated
    access by the Bundle loader is allowed.

    Binds wherever Bundles exist; in a public export there are none and
    nothing for the core to import statically.
    """
    bundles = pathlib.Path(webapp.__file__).resolve().parent / "plugins"
    bundle_ids = sorted(
        p.name for p in (bundles.iterdir() if bundles.is_dir() else []) if (p / "plugin.json").is_file()
    )

    src = pathlib.Path(webapp.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(webapp.__file__))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for plugin in bundle_ids:
                assert not module.startswith(plugin), (
                    f"Static 'from {plugin}...' import found in webapp.py "
                    f"at line {node.lineno}"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                for plugin in bundle_ids:
                    assert not alias.name.startswith(plugin), (
                        f"Static 'import {plugin}...' found in webapp.py "
                        f"at line {node.lineno}"
                    )


# ---------------------------------------------------------------------------
# Test 4: an Add-on's chat-context block reaches the LLM on the wire (#190)
# ---------------------------------------------------------------------------

def test_addon_context_block_reaches_the_llm_request(client, seed_paper, db):
    """The research-chat-context slot hands the SPA one text block, which it
    appends to the existing ``extra_context``; the block must arrive in the
    chat-completions payload the LLM endpoint receives (HTTP-level mock —
    ``LLMClient`` speaks ``requests``, so respx cannot see it)."""
    _seed_chunk(db, seed_paper, text="machine learning methods for materials science")

    sent: list[dict] = []

    def fake_post(url, headers=None, json=None, **kw):
        sent.append({"url": url, "json": json})
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"choices": [{"message": {"content": "Mit Gruss."}}]}
        return resp

    block = "Greetings the user has sent so far."
    with patch("llm_client.requests.post", side_effect=fake_post):
        resp = client.post(
            "/api/research-chat/ask",
            json={
                "question": "What machine learning methods are used?",
                "paper_ids": [seed_paper],
                "extra_context": [block],
            },
        )

    assert resp.status_code == 200
    assert resp.json()["answer"] == "Mit Gruss."
    assert sent and sent[0]["url"].endswith("/chat/completions")
    user_msg = next(m["content"] for m in sent[0]["json"]["messages"] if m["role"] == "user")
    assert "WEITERER KONTEXT" in user_msg
    assert block in user_msg
