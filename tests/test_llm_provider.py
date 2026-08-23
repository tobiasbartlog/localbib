import pytest

from literature_manager import Config


@pytest.fixture
def restore_llm_config():
    """Snapshot and restore the mutable LLM-related Config attributes."""
    snap = {
        k: getattr(Config, k)
        for k in (
            "LLM_PROVIDER",
            "LLM_BASE_URL",
            "LLM_API_KEY",
            "LLM_CHAT_URL",
            "LLM_MODELS_URL",
            "KICONNECT_API_URL",
            "KICONNECT_API_KEY",
        )
    }
    yield
    for k, v in snap.items():
        setattr(Config, k, v)


def test_preset_resolves_to_openai_compatible_endpoints(restore_llm_config):
    Config.LLM_PROVIDER = "openrouter"
    Config.LLM_BASE_URL = ""
    Config.LLM_API_KEY = "sk-test"
    Config.resolve_llm()

    assert Config.LLM_CHAT_URL == "https://openrouter.ai/api/v1/chat/completions"
    assert Config.LLM_MODELS_URL == "https://openrouter.ai/api/v1/models"
    # Backward-compat aliases stay in sync with the resolved provider
    assert Config.KICONNECT_API_URL == Config.LLM_CHAT_URL
    assert Config.KICONNECT_API_KEY == "sk-test"


def test_custom_base_url_overrides_preset(restore_llm_config):
    Config.LLM_PROVIDER = "custom"
    Config.LLM_BASE_URL = "http://localhost:11434/v1/"  # trailing slash trimmed
    Config.LLM_API_KEY = ""
    Config.resolve_llm()

    assert Config.LLM_CHAT_URL == "http://localhost:11434/v1/chat/completions"
    assert Config.LLM_MODELS_URL == "http://localhost:11434/v1/models"


def test_base_url_override_applies_to_named_preset(restore_llm_config):
    Config.LLM_PROVIDER = "openai"
    Config.LLM_BASE_URL = "https://proxy.example.com/v1"
    Config.LLM_API_KEY = "key"
    Config.resolve_llm()

    assert Config.LLM_CHAT_URL == "https://proxy.example.com/v1/chat/completions"


def test_api_key_falls_back_to_kiconnect_env(monkeypatch, restore_llm_config):
    monkeypatch.setenv("KICONNECT_API_KEY", "legacy-key")
    Config.LLM_PROVIDER = "kiconnect"
    Config.LLM_BASE_URL = ""
    Config.LLM_API_KEY = ""
    Config.resolve_llm()

    assert Config.LLM_API_KEY == "legacy-key"
    assert Config.KICONNECT_API_KEY == "legacy-key"
