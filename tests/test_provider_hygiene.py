"""Provider hygiene (#140): shipped defaults name no person and no provider.

The app used to ship the author's personal RWTH address as the CrossRef
``mailto`` default and KI Connect NRW as the LLM provider — neither is usable
(or acceptable) for a customer outside NRW universities. These tests pin the
shipped defaults and the "polite header only when set" rule.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

import llm_config
from literature_manager import Config
from openalex_client import OpenAlexClient


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """Config as a fresh install sees it: no .env, no env vars set."""
    for key in ("CROSSREF_MAILTO", "OPENALEX_API_KEY", "LLM_CONFIG_PATH") + llm_config.LEGACY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    # No llm.json either (a fresh install has none) — never the developer's.
    monkeypatch.setenv("LLM_CONFIG_PATH", str(tmp_path / "llm.json"))
    Config.reload_from_env()
    yield Config
    # Restore the suite-wide env (conftest sets these before importing webapp).
    monkeypatch.undo()
    Config.reload_from_env()


def test_no_personal_mailto_in_shipped_defaults(clean_env):
    """A fresh install must not send a stranger's e-mail on the user's behalf."""
    assert clean_env.CROSSREF_MAILTO == ""


def test_no_kiconnect_default_provider(clean_env):
    """A fresh install points at no provider at all — onboarding picks one.

    KI Connect NRW is reachable only for RWTH/NRW university accounts, so
    shipping it as the default (provider, endpoint and the ``gpt-5.5`` model
    id) would hand every other customer a broken configuration.
    """
    assert clean_env.llm_status() == {
        "reasoning": False, "fast": False, "embedding": False, "connections": 0,
    }
    assert clean_env.reasoning_model() == ""
    assert clean_env.llm_endpoint("reasoning") is None


class TestPoliteMailto:
    """The polite ``mailto`` identifies the *user*, so it is sent only when set."""

    def test_crossref_sends_no_mailto_when_unset(self, clean_env, monkeypatch):
        captured = {}

        def fake_get(url, headers=None, timeout=None, **kwargs):
            captured["headers"] = headers or {}
            raise RuntimeError("stop after the request was built")

        monkeypatch.setattr("crossref.requests.get", fake_get)
        import crossref

        crossref.fetch_crossref_metadata("10.1000/x")
        assert "mailto" not in captured["headers"].get("User-Agent", "").lower()

    def test_crossref_sends_the_configured_mailto(self, clean_env, monkeypatch):
        captured = {}

        def fake_get(url, headers=None, timeout=None, **kwargs):
            captured["headers"] = headers or {}
            raise RuntimeError("stop after the request was built")

        monkeypatch.setenv("CROSSREF_MAILTO", "user@uni.example")
        clean_env.reload_from_env()
        monkeypatch.setattr("crossref.requests.get", fake_get)
        import crossref

        crossref.fetch_crossref_metadata("10.1000/x")
        assert "mailto:user@uni.example" in captured["headers"]["User-Agent"]

    @respx.mock
    def test_openalex_omits_the_param_when_unset(self, clean_env):
        """Without an address OpenAlex is queried anonymously (common pool),
        never with a placeholder address invented by us."""
        route = respx.get("https://api.openalex.org/works").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        OpenAlexClient(Config.polite_mailto()).fetch_works_by_doi(["10.1000/x"])
        assert "mailto=" not in str(route.calls[0].request.url)


# The two addresses that must not survive anywhere in what we ship: the
# author's personal RWTH address, and the invented fallback the call sites used
# to substitute when no address was configured.
_FORBIDDEN_ADDRESSES = ("bartlog@icom.rwth-aachen.de", "literatur-manager@example.com")


def test_no_address_is_shipped_in_source_or_template():
    """Neither the author's address nor an invented one may reach a request."""
    root = Path(__file__).resolve().parent.parent
    scanned = [p for p in root.rglob("*.py")
               if not any(part in {"node_modules", "build", "dist", ".claude",
                                   "tests", "__pycache__", ".git"}
                          for part in p.relative_to(root).parts)]
    scanned.append(root / ".env.template")

    offenders = [
        f"{path.relative_to(root)}: {address}"
        for path in scanned
        for address in _FORBIDDEN_ADDRESSES
        if address in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == []
