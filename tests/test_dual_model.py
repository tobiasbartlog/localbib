"""Tests for the dual-model feature (reasoning vs. fast) and the reference
chunk-splitting refactor.

Covers:
- Config.fast_model() fallback semantics + reload_from_env defaults.
- llm_for task routing (TASK_MODELS table).
- services.reference_extraction.split_reference_text chunking.
- services.model_recommender heuristic + LLM-answer validation.
- POST /api/llm/suggest-models (LLM path + heuristic fallback).

All external LLM/HTTP calls are mocked; the suite runs fully offline.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import services.model_recommender as recommender
import services.reference_extraction as ref_svc
from literature_manager import Config
from llm_client import TASK_MODELS, llm_for
from tests.llm_helpers import configure_llm


# ---------------------------------------------------------------------------
# Config: fast_model / reasoning_model
# ---------------------------------------------------------------------------

class TestConfigModelAccessors:
    def test_fast_falls_back_to_reasoning_when_unbound(self, monkeypatch):
        configure_llm(monkeypatch, reasoning="big-model")
        assert Config.fast_model() == "big-model"
        assert Config.reasoning_model() == "big-model"

    def test_fast_uses_fast_when_bound(self, monkeypatch):
        configure_llm(monkeypatch, reasoning="big-model", fast="small-model")
        assert Config.fast_model() == "small-model"
        assert Config.reasoning_model() == "big-model"


class TestReloadFromEnv:
    def test_default_model_is_single_sourced(self, monkeypatch):
        """Regression: config.py und routers/settings.py hatten zwei
        verschiedene LLM_MODEL-Defaults (rwth-gpt vs. gpt-5.2).

        Seit #140 ist der ausgelieferte Default **leer** — ein Modellname ist
        anbieterspezifisch, und der Anbieter kommt erst aus dem Onboarding.
        Einzige Quelle bleibt ``Config.reload_from_env()`` (heute: die
        Lese-Migration der alten .env-Keys in das Verbindungsdokument)."""
        monkeypatch.delenv("LLM_MODEL", raising=False)
        old = {k: getattr(Config, k) for k in (
            "LLM_DOCUMENT", "LLM_DOCUMENT_STORED", "CROSSREF_MAILTO",
            "WATCH_INTERVAL", "MAX_OCR_PAGES", "UNLOCK_PDFS",
        )}
        try:
            Config.reload_from_env()
            assert Config.reasoning_model() == ""
        finally:
            for k, v in old.items():
                setattr(Config, k, v)


# ---------------------------------------------------------------------------
# llm_for: task -> model routing (TASK_MODELS)
# ---------------------------------------------------------------------------

class TestLlmForRouting:
    @pytest.fixture(autouse=True)
    def _models(self, monkeypatch):
        configure_llm(monkeypatch, reasoning="big-model", fast="small-model")

    def test_fast_tasks_use_fast_model(self):
        for task, tier in TASK_MODELS.items():
            if tier == "fast":
                assert llm_for(task)._model == "small-model", task

    def test_reasoning_tasks_use_reasoning_model(self):
        for task, tier in TASK_MODELS.items():
            if tier == "reasoning":
                assert llm_for(task)._model == "big-model", task

    def test_explicit_model_overrides_table(self):
        assert llm_for("plugin", model="custom")._model == "custom"

    def test_unknown_task_is_a_programming_error(self):
        with pytest.raises(KeyError):
            llm_for("nonexistent-task")

    def test_every_tier_is_valid(self):
        assert set(TASK_MODELS.values()) <= {"fast", "reasoning"}


# ---------------------------------------------------------------------------
# reference_extraction.split_reference_text
# ---------------------------------------------------------------------------

class TestSplitReferenceText:
    def test_short_text_single_chunk(self):
        assert ref_svc.split_reference_text("a few refs") == ["a few refs"]

    def test_long_text_splits_on_line_boundaries(self):
        # Two 9000-char blocks separated by newlines -> >15000 total -> 2+ chunks.
        block = ("x" * 8999 + "\n")
        text = block * 3
        chunks = ref_svc.split_reference_text(text, max_chars=15000)
        assert len(chunks) >= 2
        # Chunks reassemble to the original text (no data lost).
        assert "".join(chunks) == text

    def test_wrapper_iterates_chunks(self, monkeypatch):
        configure_llm(monkeypatch)
        calls = []

        def fake_single(chunk):
            calls.append(chunk)
            return [{"title": f"ref-{len(calls)}"}]

        monkeypatch.setattr(ref_svc, "_llm_extract_references_single", fake_single)
        monkeypatch.setattr(ref_svc, "split_reference_text", lambda t: ["c1", "c2"])
        result = ref_svc.llm_extract_references("whatever")
        assert len(result) == 2
        assert calls == ["c1", "c2"]


# ---------------------------------------------------------------------------
# model_recommender.heuristic_suggest
# ---------------------------------------------------------------------------

class TestHeuristicSuggest:
    def test_picks_strong_and_fast(self):
        models = ["gpt-4o-mini", "gpt-5-pro", "llama-3.1-8b-instant"]
        out = recommender.heuristic_suggest(models)
        assert out["reasoning"] == "gpt-5-pro"
        assert out["fast"] in ("gpt-4o-mini", "llama-3.1-8b-instant")
        assert out["source"] == "heuristic"

    def test_empty_list_returns_none(self):
        assert recommender.heuristic_suggest([]) is None

    def test_distinct_when_possible(self):
        out = recommender.heuristic_suggest(["opus-model", "haiku-model"])
        assert out["reasoning"] != out["fast"]


# ---------------------------------------------------------------------------
# model_recommender.parse_llm_suggestion
# ---------------------------------------------------------------------------

class TestParseLlmSuggestion:
    MODELS = ["big", "small"]

    def test_valid_answer(self):
        content = '{"reasoning": "big", "fast": "small", "reasoning_reason": "r", "fast_reason": "f"}'
        out = recommender.parse_llm_suggestion(content, self.MODELS)
        assert out == {
            "reasoning": "big", "fast": "small",
            "reasoning_reason": "r", "fast_reason": "f", "source": "llm",
        }

    def test_tolerates_surrounding_prose(self):
        content = 'Sure! {"reasoning": "big", "fast": "small"} hope that helps'
        out = recommender.parse_llm_suggestion(content, self.MODELS)
        assert out["reasoning"] == "big"

    def test_rejects_unknown_ids(self):
        content = '{"reasoning": "gpt-9", "fast": "small"}'
        assert recommender.parse_llm_suggestion(content, self.MODELS) is None

    def test_rejects_garbage(self):
        assert recommender.parse_llm_suggestion("not json", self.MODELS) is None
        assert recommender.parse_llm_suggestion("", self.MODELS) is None


# ---------------------------------------------------------------------------
# POST /api/llm/suggest-models
# ---------------------------------------------------------------------------

class TestSuggestModelsEndpoint:
    """The suggestion runs on ONE connection's list (the one the request
    names — a draft here), never across connections."""

    DRAFT = {"provider": "openai", "api_key": "k"}

    def test_llm_path(self, client, monkeypatch):
        monkeypatch.setattr("routers.llm.fetch_models", lambda base, key: (["big", "small"], None))
        monkeypatch.setattr(Config, "llm_ready", classmethod(lambda cls, tier: True))

        class FakeLLM:
            def complete(self, *a, **k):
                return '{"reasoning": "big", "fast": "small", "reasoning_reason": "strong", "fast_reason": "quick"}'

        monkeypatch.setattr("routers.llm.llm_for", lambda task, model=None: FakeLLM())
        resp = client.post("/api/llm/suggest-models", json=self.DRAFT)
        assert resp.status_code == 200
        sug = resp.json()["suggestion"]
        assert sug["source"] == "llm"
        assert sug["reasoning"] == "big"
        assert sug["fast"] == "small"

    def test_falls_back_to_heuristic_on_llm_error(self, client, monkeypatch):
        monkeypatch.setattr("routers.llm.fetch_models", lambda base, key: (["gpt-5-pro", "gpt-4o-mini"], None))
        monkeypatch.setattr(Config, "llm_ready", classmethod(lambda cls, tier: True))

        class BoomLLM:
            def complete(self, *a, **k):
                raise RuntimeError("boom")

        monkeypatch.setattr("routers.llm.llm_for", lambda task, model=None: BoomLLM())
        resp = client.post("/api/llm/suggest-models", json=self.DRAFT)
        assert resp.status_code == 200
        sug = resp.json()["suggestion"]
        assert sug["source"] == "heuristic"
        assert sug["reasoning"] == "gpt-5-pro"

    def test_without_reasoning_role_uses_the_heuristic(self, client, monkeypatch):
        """A fresh install has nothing to ask yet — the name heuristic still
        gives the user a starting point."""
        monkeypatch.setattr("routers.llm.fetch_models", lambda base, key: (["gpt-5-pro", "gpt-4o-mini"], None))
        monkeypatch.setattr(Config, "llm_ready", classmethod(lambda cls, tier: False))
        resp = client.post("/api/llm/suggest-models", json=self.DRAFT)
        assert resp.json()["suggestion"]["source"] == "heuristic"

    def test_no_models_returns_the_probe_error(self, client, monkeypatch):
        monkeypatch.setattr(
            "routers.llm.fetch_models",
            lambda base, key: ([], {"code": "error.llm.models.http", "params": {"status": 401}}),
        )
        resp = client.post("/api/llm/suggest-models", json=self.DRAFT)
        assert resp.status_code == 200
        assert resp.json()["suggestion"] is None
        assert resp.json()["error"]["code"] == "error.llm.models.http"
