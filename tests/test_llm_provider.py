"""Migration of the flat .env provider keys into the connection document.

An installation that predates llm.json carries LLM_PROVIDER / LLM_API_KEY /
LLM_BASE_URL / LLM_MODEL in its .env. ``Config.reload_from_env`` turns them
into one connection ``default`` (read-only — nothing is written until the
user saves the LLM tab), so every old install keeps working untouched.
"""
import pytest

from literature_manager import Config

_LEGACY = ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "KICONNECT_API_KEY",
           "LLM_MODEL", "LLM_MODEL_FAST", "LLM_EMBED_MODEL", "LLM_EMBED_URL")


@pytest.fixture
def legacy_env(monkeypatch):
    """A clean legacy environment: set what a test needs, reload, restore."""
    for key in _LEGACY:
        monkeypatch.delenv(key, raising=False)

    def apply(**env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        Config.reload_from_env()
        return Config

    yield apply
    monkeypatch.undo()
    Config.reload_from_env()


def test_preset_resolves_to_openai_compatible_endpoints(legacy_env):
    cfg = legacy_env(LLM_PROVIDER="openrouter", LLM_API_KEY="sk-test", LLM_MODEL="m")
    ep = cfg.llm_endpoint("reasoning")
    assert ep["chat_url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert ep["models_url"] == "https://openrouter.ai/api/v1/models"
    assert ep["api_key"] == "sk-test"
    assert ep["connection_id"] == "default"


def test_custom_base_url_overrides_preset(legacy_env):
    cfg = legacy_env(LLM_PROVIDER="custom", LLM_BASE_URL="http://localhost:11434/v1/",  # trailing slash trimmed
                     LLM_MODEL="llama3")
    ep = cfg.llm_endpoint("reasoning")
    assert ep["chat_url"] == "http://localhost:11434/v1/chat/completions"
    assert ep["models_url"] == "http://localhost:11434/v1/models"
    # Keyless is a valid, READY configuration (Ollama).
    assert ep["api_key"] == ""
    assert cfg.llm_ready("reasoning")


def test_base_url_override_applies_to_named_preset(legacy_env):
    cfg = legacy_env(LLM_PROVIDER="openai", LLM_BASE_URL="https://proxy.example.com/v1",
                     LLM_API_KEY="key", LLM_MODEL="m")
    assert cfg.llm_endpoint("reasoning")["chat_url"] == "https://proxy.example.com/v1/chat/completions"


def test_api_key_falls_back_to_kiconnect_env(legacy_env):
    cfg = legacy_env(KICONNECT_API_KEY="legacy-key", LLM_PROVIDER="kiconnect", LLM_MODEL="m")
    assert cfg.llm_endpoint("reasoning")["api_key"] == "legacy-key"


def test_provider_without_model_is_migrated_but_not_ready(legacy_env):
    """The onboarding used to store provider + key without a model; that is
    a connection (so onboarding will not ask again) with nothing bound."""
    cfg = legacy_env(LLM_PROVIDER="openai", LLM_API_KEY="k")
    assert cfg.llm_status()["connections"] == 1
    assert cfg.llm_ready("reasoning") is False
