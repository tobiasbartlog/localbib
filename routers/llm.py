"""Router: LLM connections and roles — the ``llm.json`` document.

Serves:
  GET  /api/llm/config          the document (keys masked) + per-role status
  PUT  /api/llm/config          replace the document atomically (validated)
  GET  /api/llm/status          per-role readiness, for the SPA outside the settings
  POST /api/llm/models          a connection's model list (role, saved id or draft)
  POST /api/llm/suggest-models  reasoning + fast pick from one connection's list
  GET  /api/llm/startup-check   the last Startup check (#209) — status per role
  POST /api/llm/startup-check   check again; replaces the result

Thin orchestration (ADR-0006): ``llm_config`` owns the document rules,
``settings_store`` the persistence + reload chain, ``services.llm_check`` the
meaning of a model-list answer; this router only parses, calls, persists and
shapes the response. Errors and statuses are **codes** (ADR-0018) — the SPA
translates them.

Keys never leave the server: GET returns ``has_key`` + ``key_hint``, and a
PUT sends ``api_key: null`` for "unchanged". The one exception is the
model-list probe for a connection the user is still typing — there the key
travels *to* the server, once, in the request body, because the list must be
loadable before the connection is saved (one Save button for the whole tab).
"""

from __future__ import annotations

import copy
import hashlib
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import ca_trust
import llm_client
import llm_config
import services.model_recommender as _recommender
import settings_store
from config import Config
from llm_client import llm_for
from services import llm_check

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


# (base_url, sha256(api_key)) -> model ids. Loading the list is also the
# connection test, so it is fetched live per probe; the cache only spares the
# settings page a second round-trip per role dropdown. Cleared on every save.
_models_cache: dict[tuple[str, str], list[str]] = {}


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

def _fetch_error(answer: llm_check.ModelList) -> dict:
    """An error *code* the SPA can render with advice. Certificate failures
    get their own: they come from the configured house bundle almost every
    time, and without that hint the cause cannot be guessed from the UI."""
    if answer.error == "certificate":
        extra = ca_trust.configured_extra_ca()
        if extra:
            return {"code": "error.llm.models.certBundle", "params": {"bundle": extra}}
        return {"code": "error.llm.models.certProxy", "params": {}}
    if answer.error == "timeout":
        return {"code": "error.llm.models.timeout", "params": {}}
    if answer.error == "connection":
        return {"code": "error.llm.models.connection", "params": {}}
    return {"code": "error.llm.models.failed", "params": {"message": answer.message[:200]}}


def fetch_models(base_url: str, api_key: str) -> tuple[list[str], Optional[dict]]:
    """``(models, error)`` for one endpoint. Never raises: a failed probe is a
    result the UI shows next to the connection, not a 500. The request itself
    is ``llm_client.fetch_model_list`` — the one model-list path (#209)."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return [], {"code": "error.llm.models.noEndpoint", "params": {}}
    # The key is part of the cache key: a different (e.g. mistyped) key against
    # the same endpoint must hit the network again and surface its 401.
    cache_key = (base, hashlib.sha256((api_key or "").encode("utf-8")).hexdigest())
    if cache_key in _models_cache:
        return list(_models_cache[cache_key]), None
    answer = llm_client.fetch_model_list(base, api_key, timeout=llm_client.MODEL_LIST_TIMEOUT)
    if answer.http_status is None:
        logging.warning("/api/llm/models %s: %s", base, answer.message)
        return [], _fetch_error(answer)
    if answer.http_status != 200:
        logging.warning("/api/llm/models %s: HTTP %s %s", base, answer.http_status, answer.message)
        return [], {"code": "error.llm.models.http", "params": {"status": answer.http_status}}
    if answer.models is None:
        logging.warning("/api/llm/models %s: unreadable answer %s", base, answer.message)
        return [], _fetch_error(answer)
    models = list(answer.models)
    _models_cache[cache_key] = list(models)
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
def post_llm_models(req: ModelsRequest):
    base, key, current = _probe_target(req)
    models, error = fetch_models(base, key)
    return {"models": models, "error": error, "current": current}


@router.post("/api/llm/suggest-models")
def suggest_models(req: ModelsRequest):
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


# ---------------------------------------------------------------------------
# Startup check (#209)
# ---------------------------------------------------------------------------
# Every start asks each bound role's connection for its model list, in the
# background — the start does not wait (webapp's startup hook calls
# start_startup_check). The result waits here for the SPA: the popup after
# the boot, the hint at the top of AI models. "Check again" (POST) replaces
# it. A generation counter makes the newest run win: an older run that ends
# later never overwrites a newer result.

#: Run the check at startup. The test suite switches it off (conftest), so no
#: test reaches a network while the app boots; a test of the boot itself
#: switches it back on.
CHECK_ON_STARTUP = True

_check_lock = threading.Lock()
_check_state: dict = {"generation": 0, "running": False, "checked_at": None, "roles": {}}


def _check_payload() -> dict:
    with _check_lock:
        return {
            "checked_at": _check_state["checked_at"],
            "running": _check_state["running"],
            "roles": copy.deepcopy(_check_state["roles"]),
            # What the start itself had to correct (e.g. an ignored base
            # folder): codes, shown by the same popup.
            "notices": copy.deepcopy(Config.STARTUP_NOTICES),
        }


def _begin_check() -> int:
    with _check_lock:
        _check_state["generation"] += 1
        _check_state["running"] = True
        return _check_state["generation"]


def _run_check(generation: int) -> None:
    """One run; stores its result unless a newer run has started meanwhile."""
    try:
        roles = llm_client.check_roles()
    except Exception as exc:  # noqa: BLE001 - the check must never take the app down
        logging.warning("Startprüfung der KI-Modelle fehlgeschlagen: %s", exc)
        roles = None
    with _check_lock:
        if generation != _check_state["generation"]:
            return
        _check_state["running"] = False
        if roles is None:
            return
        _check_state["roles"] = roles
        _check_state["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for tier in llm_check.problem_tiers(roles):
        r = roles[tier]
        logging.warning("Startprüfung: Rolle %s (%s, Modell %s): %s (%s)",
                        tier, r["connection_label"], r["model"], r["status"], r["detail_code"])


def start_startup_check() -> Optional[threading.Thread]:
    """Start the check in a background thread and return at once."""
    if not CHECK_ON_STARTUP:
        return None
    generation = _begin_check()
    thread = threading.Thread(target=_run_check, args=(generation,),
                              name="llm-startup-check", daemon=True)
    thread.start()
    return thread


@router.get("/api/llm/startup-check")
async def get_startup_check():
    """``{checked_at, running, roles: {tier: {status, connection_label, model,
    detail_code, ...}}}``; ``checked_at`` is ``None`` until a run finished."""
    return _check_payload()


@router.post("/api/llm/startup-check")
async def post_startup_check():
    generation = _begin_check()
    await run_in_threadpool(_run_check, generation)
    return _check_payload()
