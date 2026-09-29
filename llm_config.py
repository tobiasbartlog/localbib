"""The LLM connection document — the one structured LLM setting.

Named connections (``{id, label, provider, base_url, api_key}``) and three
roles (``reasoning`` / ``fast`` / ``embedding``), each bound to one
``(connection_id, model)`` pair. The document lives in ``llm.json`` next to the
``.env`` — JSON cannot live *in* the ``.env``: python-dotenv truncates an
unquoted value at ``#`` and cannot parse a double-quoted one with inner quotes,
and ``routers/settings.py`` has a hand-written line parser with rules of its
own. Two parsers with different quoting rules over one secret is how keys get
lost.

Leaf module on purpose: stdlib only, no project imports, so ``config.py`` can
call it while loading and stays import-cycle-free. Pure except ``load``/``save``
(the router is the only caller of ``save``).

Roles: ``fast`` falls back to ``reasoning`` when unbound (the historical
``LLM_MODEL_FAST`` empty -> ``LLM_MODEL`` rule); ``embedding`` never falls back
— unbound means the semantic features are off, as ``LLM_EMBED_MODEL`` empty
meant before. A key may be empty: local providers (Ollama, LM Studio) have
none, and "no key" must not read as "no LLM".
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile

TIERS = ("reasoning", "fast", "embedding")
DOCUMENT_VERSION = 1

# The flat .env keys the document replaces. Read once for the in-memory
# migration (``from_legacy_env``), removed from the .env on the first save of
# the document — two truths for the same setting is the drift that
# ``Config.reload_from_env`` was introduced to end.
LEGACY_ENV_KEYS = (
    "LLM_PROVIDER",
    "LLM_API_KEY",
    "KICONNECT_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_MODEL_FAST",
    "LLM_EMBED_MODEL",
    "LLM_EMBED_URL",
)

LEGACY_CONNECTION_ID = "default"
LEGACY_EMBED_CONNECTION_ID = "embedding"


class DocumentError(ValueError):
    """A document the app must not persist. ``code`` is an i18n error code
    (the backend never translates, ADR-0018); ``params`` fill its placeholders."""

    def __init__(self, code: str, **params):
        super().__init__(code)
        self.code = code
        self.params = params

    def detail(self) -> dict:
        return {"code": self.code, "params": self.params}


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def empty_document() -> dict:
    return {
        "version": DOCUMENT_VERSION,
        "connections": [],
        "roles": {tier: {"connection_id": "", "model": ""} for tier in TIERS},
    }


def new_connection_id() -> str:
    """Short random slug. Never shown to the user (the label is), so it needs
    no meaning — only uniqueness within one document."""
    return secrets.token_hex(4)


def key_hint(api_key: str) -> str:
    """What the UI may show instead of a key: prefix + last four characters.
    Short keys give only the tail so the hint never reproduces most of them."""
    key = (api_key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "…" + key[-2:]
    return key[:3] + "…" + key[-4:]


def connection_base_url(conn: dict, providers: dict) -> str:
    """The connection's base URL: its own override, else the preset's."""
    own = (conn.get("base_url") or "").strip().rstrip("/")
    if own:
        return own
    preset = providers.get(conn.get("provider") or "", {})
    return (preset.get("base_url") or "").rstrip("/")


def strip_embeddings_suffix(url: str) -> str:
    """``.../v1/embeddings`` (the historical ``LLM_EMBED_URL`` shape) -> ``.../v1``."""
    base = (url or "").strip().rstrip("/")
    if base.endswith("/embeddings"):
        base = base[: -len("/embeddings")]
    return base


# ---------------------------------------------------------------------------
# Validation / normalisation
# ---------------------------------------------------------------------------

def _current_keys(current: dict | None) -> dict:
    return {
        c.get("id"): c.get("api_key") or ""
        for c in (current or {}).get("connections", [])
        if c.get("id")
    }


def normalize(doc: dict, providers: dict, current: dict | None = None) -> dict:
    """Validate an incoming document and return its canonical form.

    ``api_key`` handling is the masking contract with the UI: ``None`` (or a
    missing field) means "unchanged — keep the key stored under this id",
    ``""`` means "remove the key", any other string sets it. GET never returns
    a key, so the SPA never has one to send back.

    Raises :class:`DocumentError` for anything that must not be persisted: an
    unknown provider, a custom connection without a base URL, a duplicate id,
    or a role bound to a connection that is not in the document.
    """
    if not isinstance(doc, dict):
        raise DocumentError("error.llm.documentInvalid")
    keys_before = _current_keys(current)
    connections: list[dict] = []
    seen: set[str] = set()
    for raw in doc.get("connections") or []:
        if not isinstance(raw, dict):
            raise DocumentError("error.llm.documentInvalid")
        provider = str(raw.get("provider") or "").strip().lower()
        if provider not in providers:
            raise DocumentError("error.llm.providerUnknown", provider=provider)
        cid = str(raw.get("id") or "").strip() or new_connection_id()
        if cid in seen:
            raise DocumentError("error.llm.connectionDuplicate", id=cid)
        seen.add(cid)
        base_url = str(raw.get("base_url") or "").strip().rstrip("/")
        if not base_url and not providers[provider].get("base_url"):
            raise DocumentError("error.llm.baseUrlRequired", id=cid)
        label = str(raw.get("label") or "").strip() or providers[provider].get("label", provider)
        api_key = raw.get("api_key", None)
        if api_key is None:
            api_key = keys_before.get(cid, "")
        connections.append({
            "id": cid,
            "label": label,
            "provider": provider,
            "base_url": base_url,
            "api_key": str(api_key).strip(),
        })

    roles: dict = {}
    raw_roles = doc.get("roles") or {}
    if not isinstance(raw_roles, dict):
        raise DocumentError("error.llm.documentInvalid")
    for tier in TIERS:
        raw = raw_roles.get(tier) or {}
        if not isinstance(raw, dict):
            raise DocumentError("error.llm.documentInvalid")
        cid = str(raw.get("connection_id") or "").strip()
        if cid and cid not in seen:
            raise DocumentError("error.llm.connectionUnknown", tier=tier, id=cid)
        roles[tier] = {"connection_id": cid, "model": str(raw.get("model") or "").strip()}

    return {"version": DOCUMENT_VERSION, "connections": connections, "roles": roles}


def mask(doc: dict) -> dict:
    """The wire form: every ``api_key`` replaced by ``has_key`` + ``key_hint``."""
    return {
        "version": doc.get("version", DOCUMENT_VERSION),
        "connections": [
            {
                "id": c["id"],
                "label": c["label"],
                "provider": c["provider"],
                "base_url": c["base_url"],
                "has_key": bool(c.get("api_key")),
                "key_hint": key_hint(c.get("api_key") or ""),
            }
            for c in doc.get("connections", [])
        ],
        "roles": {tier: dict(doc.get("roles", {}).get(tier) or {"connection_id": "", "model": ""})
                  for tier in TIERS},
    }


# ---------------------------------------------------------------------------
# Migration from the flat .env keys
# ---------------------------------------------------------------------------

def from_legacy_env(
    *,
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    model_fast: str,
    embed_model: str,
    embed_url: str,
    providers: dict,
) -> dict | None:
    """Build the document an old ``.env`` describes, or ``None`` for a fresh
    install (nothing set at all).

    One connection ``default`` carries provider, key and base URL; reasoning and
    fast bind to it (fast stays unbound when ``LLM_MODEL_FAST`` was empty — the
    fallback rule does the rest). A historical ``LLM_EMBED_URL`` becomes a
    second connection ``embedding`` carrying the same key — that is what the
    old code did: send the chat key to the embedding endpoint.
    """
    values = (provider, base_url, api_key, model, model_fast, embed_model, embed_url)
    if not any((v or "").strip() for v in values):
        return None
    provider = (provider or "").strip().lower()
    if provider not in providers:
        provider = "custom"
    doc = empty_document()
    doc["connections"].append({
        "id": LEGACY_CONNECTION_ID,
        "label": providers[provider].get("label", provider),
        "provider": provider,
        "base_url": (base_url or "").strip().rstrip("/"),
        "api_key": (api_key or "").strip(),
    })
    doc["roles"]["reasoning"] = {"connection_id": LEGACY_CONNECTION_ID, "model": (model or "").strip()}
    if (model_fast or "").strip():
        doc["roles"]["fast"] = {"connection_id": LEGACY_CONNECTION_ID, "model": model_fast.strip()}
    embed_model = (embed_model or "").strip()
    embed_base = strip_embeddings_suffix(embed_url)
    if embed_model and embed_base:
        doc["connections"].append({
            "id": LEGACY_EMBED_CONNECTION_ID,
            "label": "Embedding endpoint",
            "provider": "custom",
            "base_url": embed_base,
            "api_key": (api_key or "").strip(),
        })
        doc["roles"]["embedding"] = {"connection_id": LEGACY_EMBED_CONNECTION_ID, "model": embed_model}
    elif embed_model:
        doc["roles"]["embedding"] = {"connection_id": LEGACY_CONNECTION_ID, "model": embed_model}
    return doc


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def find_connection(doc: dict, connection_id: str) -> dict | None:
    for c in doc.get("connections", []):
        if c.get("id") == connection_id:
            return c
    return None


def endpoint_for(conn: dict, model: str, providers: dict) -> dict | None:
    """The resolved endpoint of ``conn`` for ``model``; ``None`` without a base
    URL. The key may be empty (local providers)."""
    base = connection_base_url(conn, providers)
    if not base:
        return None
    return {
        "connection_id": conn.get("id", ""),
        "provider": conn.get("provider", ""),
        "base_url": base,
        "api_key": conn.get("api_key") or "",
        "model": model,
        "chat_url": f"{base}/chat/completions",
        "models_url": f"{base}/models",
        "embed_url": f"{base}/embeddings",
    }


def resolve_role(doc: dict, tier: str, providers: dict) -> dict | None:
    """The endpoint a tier runs on, or ``None`` when the tier is not usable:
    no connection bound, connection gone, no model, no base URL. ``fast``
    falls back to ``reasoning``; ``embedding`` never falls back."""
    role = (doc.get("roles") or {}).get(tier) or {}
    conn = find_connection(doc, role.get("connection_id") or "")
    model = (role.get("model") or "").strip()
    if conn and model:
        ep = endpoint_for(conn, model, providers)
        if ep:
            return ep
    if tier == "fast":
        return resolve_role(doc, "reasoning", providers)
    return None


def status(doc: dict, providers: dict) -> dict:
    """What the SPA needs to know outside the settings page: per-tier readiness
    and whether any connection exists at all (the onboarding's question)."""
    out = {tier: resolve_role(doc, tier, providers) is not None for tier in TIERS}
    out["connections"] = len(doc.get("connections", []))
    return out


# ---------------------------------------------------------------------------
# File I/O (save is called by the router only)
# ---------------------------------------------------------------------------

def load(path: str) -> dict | None:
    """The stored document, or ``None`` when there is none (or it is
    unreadable — a corrupt file must not take the app down; the settings page
    will show an empty configuration instead)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("connections"), list):
        return None
    return data


def save(path: str, doc: dict) -> None:
    """Atomic write (temp file + replace) so a crash mid-write never leaves a
    half document — the file holds every key the user has."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".llm-", suffix=".json.tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
