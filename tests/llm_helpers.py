"""Shape the LLM configuration for one test.

The LLM configuration is a document (``Config.LLM_DOCUMENT``: connections +
roles, see ``llm_config``), not a set of flat scalars — so a test states
which roles are bound instead of poking ``Config.LLM_EMBED_MODEL`` &Co.
``configure_llm`` installs a document through ``monkeypatch`` (restored
after the test); ``llm_off`` installs an empty one.

Defaults mirror ``conftest``: one custom connection at ``http://llm.test/v1``
with key ``test-key``, reasoning bound to ``test-model``, fast unbound (falls
back to reasoning), embedding unbound (semantic features off).
"""
from __future__ import annotations

import llm_config
from config import Config

TEST_BASE_URL = "http://llm.test/v1"
TEST_KEY = "test-key"


def configure_llm(
    monkeypatch,
    *,
    reasoning: str | None = "test-model",
    fast: str | None = None,
    embedding: str | None = None,
    base_url: str = TEST_BASE_URL,
    api_key: str = TEST_KEY,
    embed_base_url: str | None = None,
    embed_api_key: str | None = None,
) -> dict:
    """Install a document with the given role bindings and return it.

    ``embedding`` binds the embedding role — to the main connection, or to a
    second connection when ``embed_base_url`` is given (the "Ollama for
    embeddings, OpenAI for chat" shape). An empty string leaves a role unbound.
    """
    doc = llm_config.empty_document()
    doc["connections"].append({
        "id": "test", "label": "Test", "provider": "custom",
        "base_url": base_url, "api_key": api_key,
    })
    if reasoning:
        doc["roles"]["reasoning"] = {"connection_id": "test", "model": reasoning}
    if fast:
        doc["roles"]["fast"] = {"connection_id": "test", "model": fast}
    if embedding:
        conn_id = "test"
        if embed_base_url:
            conn_id = "embed"
            doc["connections"].append({
                "id": "embed", "label": "Embed", "provider": "custom",
                "base_url": embed_base_url,
                "api_key": api_key if embed_api_key is None else embed_api_key,
            })
        doc["roles"]["embedding"] = {"connection_id": conn_id, "model": embedding}
    monkeypatch.setattr(Config, "LLM_DOCUMENT", doc)
    monkeypatch.setattr(Config, "LLM_DOCUMENT_STORED", True)
    return doc


def llm_off(monkeypatch) -> dict:
    """No connection at all: every role off, every gate closed."""
    doc = llm_config.empty_document()
    monkeypatch.setattr(Config, "LLM_DOCUMENT", doc)
    monkeypatch.setattr(Config, "LLM_DOCUMENT_STORED", True)
    return doc
