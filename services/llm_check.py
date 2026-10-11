"""Startup check of the AI models — what a model-list answer means for a role.

At every start (and on "Check again") LocalBib asks the connection of every
bound role for its model list: no chat call, no embedding call, no tokens.
This module is the *classification* half (ADR-0006, #209): which connections
to ask, and what their answers mean for each role. The asking is
``llm_client.fetch_model_list``, the keeping of the result is
``routers/llm.py``; the Library Diagnosis reads the same report through
:func:`diagnosis_probes`, so there is one path for both.

Pure: no I/O, no logging, no ``Config`` — the document, the provider presets,
the resolved endpoints (``Config.llm_endpoint(tier)``) and the task table come
in as arguments.

Status per role — codes, the SPA words them (ADR-0018):

``ok``            the connection answered and offers the model
``unreachable``   connection error, timeout, certificate, or a 5xx answer
``auth_failed``   401/403: the key was rejected
``model_missing`` the list does not contain the model
``unchecked``     the provider has no model list (404 & co.) — counts as ok,
                  a provider that cannot be asked is no false alarm
``unbound``       the role is deliberately off

``fast`` without a binding of its own runs on ``reasoning`` and says so
(``fallback``); ``embedding`` never falls back. A connection without a key
(Ollama, LM Studio) is a valid connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import llm_config

STATUSES = ("ok", "unreachable", "auth_failed", "model_missing", "unchecked", "unbound")
#: The statuses that mean "this role does not work right now".
PROBLEMS = frozenset({"unreachable", "auth_failed", "model_missing"})

#: What runs on the embedding role. ``TASK_MODELS`` routes chat calls only;
#: the embedding role serves the semantic search.
EMBEDDING_TASKS = ("semantic_search",)

#: The ``error`` values of a :class:`ModelList`.
FETCH_ERRORS = ("timeout", "connection", "certificate", "failed")


@dataclass(frozen=True)
class ModelList:
    """One answer of a connection's ``/models`` endpoint.

    ``models`` is ``None`` when no list could be read; ``http_status`` is
    ``None`` when there was no HTTP answer at all, ``error`` then says why
    (one of :data:`FETCH_ERRORS`). ``message`` is the first part of the
    failure, for the log only — it never reaches the SPA."""

    models: tuple[str, ...] | None = None
    http_status: int | None = None
    error: str = ""
    message: str = ""


def tasks_by_tier(task_models: dict[str, str]) -> dict[str, list[str]]:
    """The tasks each role serves: the ``TASK_MODELS`` table read backwards,
    plus the embedding role's own. Feeds "these features are off"."""
    out: dict[str, list[str]] = {tier: [] for tier in llm_config.TIERS}
    for task, tier in task_models.items():
        out.setdefault(tier, []).append(task)
    out["embedding"] = list(EMBEDDING_TASKS)
    return out


def offered(model: str, models) -> bool:
    """Whether the list offers ``model``. Ollama lists ``name:latest`` for a
    model configured as ``name`` (and the other way round) — the same model,
    no false alarm."""
    names = set(models or ())
    if model in names:
        return True
    if model.endswith(":latest"):
        return model[: -len(":latest")] in names
    return ":" not in model and f"{model}:latest" in names


def classify(model: str, answer: ModelList) -> tuple[str, str]:
    """``(status, detail_code)`` of a role whose model is ``model``, given its
    connection's answer."""
    if answer.http_status is None:
        if answer.error == "timeout":
            return "unreachable", "timeout"
        if answer.error == "certificate":
            return "unreachable", "certificate"
        if answer.error == "connection":
            return "unreachable", "connection"
        return "unreachable", "failed"
    if answer.http_status in (401, 403):
        return "auth_failed", "key_rejected"
    if answer.http_status >= 500:
        return "unreachable", "server_error"
    if answer.http_status != 200 or not answer.models:
        return "unchecked", "no_model_list"
    if offered(model, answer.models):
        return "ok", "offered"
    return "model_missing", "not_offered"


def _has_own_binding(doc: dict, tier: str, providers: dict) -> bool:
    """Whether ``tier`` is usable on its own binding (not via a fallback)."""
    role = (doc.get("roles") or {}).get(tier) or {}
    conn = llm_config.find_connection(doc, role.get("connection_id") or "")
    model = (role.get("model") or "").strip()
    return bool(conn and model and llm_config.endpoint_for(conn, model, providers))


def targets(endpoints: dict) -> dict[str, tuple[str, str]]:
    """The endpoints to ask, once per connection:
    ``connection_id -> (base_url, api_key)``. ``endpoints`` maps a tier to
    ``Config.llm_endpoint(tier)`` (``None`` = not usable)."""
    out: dict[str, tuple[str, str]] = {}
    for ep in endpoints.values():
        if ep:
            out.setdefault(ep["connection_id"], (ep["base_url"], ep.get("api_key") or ""))
    return out


def report(
    doc: dict,
    providers: dict,
    endpoints: dict,
    answers: dict[str, ModelList],
    task_models: dict[str, str],
) -> dict[str, dict]:
    """One entry per role: ``{status, detail_code, connection_id,
    connection_label, model, fallback, host, http_status, tasks}``.

    ``answers`` maps a connection id to its :class:`ModelList` (see
    :func:`targets`); a connection without an answer counts as unreachable."""
    labels = {c.get("id"): (c.get("label") or c.get("id") or "") for c in doc.get("connections", [])}
    tasks = tasks_by_tier(task_models)
    roles: dict[str, dict] = {}
    for tier in llm_config.TIERS:
        ep = endpoints.get(tier)
        entry = {
            "status": "unbound", "detail_code": "not_bound",
            "connection_id": "", "connection_label": "", "model": "",
            "fallback": False, "host": "", "http_status": None,
            "tasks": list(tasks.get(tier, [])),
        }
        if ep:
            answer = answers.get(ep["connection_id"]) or ModelList(error="failed")
            status, detail = classify(ep["model"], answer)
            entry.update({
                "status": status, "detail_code": detail,
                "connection_id": ep["connection_id"],
                "connection_label": labels.get(ep["connection_id"], ep["connection_id"]),
                "model": ep["model"],
                "fallback": not _has_own_binding(doc, tier, providers),
                "host": urlsplit(ep["base_url"]).netloc or ep["base_url"],
                "http_status": answer.http_status,
            })
        roles[tier] = entry
    return roles


def problem_tiers(roles: dict) -> list[str]:
    """The roles that do not work right now, in tier order."""
    return [t for t in llm_config.TIERS if (roles.get(t) or {}).get("status") in PROBLEMS]


def diagnosis_probes(roles: dict) -> list[dict]:
    """The LLM input of ``library_diagnosis.diagnose`` from the same report:
    one entry per connection for a rejected key (``auth``) or an unreachable
    host (``unreachable``), one per connection and model for a model its list
    does not offer (``model_missing``). Each lists the roles it affects."""
    groups: dict[tuple, dict] = {}
    for tier in problem_tiers(roles):
        r = roles[tier]
        if r["status"] == "model_missing":
            key, status, model = ("model_missing", r["connection_id"], r["model"]), "model_missing", r["model"]
        else:
            status = "auth" if r["status"] == "auth_failed" else "unreachable"
            key, model = (status, r["connection_id"], ""), ""
        entry = groups.setdefault(key, {
            "connection_id": r["connection_id"], "label": r["connection_label"],
            "tiers": [], "status": status, "model": model,
        })
        entry["tiers"].append(tier)
    return list(groups.values())
