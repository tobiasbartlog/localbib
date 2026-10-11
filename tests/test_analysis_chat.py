"""POST /api/analysis/chat: a failed answer reaches the SPA as a catalog code
(ADR-0018), never as a Python sentence like ``list index out of range``."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import routers.analysis as analysis_router
from llm_client import LLMClientError

BODY = {"external_papers": [{"title": "A work", "authors": "Doe"}], "question": "What?"}


def _events(resp) -> list[str]:
    return [line[6:] for line in resp.text.splitlines() if line.startswith("data: ")]


def _chat_with(client, llm):
    with patch.object(analysis_router, "llm_for", return_value=llm):
        return client.post("/api/analysis/chat", json=BODY)


def test_llm_error_becomes_chat_failed_code(client):
    llm = MagicMock()
    llm.stream.side_effect = LLMClientError("HTTP 401", status=401)
    events = _events(_chat_with(client, llm))
    assert json.loads(events[0]) == {
        "error": {"code": "error.chat_failed", "params": {"message": "HTTP 401"}}
    }
    assert events[-1] == "[DONE]"


def test_unexpected_error_does_not_leak_its_text(client):
    llm = MagicMock()
    llm.stream.side_effect = IndexError("list index out of range")
    resp = _chat_with(client, llm)
    assert "list index" not in resp.text
    events = _events(resp)
    assert json.loads(events[0]) == {"error": {"code": "error.chat_interrupted"}}
    assert events[-1] == "[DONE]"
