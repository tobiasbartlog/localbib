from unittest.mock import patch

import host_services
import webapp

# _CoreLlmApi was relocated from webapp.py into the neutral host_services module
# (Backend-Modularisierung #93); it resolves ``llm_for`` in that module's
# namespace, so the patch target follows it there. webapp._CoreLlmApi is a
# re-export of host_services._CoreLlmApi.


def test_core_llm_api_complete_uses_model_override():
    with patch.object(host_services, "llm_for") as llm_for_mock:
        llm_for_mock.return_value.complete.return_value = "ok"

        result = webapp._CoreLlmApi().complete(
            {"messages": [{"role": "user", "content": "hi"}], "model": "custom-model"}
        )

    assert result == {"content": "ok"}
    llm_for_mock.assert_called_once_with("plugin", model="custom-model")


def test_core_llm_api_complete_without_model_uses_task_routing():
    with patch.object(host_services, "llm_for") as llm_for_mock:
        llm_for_mock.return_value.complete.return_value = "ok"

        webapp._CoreLlmApi().complete({"messages": [{"role": "user", "content": "hi"}]})

    llm_for_mock.assert_called_once_with("plugin", model=None)


def test_core_llm_api_fast_tier_routes_to_the_fast_task(monkeypatch):
    """A plugin states the *kind* of task (``tier="fast"``, #121); the
    tier→model mapping stays in the core's Model Routing table."""
    with patch.object(host_services, "llm_for") as llm_for_mock:
        llm_for_mock.return_value.complete.return_value = "ok"

        webapp._CoreLlmApi().complete(
            {"messages": [{"role": "user", "content": "hi"}], "tier": "fast"}
        )

    llm_for_mock.assert_called_once_with("plugin_fast", model=None)


def test_core_llm_api_unknown_tier_stays_on_the_default_task():
    with patch.object(host_services, "llm_for") as llm_for_mock:
        llm_for_mock.return_value.complete.return_value = "ok"

        webapp._CoreLlmApi().complete(
            {"messages": [{"role": "user", "content": "hi"}], "tier": "turbo"}
        )

    llm_for_mock.assert_called_once_with("plugin", model=None)


def test_plugin_fast_task_resolves_to_the_fast_model():
    from llm_client import TASK_MODELS

    assert TASK_MODELS["plugin_fast"] == "fast"
