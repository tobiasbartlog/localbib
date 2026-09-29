"""LLM connections + roles (llm.json): document rules, Config resolution, the
HTTP seam, and the migration from the flat .env keys.

The point of the feature: three roles (reasoning / fast / embedding) each bound
to their own (connection, model) pair, so e.g. embeddings run on a keyless
local Ollama while the chat runs on OpenAI. Before, ``LLM_EMBED_URL`` allowed a
foreign embedding endpoint but sent the *chat* key to it, and a keyless
provider read as "no LLM" everywhere the key was the feature gate.

Every test redirects ``.env`` and ``llm.json`` to a temp dir (conftest already
points ``LLM_CONFIG_PATH`` at the suite's temp base; here each test gets its
own so a stored document never leaks into another test).
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import llm_config
import routers.llm as llm_router
import webapp
from config import Config
from llm_client import embed_texts, llm_for

PROVIDERS = Config.LLM_PROVIDERS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def llm_env(tmp_path, monkeypatch):
    """A private .env (with legacy LLM keys) and llm.json location.

    Legacy env vars are re-set through monkeypatch so their pre-test values
    come back afterwards — the PUT removes them from ``os.environ``."""
    import routers.settings as settings_mod

    env = tmp_path / ".env"
    env.write_text(
        "# kept comment\nLLM_PROVIDER=openai\nLLM_API_KEY=sk-old-1234\n"
        "LLM_MODEL=gpt-old\nLLM_MODEL_FAST=gpt-old-mini\nCROSSREF_MAILTO=me@uni.example\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_mod, "_ENV_PATH", env)
    monkeypatch.setattr(Config, "ENV_PATH", str(env))
    monkeypatch.setenv("LLM_CONFIG_PATH", str(tmp_path / "llm.json"))
    # Record (for restore) and clear every legacy key — the developer's own
    # .env is in os.environ and must not shape these tests.
    for key in llm_config.LEGACY_ENV_KEYS:
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "sk-old-1234")
    monkeypatch.setenv("LLM_MODEL", "gpt-old")
    monkeypatch.setenv("LLM_MODEL_FAST", "gpt-old-mini")
    llm_router._models_cache.clear()
    Config.reload_from_env()
    yield tmp_path
    llm_router._models_cache.clear()
    monkeypatch.undo()
    Config.reload_from_env()


TWO_CONNECTIONS = {
    "connections": [
        {"id": "oai", "label": "OpenAI", "provider": "openai", "base_url": "", "api_key": "sk-oai-1234"},
        {"id": "local", "label": "Ollama", "provider": "custom",
         "base_url": "http://localhost:11434/v1/", "api_key": ""},
    ],
    "roles": {
        "reasoning": {"connection_id": "oai", "model": "gpt-4o"},
        "fast": {"connection_id": "local", "model": "llama3"},
        "embedding": {"connection_id": "local", "model": "nomic-embed-text"},
    },
}


@pytest.fixture
def stored(llm_env):
    """The two-connection document written to llm.json and loaded."""
    doc = llm_config.normalize(TWO_CONNECTIONS, PROVIDERS)
    llm_config.save(Config.LLM_CONFIG_PATH, doc)
    Config.reload_from_env()
    return doc


# ---------------------------------------------------------------------------
# llm_config: document rules
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_canonical_form_and_preset_base_url(self):
        doc = llm_config.normalize(TWO_CONNECTIONS, PROVIDERS)
        assert [c["id"] for c in doc["connections"]] == ["oai", "local"]
        # Trailing slash trimmed; preset URL is not copied into the connection.
        assert doc["connections"][1]["base_url"] == "http://localhost:11434/v1"
        assert doc["connections"][0]["base_url"] == ""
        assert llm_config.connection_base_url(doc["connections"][0], PROVIDERS) == "https://api.openai.com/v1"

    def test_missing_id_is_generated_and_label_defaults_to_preset(self):
        doc = llm_config.normalize(
            {"connections": [{"provider": "groq", "api_key": "k"}], "roles": {}}, PROVIDERS,
        )
        conn = doc["connections"][0]
        assert conn["id"]
        assert conn["label"] == PROVIDERS["groq"]["label"]
        # Every tier is present, unbound.
        assert doc["roles"] == {t: {"connection_id": "", "model": ""} for t in llm_config.TIERS}

    def test_null_key_keeps_the_stored_key_and_empty_string_removes_it(self):
        current = llm_config.normalize(TWO_CONNECTIONS, PROVIDERS)
        incoming = {
            "connections": [
                {"id": "oai", "provider": "openai"},                      # api_key absent -> unchanged
                {"id": "local", "provider": "custom", "base_url": "http://l/v1", "api_key": ""},
            ],
            "roles": {},
        }
        doc = llm_config.normalize(incoming, PROVIDERS, current=current)
        assert doc["connections"][0]["api_key"] == "sk-oai-1234"
        assert doc["connections"][1]["api_key"] == ""

    @pytest.mark.parametrize("payload, code", [
        ({"connections": [{"id": "x", "provider": "nope"}]}, "error.llm.providerUnknown"),
        ({"connections": [{"id": "x", "provider": "custom"}]}, "error.llm.baseUrlRequired"),
        ({"connections": [{"id": "x", "provider": "openai"}, {"id": "x", "provider": "groq"}]},
         "error.llm.connectionDuplicate"),
        ({"connections": [], "roles": {"reasoning": {"connection_id": "ghost", "model": "m"}}},
         "error.llm.connectionUnknown"),
        ("not a document", "error.llm.documentInvalid"),
    ])
    def test_rejects_what_must_not_be_persisted(self, payload, code):
        with pytest.raises(llm_config.DocumentError) as exc:
            llm_config.normalize(payload, PROVIDERS)
        assert exc.value.code == code

    def test_role_bound_to_connection_without_model_is_allowed(self):
        """A legitimate intermediate state while setting things up — the role
        is simply not ready, not a 422."""
        doc = llm_config.normalize(
            {"connections": TWO_CONNECTIONS["connections"],
             "roles": {"embedding": {"connection_id": "local", "model": ""}}}, PROVIDERS,
        )
        assert llm_config.resolve_role(doc, "embedding", PROVIDERS) is None


class TestMask:
    def test_keys_never_appear_in_the_wire_form(self):
        doc = llm_config.normalize(TWO_CONNECTIONS, PROVIDERS)
        wire = llm_config.mask(doc)
        assert "sk-oai-1234" not in json.dumps(wire)
        assert wire["connections"][0] == {
            "id": "oai", "label": "OpenAI", "provider": "openai", "base_url": "",
            "has_key": True, "key_hint": "sk-…1234",
        }
        assert wire["connections"][1]["has_key"] is False
        assert wire["connections"][1]["key_hint"] == ""

    def test_short_keys_show_only_the_tail(self):
        assert llm_config.key_hint("abcd") == "…cd"
        assert llm_config.key_hint("") == ""


class TestResolveRole:
    def test_fast_falls_back_to_reasoning_but_embedding_does_not(self):
        doc = llm_config.normalize(
            {"connections": TWO_CONNECTIONS["connections"],
             "roles": {"reasoning": {"connection_id": "oai", "model": "gpt-4o"}}}, PROVIDERS,
        )
        assert llm_config.resolve_role(doc, "fast", PROVIDERS)["model"] == "gpt-4o"
        assert llm_config.resolve_role(doc, "embedding", PROVIDERS) is None

    def test_keyless_connection_is_usable(self):
        doc = llm_config.normalize(TWO_CONNECTIONS, PROVIDERS)
        ep = llm_config.resolve_role(doc, "embedding", PROVIDERS)
        assert ep["api_key"] == ""
        assert ep["embed_url"] == "http://localhost:11434/v1/embeddings"
        assert ep["models_url"] == "http://localhost:11434/v1/models"

    def test_status_counts_connections(self):
        doc = llm_config.normalize(TWO_CONNECTIONS, PROVIDERS)
        assert llm_config.status(doc, PROVIDERS) == {
            "reasoning": True, "fast": True, "embedding": True, "connections": 2,
        }


class TestFromLegacyEnv:
    def _legacy(self, **kw):
        base = dict(provider="", base_url="", api_key="", model="", model_fast="",
                    embed_model="", embed_url="", providers=PROVIDERS)
        base.update(kw)
        return llm_config.from_legacy_env(**base)

    def test_fresh_install_has_no_document(self):
        assert self._legacy() is None

    def test_old_env_becomes_one_default_connection(self):
        doc = self._legacy(provider="openai", api_key="sk-x", model="gpt-4o", embed_model="text-embedding-3-small")
        assert [c["id"] for c in doc["connections"]] == ["default"]
        assert doc["roles"]["reasoning"] == {"connection_id": "default", "model": "gpt-4o"}
        # LLM_MODEL_FAST empty -> fast unbound (the fallback rule covers it).
        assert doc["roles"]["fast"] == {"connection_id": "", "model": ""}
        assert doc["roles"]["embedding"] == {"connection_id": "default", "model": "text-embedding-3-small"}

    def test_embed_url_override_becomes_a_second_connection(self):
        doc = self._legacy(provider="openai", api_key="sk-x", model="gpt-4o",
                           embed_model="nomic-embed-text",
                           embed_url="http://localhost:11434/v1/embeddings")
        ids = [c["id"] for c in doc["connections"]]
        assert ids == ["default", "embedding"]
        assert doc["connections"][1]["base_url"] == "http://localhost:11434/v1"
        assert doc["roles"]["embedding"]["connection_id"] == "embedding"

    def test_unknown_provider_degrades_to_custom(self):
        doc = self._legacy(provider="rwth-gpt", base_url="http://x/v1", api_key="k", model="m")
        assert doc["connections"][0]["provider"] == "custom"


# ---------------------------------------------------------------------------
# Config: resolution with and without a stored document
# ---------------------------------------------------------------------------

class TestConfigResolution:
    def test_without_llm_json_the_flat_env_keys_still_configure_the_app(self, llm_env):
        """Existing installations keep working untouched (read-only migration)."""
        assert Config.LLM_DOCUMENT_STORED is False
        assert Config.llm_endpoint("reasoning")["chat_url"] == "https://api.openai.com/v1/chat/completions"
        assert Config.llm_endpoint("reasoning")["api_key"] == "sk-old-1234"
        assert Config.llm_ready("reasoning")
        assert Config.llm_endpoint("fast")["model"] == "gpt-old-mini"
        assert Config.llm_ready("embedding") is False
        # The migrated view the settings page will show:
        assert [c["id"] for c in Config.LLM_DOCUMENT["connections"]] == ["default"]

    def test_with_llm_json_each_role_runs_on_its_own_connection(self, stored):
        assert Config.LLM_DOCUMENT_STORED is True
        r = Config.llm_endpoint("reasoning")
        f = Config.llm_endpoint("fast")
        e = Config.llm_endpoint("embedding")
        assert (r["chat_url"], r["api_key"], r["model"]) == (
            "https://api.openai.com/v1/chat/completions", "sk-oai-1234", "gpt-4o")
        assert (f["chat_url"], f["api_key"], f["model"]) == (
            "http://localhost:11434/v1/chat/completions", "", "llama3")
        assert (e["embed_url"], e["api_key"], e["model"]) == (
            "http://localhost:11434/v1/embeddings", "", "nomic-embed-text")
        assert Config.llm_status() == {"reasoning": True, "fast": True, "embedding": True, "connections": 2}

    def test_model_accessors_follow_the_roles(self, stored):
        assert Config.reasoning_model() == "gpt-4o"
        assert Config.fast_model() == "llama3"
        assert Config.embed_model() == "nomic-embed-text"

    def test_gates_are_role_aware(self, llm_env):
        """A keyless Ollama bound to fast/embedding is ON; a reasoning role
        without a model is OFF — the key was never the right question."""
        doc = llm_config.normalize(
            {"connections": TWO_CONNECTIONS["connections"],
             "roles": {"reasoning": {"connection_id": "oai", "model": ""},
                       "fast": {"connection_id": "local", "model": "llama3"},
                       "embedding": {"connection_id": "local", "model": "nomic-embed-text"}}},
            PROVIDERS,
        )
        llm_config.save(Config.LLM_CONFIG_PATH, doc)
        Config.reload_from_env()
        assert Config.llm_ready("reasoning") is False
        assert Config.llm_ready("fast") is True
        assert Config.llm_ready("embedding") is True

    def test_corrupt_llm_json_leaves_the_app_running_with_no_llm(self, llm_env):
        (llm_env / "llm.json").write_text("{not json", encoding="utf-8")
        Config.reload_from_env()
        assert Config.LLM_DOCUMENT_STORED is False
        # Falls back to the legacy view rather than crashing at import.
        assert Config.llm_ready("reasoning")


# ---------------------------------------------------------------------------
# llm_for / embed_texts on a stored document
# ---------------------------------------------------------------------------

class TestSeam:
    def test_llm_for_routes_fast_tasks_to_the_fast_connection(self, stored):
        c = llm_for("categorize")
        assert (c._url, c._model, c._api_key) == ("http://localhost:11434/v1/chat/completions", "llama3", "")
        r = llm_for("research_chat")
        assert (r._url, r._model, r._api_key) == ("https://api.openai.com/v1/chat/completions", "gpt-4o", "sk-oai-1234")
        # A model override keeps the role's connection.
        p = llm_for("plugin", model="gpt-4o-mini")
        assert (p._url, p._model) == ("https://api.openai.com/v1/chat/completions", "gpt-4o-mini")

    def test_keyless_client_sends_no_authorization_header(self, stored):
        assert "Authorization" not in llm_for("categorize")._headers()
        assert llm_for("research_chat")._headers()["Authorization"] == "Bearer sk-oai-1234"

    def test_embed_texts_uses_the_embedding_connection_not_the_chat_key(self, stored):
        """The bug the feature closes: LLM_EMBED_URL pointed at Ollama, but the
        request carried the chat provider's key."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}
        with patch("llm_client.requests.post", return_value=resp) as post:
            assert embed_texts(["a"]) == [[0.1, 0.2]]
        url = post.call_args.args[0]
        kwargs = post.call_args.kwargs
        assert url == "http://localhost:11434/v1/embeddings"
        assert kwargs["json"]["model"] == "nomic-embed-text"
        assert "Authorization" not in kwargs["headers"]

    def test_embed_texts_degrades_when_the_role_is_unbound(self, llm_env):
        doc = llm_config.normalize(
            {"connections": TWO_CONNECTIONS["connections"],
             "roles": {"reasoning": {"connection_id": "oai", "model": "gpt-4o"}}}, PROVIDERS,
        )
        llm_config.save(Config.LLM_CONFIG_PATH, doc)
        Config.reload_from_env()
        with pytest.raises(NotImplementedError):
            embed_texts(["a"])


# ---------------------------------------------------------------------------
# HTTP seam
# ---------------------------------------------------------------------------

class TestConfigEndpoint:
    def test_get_shows_the_migrated_legacy_view_with_masked_keys(self, llm_env):
        with TestClient(webapp.app) as c:
            data = c.get("/api/llm/config").json()
        assert [x["id"] for x in data["connections"]] == ["default"]
        assert data["connections"][0]["key_hint"] == "sk-…1234"
        assert "sk-old-1234" not in json.dumps(data)
        assert data["roles"]["reasoning"] == {"connection_id": "default", "model": "gpt-old"}
        assert data["status"]["reasoning"] is True

    def test_put_persists_strips_legacy_env_keys_and_takes_effect(self, llm_env):
        with TestClient(webapp.app) as c:
            resp = c.put("/api/llm/config", json=TWO_CONNECTIONS)
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"]["embedding"] is True
            # The document is on disk, with the keys.
            on_disk = json.loads((llm_env / "llm.json").read_text(encoding="utf-8"))
            assert on_disk["connections"][0]["api_key"] == "sk-oai-1234"
            # The legacy keys are gone from the .env; other lines survive.
            env_text = (llm_env / ".env").read_text(encoding="utf-8")
            for key in llm_config.LEGACY_ENV_KEYS:
                assert f"{key}=" not in env_text, key
            assert "# kept comment" in env_text
            assert "CROSSREF_MAILTO=me@uni.example" in env_text
            assert "LLM_API_KEY" not in os.environ
            # ...and GET /api/settings no longer reports them either.
            assert "LLM_API_KEY" not in c.get("/api/settings").json()
        # The running app switched without a restart.
        assert llm_for("categorize")._model == "llama3"
        assert Config.llm_ready("embedding")

    def test_put_with_null_key_keeps_the_stored_key(self, stored):
        wire = llm_config.mask(Config.LLM_DOCUMENT)
        wire["connections"][0]["api_key"] = None
        with TestClient(webapp.app) as c:
            assert c.put("/api/llm/config", json={"connections": wire["connections"], "roles": wire["roles"]}).status_code == 200
        assert Config.llm_endpoint("reasoning")["api_key"] == "sk-oai-1234"

    def test_put_rejects_a_role_bound_to_a_removed_connection(self, stored):
        payload = {
            "connections": [TWO_CONNECTIONS["connections"][0]],   # "local" removed …
            "roles": TWO_CONNECTIONS["roles"],                      # … but still bound
        }
        with TestClient(webapp.app) as c:
            resp = c.put("/api/llm/config", json=payload)
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "error.llm.connectionUnknown"
        assert resp.json()["detail"]["params"]["id"] == "local"
        # Nothing was written.
        assert Config.llm_endpoint("fast")["model"] == "llama3"

    def test_status_endpoint(self, stored):
        with TestClient(webapp.app) as c:
            assert c.get("/api/llm/status").json() == {
                "reasoning": True, "fast": True, "embedding": True, "connections": 2,
            }


class TestModelsEndpoint:
    def _ok(self, *ids):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"data": [{"id": i} for i in ids]}
        return resp

    def test_saved_connection_by_id_needs_no_key_in_the_request(self, stored, monkeypatch):
        captured = {}

        def fake_get(url, **kw):
            captured["url"], captured["headers"] = url, kw.get("headers")
            return self._ok("gpt-4o", "gpt-4o-mini")

        monkeypatch.setattr(llm_router.http_requests, "get", fake_get)
        with TestClient(webapp.app) as c:
            # The startup hook probes every bound connection; look only at
            # the request this endpoint makes.
            llm_router._models_cache.clear()
            captured.clear()
            data = c.post("/api/llm/models", json={"connection_id": "oai"}).json()
        assert data == {"models": ["gpt-4o", "gpt-4o-mini"], "error": None, "current": ""}
        assert captured["url"] == "https://api.openai.com/v1/models"
        assert captured["headers"]["Authorization"] == "Bearer sk-oai-1234"

    def test_unsaved_draft_is_probed_with_its_own_key(self, llm_env, monkeypatch):
        captured = {}

        def fake_get(url, **kw):
            captured["url"], captured["headers"] = url, kw.get("headers")
            return self._ok("llama3")

        monkeypatch.setattr(llm_router.http_requests, "get", fake_get)
        with TestClient(webapp.app) as c:
            data = c.post("/api/llm/models", json={
                "provider": "custom", "base_url": "http://localhost:11434/v1/", "api_key": "",
            }).json()
        assert data["models"] == ["llama3"]
        assert captured["url"] == "http://localhost:11434/v1/models"
        assert "Authorization" not in captured["headers"]

    def test_failures_are_error_codes_not_500s(self, llm_env, monkeypatch):
        import requests

        def boom(*a, **k):
            raise requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")

        monkeypatch.setattr(llm_router.http_requests, "get", boom)
        monkeypatch.setattr(llm_router.ca_trust, "configured_extra_ca", lambda: "C:/haus.pem")
        with TestClient(webapp.app) as c:
            data = c.post("/api/llm/models", json={"provider": "openai", "api_key": "k"}).json()
            assert data["models"] == []
            assert data["error"] == {"code": "error.llm.models.certBundle", "params": {"bundle": "C:/haus.pem"}}
            # No endpoint at all is explained, not attempted.
            data = c.post("/api/llm/models", json={"provider": "custom", "base_url": ""}).json()
            assert data["error"]["code"] == "error.llm.models.noEndpoint"
            # Unknown saved id -> 404 with a code.
            assert c.post("/api/llm/models", json={"connection_id": "ghost"}).status_code == 404

    def test_http_error_reports_status(self, llm_env, monkeypatch):
        monkeypatch.setattr(llm_router.http_requests, "get",
                            lambda *a, **k: MagicMock(status_code=401, text="unauthorized"))
        with TestClient(webapp.app) as c:
            data = c.post("/api/llm/models", json={"provider": "openai", "api_key": "k"}).json()
        assert data["error"] == {"code": "error.llm.models.http", "params": {"status": 401}}
