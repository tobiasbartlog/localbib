"""GET /api/llm/embed-models — Modell-Liste fuers Embedding-Dropdown (Settings-UI).

Ohne Embed-URL-Override liefert der Endpunkt die Liste des aktiven
LLM-Providers (embedding-artige Modelle zuerst); mit Override wird dessen
abgeleiteter /models-Endpunkt abgefragt. Fehler degradieren zu einer leeren
Liste — niemals 500.
"""

from unittest.mock import MagicMock

from literature_manager import Config
from routers.settings import _embed_models_url


class TestEmbedModelsUrlDerivation:
    def test_strips_embeddings_suffix(self):
        assert _embed_models_url("http://localhost:11434/v1/embeddings") == "http://localhost:11434/v1/models"

    def test_plain_base_url_gets_models_appended(self):
        assert _embed_models_url("http://localhost:11434/v1/") == "http://localhost:11434/v1/models"

    def test_empty_returns_empty(self):
        assert _embed_models_url("") == ""
        assert _embed_models_url("   ") == ""


class TestEmbedModelsEndpoint:
    def test_uses_provider_list_without_override(self, client, monkeypatch):
        monkeypatch.setattr(Config, "AVAILABLE_MODELS", ["gpt-4o", "text-embedding-3-small", "qwen3-embedding-8b"])
        monkeypatch.setattr(Config, "LLM_EMBED_URL", "")
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "qwen3-embedding-8b")
        resp = client.get("/api/llm/embed-models")
        assert resp.status_code == 200
        data = resp.json()
        # Embedding-artige Modelle stehen vor den Chat-Modellen
        assert data["models"] == ["qwen3-embedding-8b", "text-embedding-3-small", "gpt-4o"]
        assert data["current"] == "qwen3-embedding-8b"

    def test_override_url_fetches_derived_models_endpoint(self, client, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_URL", "http://localhost:11434/v1/embeddings")
        monkeypatch.setattr(Config, "LLM_EMBED_MODEL", "")
        monkeypatch.setattr(Config, "LLM_API_KEY", "")
        fake = MagicMock()
        fake.status_code = 200
        fake.json.return_value = {"data": [{"id": "nomic-embed-text"}, {"id": "llama3"}]}
        captured = {}

        def fake_get(url, **kwargs):
            captured["url"] = url
            captured["headers"] = kwargs.get("headers")
            return fake

        monkeypatch.setattr("routers.settings.http_requests.get", fake_get)
        resp = client.get("/api/llm/embed-models")
        assert resp.status_code == 200
        assert captured["url"] == "http://localhost:11434/v1/models"
        # Ohne API-Key kein Authorization-Header (z. B. Ollama)
        assert "Authorization" not in (captured["headers"] or {})
        assert resp.json()["models"] == ["nomic-embed-text", "llama3"]

    def test_override_sends_bearer_when_key_configured(self, client, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_URL", "https://api.example.org/v1/embeddings")
        monkeypatch.setattr(Config, "LLM_API_KEY", "sk-test")
        fake = MagicMock()
        fake.status_code = 200
        fake.json.return_value = {"data": []}
        captured = {}

        def fake_get(url, **kwargs):
            captured["headers"] = kwargs.get("headers")
            return fake

        monkeypatch.setattr("routers.settings.http_requests.get", fake_get)
        client.get("/api/llm/embed-models")
        assert captured["headers"]["Authorization"] == "Bearer sk-test"

    def test_url_param_overrides_saved_config(self, client, monkeypatch):
        # Der url-Param (ungespeicherte SPA-Eingabe) schlaegt die gespeicherte
        # URL; leerer Param heisst explizit "kein Override" -> Provider-Liste.
        monkeypatch.setattr(Config, "LLM_EMBED_URL", "http://old:1/v1/embeddings")
        monkeypatch.setattr(Config, "AVAILABLE_MODELS", ["provider-model"])
        resp = client.get("/api/llm/embed-models", params={"url": ""})
        assert resp.status_code == 200
        assert resp.json()["models"] == ["provider-model"]

    def test_fetch_failure_degrades_to_empty_list(self, client, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_URL", "http://localhost:11434/v1/embeddings")

        def boom(url, **kwargs):
            raise OSError("connection refused")

        monkeypatch.setattr("routers.settings.http_requests.get", boom)
        resp = client.get("/api/llm/embed-models")
        assert resp.status_code == 200
        assert resp.json()["models"] == []

    def test_non_200_degrades_to_empty_list(self, client, monkeypatch):
        monkeypatch.setattr(Config, "LLM_EMBED_URL", "http://localhost:11434/v1/embeddings")
        fake = MagicMock()
        fake.status_code = 401
        fake.text = "unauthorized"
        monkeypatch.setattr("routers.settings.http_requests.get", lambda *a, **k: fake)
        resp = client.get("/api/llm/embed-models")
        assert resp.status_code == 200
        assert resp.json()["models"] == []
