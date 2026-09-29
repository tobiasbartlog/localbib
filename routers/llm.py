"""Router: LLM connections and roles — the ``llm.json`` document.

Serves:
  GET  /api/llm/config          the document (keys masked) + per-role status
  PUT  /api/llm/config          replace the document atomically (validated)
  GET  /api/llm/status          per-role readiness, for the SPA outside the settings
  POST /api/llm/models          a connection's model list (role, saved id or draft)
  POST /api/llm/suggest-models  reasoning + fast pick from one connection's list

Thin orchestration (ADR-0006): ``llm_config`` owns the document rules,
``settings_store`` the persistence + reload chain; this router only parses,
calls, persists and shapes the response. Errors are **codes** (ADR-0018) —
the SPA translates them.

Keys never leave the server: GET returns ``has_key`` + ``key_hint``, and a
PUT sends ``api_key: null`` for "unchanged". The one exception is the
model-list probe for a connection the user is still typing — there the key
travels *to* the server, once, in the request body, because the list must be
loadable before the connection is saved (one Save button for the whole tab).
"""

from __future__ import annotations

import logging
from typing import Optional

import requests as http_requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import ca_trust
import llm_config
import services.model_recommender as _recommender
import settings_store
from config import Config
from llm_client import llm_for

router = APIRouter()


class LlmConfigUpdate(BaseModel):
    connections: list[dict] = []
    roles: dict = {}


class ModelsRequest(BaseModel):
    """Which endpoint to probe: a role (``tier``), a saved connection
    (``connection_id``, optionally with an edited ``provider``/``base_url`` —
    the stored key is used), or an unsaved draft (``provider`` + ``base_url``
    + ``api_key``)."""
    tier: Optional[str] = None
    connection_id: Optional[str] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None


# base_url -> model ids. Loading the list is also the connection test, so it
# is fetched live per probe; the cache only spares the settings page a second
# round-trip per role dropdown. Cleared on every save.
_models_cache: dict[str, list[str]] = {}


def _config_payload() -> dict:
    return {**llm_config.mask(Config.LLM_DOCUMENT), "status": Config.llm_status()}


@router.get("/api/llm/config")
async def get_llm_config():
    return _config_payload()


@router.get("/api/llm/status")
async def get_llm_status():
    return Config.llm_status()


@router.put("/api/llm/config")
async def put_llm_config(data: LlmConfigUpdate):
    try:
        doc = llm_config.normalize(
            {"connections": data.connections, "roles": data.roles},
            Config.LLM_PROVIDERS,
            current=Config.LLM_DOCUMENT,
        )
    except llm_config.DocumentError as exc:
        raise HTTPException(status_code=422, detail=exc.detail())

    llm_config.save(Config.LLM_CONFIG_PATH, doc)
    # The document is now the only truth: the flat keys leave the .env so the
    # in-memory migration can never resurrect a stale provider or key.
    settings_store.write_env(
        settings_store.env_path(),
        {key: None for key in llm_config.LEGACY_ENV_KEYS},
    )
    _models_cache.clear()
    await settings_store.reload_runtime(settings_store.env_path())
    return _config_payload()


# ---------------------------------------------------------------------------
# Model list = connection test
# ---------------------------------------------------------------------------

def _fetch_error(exc: Exception) -> dict:
    """An error *code* the SPA can render with advice. Certificate failures
    get their own: they come from the configured house bundle almost every
    time, and without that hint the cause cannot be guessed from the UI."""
    text = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in text or isinstance(exc, http_requests.exceptions.SSLError):
        extra = ca_trust.configured_extra_ca()
        if extra:
            return {"code": "error.llm.models.certBundle", "params": {"bundle": extra}}
        return {"code": "error.llm.models.certProxy", "params": {}}
    if isinstance(exc, http_requests.exceptions.Timeout):
        return {"code": "error.llm.models.timeout", "params": {}}
    if isinstance(exc, http_requests.exceptions.ConnectionError):
        return {"code": "error.llm.models.connection", "params": {}}
    return {"code": "error.llm.models.failed", "params": {"message": text[:200]}}


def fetch_models(base_url: str, api_key: str) -> tuple[list[str], Optional[dict]]:
    """``(models, error)`` for one endpoint. Never raises: a failed probe is a
    result the UI shows next to the connection, not a 500."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return [], {"code": "error.llm.models.noEndpoint", "params": {}}
    if base in _models_cache:
        return list(_models_cache[base]), None
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = http_requests.get(
            f"{base}/models", headers=headers, timeout=10, verify=ca_trust.ca_bundle(),
        )
    except Exception as exc:
        logging.warning("/api/llm/models %s: %s", base, exc)
        return [], _fetch_error(exc)
    if resp.status_code != 200:
        logging.warning("/api/llm/models %s: HTTP %s %s", base, resp.status_code, resp.text[:200])
        return [], {"code": "error.llm.models.http", "params": {"status": resp.status_code}}
    models = [m["id"] for m in resp.json().get("data", []) if m.get("id")]
    _models_cache[base] = list(models)
    return models, None


def _probe_target(req: ModelsRequest) -> tuple[str, str, str]:
    """``(base_url, api_key, current_model)`` of the endpoint to probe.

    A tier resolves through the role (for consumers outside the settings page
    that just want "the reasoning connection's list"). A saved id uses the
    stored key — it never has to travel through the SPA — while an edited
    provider/base URL in the same request is honoured, so the list matches
    what the user is looking at, not what was saved. Anything else is a draft
    and brings its own key."""
    if req.tier:
        ep = Config.llm_endpoint(req.tier)
        if ep is None:
            return "", "", ""
        return ep["base_url"], ep["api_key"], ep["model"]
    if req.connection_id:
        conn = llm_config.find_connection(Config.LLM_DOCUMENT, req.connection_id)
        if conn is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "error.llm.connectionUnknown",
                        "params": {"tier": "", "id": req.connection_id}},
            )
        edited = {
            "provider": (req.provider or conn.get("provider") or "").strip().lower(),
            "base_url": req.base_url if req.base_url is not None else conn.get("base_url") or "",
        }
        return llm_config.connection_base_url(edited, Config.LLM_PROVIDERS), conn.get("api_key") or "", ""
    draft = {"provider": (req.provider or "").strip().lower(), "base_url": req.base_url or ""}
    return llm_config.connection_base_url(draft, Config.LLM_PROVIDERS), (req.api_key or "").strip(), ""


@router.post("/api/llm/models")
async def post_llm_models(req: ModelsRequest):
    base, key, current = _probe_target(req)
    models, error = fetch_models(base, key)
    return {"models": models, "error": error, "current": current}


@router.post("/api/llm/suggest-models")
async def suggest_models(req: ModelsRequest):
    """Recommend a reasoning + a fast model from ONE connection's list.

    Asks the reasoning role's model to pick the two best (source="llm"); on
    any failure — no reasoning role, unparseable answer, invalid ids — falls
    back to the offline name heuristic (source="heuristic"). Persists
    nothing; the SPA pre-fills the dropdowns and the user saves explicitly.
    Suggesting *across* connections would need costs and access the app does
    not know, so the target is always the one the request names."""
    base, key, _ = _probe_target(req)
    models, error = fetch_models(base, key)
    if not models:
        return {"suggestion": None, "error": error or {"code": "error.llm.models.noEndpoint", "params": {}}}

    if Config.llm_ready("reasoning"):
        try:
            llm = llm_for("model_suggest")
            content = llm.complete(
                [{"role": "user", "content": _recommender.build_suggest_prompt(models)}],
                timeout=60,
            )
            parsed = _recommender.parse_llm_suggestion(content, models)
            if parsed:
                return {"suggestion": parsed}
            logging.info("suggest-models: LLM-Antwort unbrauchbar, nutze Heuristik")
        except Exception as exc:
            logging.warning("suggest-models: LLM-Call fehlgeschlagen (%s), nutze Heuristik", exc)

    return {"suggestion": _recommender.heuristic_suggest(models)}
