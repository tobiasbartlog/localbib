"""GET /api/llm/models — eine leere Liste muss ihren Grund mitliefern.

Ohne das endet jeder Fehler als leeres Dropdown, dessen Ursache nur im Log
steht — und das Log sieht im gebauten .exe niemand. Der Bug, der das ausgeloest
hat: ein Zertifikatsfehler (Haus-Bundle ohne Wurzel, siehe ``ca_trust``) liess
die Modellauswahl kommentarlos verschwinden.
"""

import requests
from unittest.mock import MagicMock

import routers.settings as settings_router
from literature_manager import Config


def _reset(monkeypatch, **cfg):
    monkeypatch.setattr(Config, "AVAILABLE_MODELS", [])
    monkeypatch.setattr(Config, "LLM_API_KEY", cfg.get("key", "k"))
    monkeypatch.setattr(Config, "LLM_MODELS_URL", cfg.get("url", "https://example.invalid/v1/models"))
    monkeypatch.setattr(settings_router, "_last_models_error", None)


class TestModelsEndpointErrorSurface:
    def test_success_reports_no_error(self, client, monkeypatch):
        _reset(monkeypatch)
        fake = MagicMock(status_code=200)
        fake.json.return_value = {"data": [{"id": "gpt-4o"}]}
        monkeypatch.setattr(settings_router.http_requests, "get", lambda *a, **k: fake)

        data = client.get("/api/llm/models").json()

        assert data["models"] == ["gpt-4o"]
        assert data["error"] is None

    def test_certificate_error_names_the_configured_bundle(self, client, monkeypatch, tmp_path):
        """Der Fall aus dem Bug: die Meldung muss auf das Bundle zeigen."""
        bundle = tmp_path / "haus.pem"
        bundle.write_text("-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n")
        _reset(monkeypatch)
        monkeypatch.setattr(settings_router.ca_trust, "configured_extra_ca", lambda: str(bundle))

        def boom(*a, **k):
            raise requests.exceptions.SSLError(
                "CERTIFICATE_VERIFY_FAILED: unable to get issuer certificate"
            )

        monkeypatch.setattr(settings_router.http_requests, "get", boom)

        data = client.get("/api/llm/models").json()

        assert data["models"] == []
        assert "Zertifikatspruefung" in data["error"]
        assert str(bundle) in data["error"]

    def test_certificate_error_without_bundle_points_at_proxy(self, client, monkeypatch):
        _reset(monkeypatch)
        monkeypatch.setattr(settings_router.ca_trust, "configured_extra_ca", lambda: None)

        def boom(*a, **k):
            raise requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")

        monkeypatch.setattr(settings_router.http_requests, "get", boom)

        error = client.get("/api/llm/models").json()["error"]

        assert "REQUESTS_CA_BUNDLE" in error

    def test_http_error_reports_status(self, client, monkeypatch):
        _reset(monkeypatch)
        fake = MagicMock(status_code=401, text="unauthorized")
        monkeypatch.setattr(settings_router.http_requests, "get", lambda *a, **k: fake)

        error = client.get("/api/llm/models").json()["error"]

        assert "401" in error

    def test_missing_key_is_explained(self, client, monkeypatch):
        _reset(monkeypatch, key="")

        data = client.get("/api/llm/models").json()

        assert data["models"] == []
        assert "API-Key" in data["error"]

    def test_endpoint_never_500s_on_network_failure(self, client, monkeypatch):
        _reset(monkeypatch)

        def boom(*a, **k):
            raise requests.exceptions.ConnectionError("no route")

        monkeypatch.setattr(settings_router.http_requests, "get", boom)

        resp = client.get("/api/llm/models")

        assert resp.status_code == 200
        assert "Verbindung" in resp.json()["error"]
