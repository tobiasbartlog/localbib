"""Test fakes for Add-on authors: run an Add-on's tests without the LocalBib core.

Every fake satisfies the matching Protocol in ``plugin_api`` and is stdlib-only.
:func:`make_api` assembles a ``PluginApi`` from a permission set exactly the way
the core does: a gated service whose Berechtigung is not declared is ``None``,
and ``settings.core()`` answers only with ``settings.core`` declared.

    from plugin_api import load_manifest
    from plugin_api.testing import make_api

    manifest = load_manifest("plugin.json")
    api = make_api(manifest.permissions, llm=DummyLlm("Hello"))
    await plugin.activate(api)
    assert api.ui.nav_items
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence, Union

from plugin_api import (
    API_VERSION,
    COLLECTION_EVENT,
    COLLECTION_EVENT_ACTIONS,
    CoreSettings,
    NavItem,
    Permission,
    setting_value,
)
from plugin_api.manifest import parse_permissions

__all__ = [
    "InMemoryLibrary",
    "DummyLlm",
    "InMemorySettings",
    "NullFiles",
    "TempSqliteStorage",
    "RecordingRegistry",
    "FakePluginApi",
    "make_api",
]


# =============================================================================
# Library
# =============================================================================

class InMemoryLibrary:
    """A ``LibraryApi`` over a list of reference dicts.

    Each reference needs a ``citekey``; ``title``, ``abstract``, ``full_text``,
    ``authors``, ``year`` are optional. ``full_text`` absent means ``None`` —
    as in the real core, where many Items have no extractable text.

    ``create_by_doi`` works like the core's DOI intake minus the network:
    dedup by the normalised DOI, a new reference otherwise (no PDF, no
    enrichment), each call recorded in ``created``. ``write_allowed`` mirrors
    ``library.write``: :func:`make_api` sets it from the declared permissions,
    and while it is False ``create_by_doi`` raises ``PermissionError``.

    A reference may carry ``passages``: a list of ``{"text", "page_start"?,
    "page_end"?, "printed_page_start"?, "printed_page_end"?}`` (missing
    pages are ``None``). :meth:`search_passages` ranks them by how many
    query words they contain, :meth:`get_passages` pages through them
    (contract 2.5.0).

    ``collections`` seeds Collections the way the core holds them: each dict
    takes ``name`` and optionally ``id``, ``description``, ``refs`` (Cite
    Keys), ``archived`` / ``archived_at``, ``deleted`` / ``deleted_at``,
    ``created_at``, ``updated_at``. As in the core, a Collection only ever
    holds Items of the library: a ref without a reference is dropped. The
    test helpers :meth:`add_collection`, :meth:`rename_collection`,
    :meth:`set_collection_description`, :meth:`archive_collection`,
    :meth:`delete_collection` and :meth:`set_collection_refs` change them and
    fire the same ``on_change`` events the core fires.
    """

    def __init__(
        self,
        references: Iterable[dict] = (),
        projects: Iterable[dict] = (),
        *,
        collections: Iterable[dict] = (),
        write_allowed: bool = True,
        source: str = "addon",
    ) -> None:
        self._refs: dict[str, dict] = {r["citekey"]: dict(r) for r in references}
        self._projects = [dict(p) for p in projects]
        self._collections: dict[int, dict] = {}
        self._collection_ids = itertools.count(1)
        for seed in collections:
            self._store_collection(seed)
        self._listeners: list[Callable[[dict], None]] = []
        self.write_allowed = write_allowed
        self.source = source
        self.created: list[dict] = []
        self._ids = itertools.count(1)

    def _reference_wire(self, ref: dict) -> dict:
        """The core's answer shape (contract 2.3): a seeded reference keeps
        what it has, missing keys get defaults — ``id`` stable per Cite Key,
        ``has_pdf`` False, ``volume``/``issue``/``pages`` None."""
        out = dict(ref)
        if out.get("id") is None:
            out["id"] = list(self._refs).index(ref["citekey"]) + 1
        out.setdefault("has_pdf", False)
        for key in ("volume", "issue", "pages"):
            out.setdefault(key, None)
        return out

    def get_reference(self, citekey: str) -> Optional[dict]:
        ref = self._refs.get(citekey)
        return self._reference_wire(ref) if ref is not None else None

    def search_references(self, query: str, limit: int = 20) -> list[dict]:
        q = query.lower()
        hits = [
            self._reference_wire(r) for r in self._refs.values()
            if q in " ".join(str(r.get(k, "")) for k in ("citekey", "title", "abstract")).lower()
        ]
        return hits[:limit]

    def get_abstract(self, citekey: str) -> Optional[str]:
        return self._refs.get(citekey, {}).get("abstract")

    def get_full_text(self, citekey: str) -> Optional[str]:
        return self._refs.get(citekey, {}).get("full_text")

    def export_bibtex(self) -> str:
        entries = [self._bib_entry(key, ref) for key, ref in self._refs.items()]
        return "\n\n".join(entries) + ("\n" if entries else "")

    @staticmethod
    def _bib_entry(key: str, ref: dict) -> str:
        fields = [f"  title = {{{ref.get('title', '')}}}"]
        if ref.get("year"):
            fields.append(f"  year = {{{ref['year']}}}")
        return "@article{" + key + ",\n" + ",\n".join(fields) + "\n}"

    # --- Passages (contract 2.5) -------------------------------------------
    @staticmethod
    def _passage_wire(passage: dict) -> dict:
        start = passage.get("page_start")
        return {
            "text": passage.get("text") or "",
            "page_start": start,
            "page_end": passage.get("page_end", start),
            "printed_page_start": passage.get("printed_page_start"),
            "printed_page_end": passage.get("printed_page_end", passage.get("printed_page_start")),
        }

    def search_passages(self, query: str, limit: int = 10) -> list[dict]:
        words = [w for w in re.findall(r"\w+", (query or "").lower()) if w]
        if not words:
            return []
        scored = []
        for ref in self._refs.values():
            for passage in ref.get("passages") or ():
                text = (passage.get("text") or "").lower()
                score = sum(text.count(w) for w in words)
                if score:
                    scored.append((score, ref, passage))
        scored.sort(key=lambda item: -item[0])
        return [
            {"citekey": ref["citekey"], "title": ref.get("title") or "", "year": ref.get("year"),
             **self._passage_wire(p), "score": float(score)}
            for score, ref, p in scored[:max(1, limit)]
        ]

    def get_passages(self, citekey: str, offset: int = 0, limit: int = 20) -> Optional[dict]:
        ref = self._refs.get(citekey)
        if ref is None:
            return None
        passages = list(ref.get("passages") or ())
        offset, limit = max(0, offset), max(1, limit)
        return {
            "citekey": citekey,
            "total": len(passages),
            "passages": [
                {"index": offset + i, **self._passage_wire(p)}
                for i, p in enumerate(passages[offset:offset + limit])
            ],
        }

    # --- Collections (contract 2.1) ----------------------------------------
    def list_collections(self) -> list[dict]:
        ordered = sorted(self._collections.values(), key=lambda c: (c["name"].casefold(), c["id"]))
        return [self._collection_wire(c) for c in ordered]

    def get_collection(self, collection_id: int) -> Optional[dict]:
        found = self._collections.get(collection_id)
        return self._collection_wire(found) if found else None

    def get_collection_refs(self, collection_id: int) -> list[str]:
        return list(self._live_refs(collection_id))

    def export_collection_bibtex(self, collection_id: int) -> str:
        # Like the core: one entry per Item, a lone newline when there is none.
        keys = self._live_refs(collection_id)
        return "\n\n".join(self._bib_entry(k, self._refs[k]) for k in keys) + "\n"

    def get_core_projects(self) -> list[dict]:
        return [dict(p) for p in self._projects]

    def create_by_doi(
        self,
        doi: str,
        *,
        title: str = "",
        authors: Union[list[str], str, None] = None,
        year: Optional[int] = None,
        journal: str = "",
        abstract: str = "",
    ) -> dict:
        if not self.write_allowed:
            raise PermissionError("create_by_doi needs the library.write permission")
        norm = _normalize_doi(doi)
        if not norm:
            raise ValueError("empty DOI")
        for ref in self._refs.values():
            if _normalize_doi(str(ref.get("doi") or "")) == norm:
                return {"doi": norm, "created": False, "paper_id": ref.get("id"),
                        "citekey": ref["citekey"], "pdf": "fetched" if ref.get("full_text") else "none"}
        paper_id = next(self._ids) + len(self._refs)
        citekey = f"doi{paper_id}"
        joined = authors if isinstance(authors, str) else "; ".join(a for a in (authors or []) if a)
        ref = {"citekey": citekey, "id": paper_id, "doi": norm, "title": title,
               "authors": joined, "year": year, "journal": journal, "abstract": abstract,
               "import_source": self.source}
        self.created.append(dict(ref))
        self.add(ref)
        return {"doi": norm, "created": True, "paper_id": paper_id, "citekey": citekey, "pdf": "none"}

    def on_change(self, callback: Callable[[dict], None]) -> Callable[[], None]:
        self._listeners.append(callback)

        def unsubscribe() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return unsubscribe

    # --- test helpers ------------------------------------------------------
    def add(self, reference: dict) -> None:
        """Add or replace a reference and notify subscribers."""
        self._refs[reference["citekey"]] = dict(reference)
        self.emit({"type": "changed", "citekey": reference["citekey"]})

    def add_collection(self, name: str, *, refs: Iterable[str] = (), description: str = "") -> int:
        """Create a Collection (``created``, then ``members_changed`` when it
        starts with Items); returns its id."""
        stored = self._store_collection({"name": name, "description": description, "refs": list(refs)})
        self.emit_collection("created", stored["id"])
        if self._live_refs(stored["id"]):
            self.emit_collection("members_changed", stored["id"])
        return stored["id"]

    def rename_collection(self, collection_id: int, name: str) -> None:
        self._change(collection_id, "renamed", name=name)

    def set_collection_description(self, collection_id: int, description: str) -> None:
        self._change(collection_id, "updated", description=description)

    def archive_collection(self, collection_id: int, archived: bool = True) -> None:
        """Archive (``archived``) or restore (``restored``) a Collection."""
        if archived:
            self._change(collection_id, "archived", archived_at=_now())
        else:
            self._change(collection_id, "restored", archived_at=None)

    def delete_collection(self, collection_id: int) -> None:
        """Tombstone it: still readable here, membership frozen (``deleted``)."""
        self._change(collection_id, "deleted", deleted_at=_now())

    def set_collection_refs(self, collection_id: int, refs: Iterable[str]) -> None:
        """Replace the membership (``members_changed``)."""
        self._change(collection_id, "members_changed", refs=list(dict.fromkeys(refs)))

    def emit_collection(self, action: str, collection_id: int) -> None:
        """Fire one Collection event exactly as the core shapes it."""
        if action not in COLLECTION_EVENT_ACTIONS:
            raise ValueError(f"unknown collection action: {action!r}")
        self.emit({"type": COLLECTION_EVENT, "action": action, "collection_id": collection_id})

    def _store_collection(self, seed: dict) -> dict:
        cid = seed.get("id")
        if cid is None:
            cid = next(self._collection_ids)
            while cid in self._collections:
                cid = next(self._collection_ids)
        now = _now()
        archived_at = seed.get("archived_at") or (now if seed.get("archived") else None)
        deleted_at = seed.get("deleted_at") or (now if seed.get("deleted") else None)
        stored = {
            "id": int(cid),
            "name": seed["name"],
            "description": seed.get("description") or "",
            "archived_at": archived_at,
            "deleted_at": deleted_at,
            "created_at": seed.get("created_at") or now,
            "updated_at": seed.get("updated_at") or now,
            "refs": list(dict.fromkeys(seed.get("refs") or [])),
        }
        self._collections[stored["id"]] = stored
        return stored

    def _change(self, collection_id: int, action: str, **values: Any) -> None:
        stored = self._collections[collection_id]
        stored.update(values)
        stored["updated_at"] = _now()
        self.emit_collection(action, collection_id)

    def _live_refs(self, collection_id: int) -> list[str]:
        stored = self._collections.get(collection_id)
        return [k for k in stored["refs"] if k in self._refs] if stored else []

    def _collection_wire(self, stored: dict) -> dict:
        return {
            "id": stored["id"],
            "name": stored["name"],
            "description": stored["description"],
            "archived": stored["archived_at"] is not None,
            "archived_at": stored["archived_at"],
            "deleted": stored["deleted_at"] is not None,
            "deleted_at": stored["deleted_at"],
            "created_at": stored["created_at"],
            "updated_at": stored["updated_at"],
            "item_count": len(self._live_refs(stored["id"])),
        }

    def emit(self, event: dict) -> None:
        for callback in list(self._listeners):
            callback(event)

    @property
    def listener_count(self) -> int:
        return len(self._listeners)


def _now() -> str:
    """A timestamp in the core's form (SQLite ``CURRENT_TIMESTAMP``, UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# =============================================================================
# LLM
# =============================================================================

class DummyLlm:
    """An ``LlmApi`` with canned answers.

    ``responses`` is one string (always returned), a sequence (returned in
    order, the last one repeating) or a callable ``request -> str``. Every
    request is recorded in ``requests``. Embeddings are off unless
    ``embeddings=True``: then ``embed`` returns deterministic unit vectors of
    length ``dimensions`` derived from each text's hash; off, it raises
    ``NotImplementedError`` like a core without an embeddings endpoint.
    """

    def __init__(
        self,
        responses: Union[str, Sequence[str], Callable[[dict], str]] = "",
        *,
        embeddings: bool = False,
        dimensions: int = 8,
        model: str = "dummy-embed",
    ) -> None:
        self._responses = responses
        self._counter = itertools.count()
        self._embeddings = embeddings
        self._dimensions = dimensions
        self._model = model
        self.requests: list[dict] = []
        self.embedded: list[list[str]] = []

    def complete(self, request: dict) -> dict:
        self.requests.append(request)
        r = self._responses
        if callable(r):
            content = r(request)
        elif isinstance(r, str):
            content = r
        else:
            i = next(self._counter)
            content = r[min(i, len(r) - 1)] if r else ""
        return {"content": content}

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._embeddings:
            raise NotImplementedError("no embeddings endpoint configured")
        self.embedded.append(list(texts))
        return [self._vector(t) for t in texts]

    def embed_model(self) -> str:
        return self._model

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [(digest[i % len(digest)] - 127.5) for i in range(self._dimensions)]
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]


# =============================================================================
# Settings
# =============================================================================

class InMemorySettings:
    """A ``SettingsApi``: a dict for the plugin's own keys plus the core set.

    ``core_settings`` is what ``core()`` returns; :func:`make_api` passes
    ``None`` when ``settings.core`` is not declared.
    """

    def __init__(self, values: Optional[dict] = None, core_settings: Optional[CoreSettings] = None) -> None:
        # Seeds follow the same value rule as ``set`` (and the core's load).
        self.values: dict[str, Any] = {k: setting_value(v) for k, v in (values or {}).items()}
        self._core = core_settings

    def get(self, key: str) -> Optional[Any]:
        return self.values.get(key)

    def set(self, key: str, value: Any) -> None:
        # The core's rule (ADD-4): str/bool/None as is, int/float as str,
        # anything else TypeError.
        self.values[key] = setting_value(value)

    def core(self) -> Optional[CoreSettings]:
        return self._core


#: A plausible default for ``settings.core`` in tests.
DEFAULT_CORE_SETTINGS = CoreSettings(
    mailto="", openalex_api_key="", ui_language="en", base_dir=""
)


# =============================================================================
# Files and storage
# =============================================================================

class NullFiles:
    """A ``FilesApi`` that never fires; records what was watched.

    Call :meth:`fire` to simulate a file event for every watcher of a folder.
    """

    def __init__(self) -> None:
        self.watched: dict[str, list[Callable[[dict], None]]] = {}

    def watch(self, directory: str, callback: Callable[[dict], None]) -> Callable[[], None]:
        self.watched.setdefault(directory, []).append(callback)

        def stop() -> None:
            callbacks = self.watched.get(directory, [])
            if callback in callbacks:
                callbacks.remove(callback)

        return stop

    def fire(self, directory: str, event: dict) -> None:
        for callback in list(self.watched.get(directory, [])):
            callback(event)


_DB_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", re.IGNORECASE)


def _normalize_doi(doi: str) -> str:
    """The core's DOI identity: no resolver prefix, trimmed, lower case."""
    return _DOI_PREFIX.sub("", (doi or "").strip()).strip().lower()


class TempSqliteStorage:
    """A ``StorageApi`` whose databases are SQLite files in a temp folder.

    Pass ``directory`` (e.g. pytest's ``tmp_path``) to control the location;
    otherwise a private temp folder is created and removed by :meth:`close`.
    """

    def __init__(self, directory: Union[str, Path, None] = None) -> None:
        self._owned = directory is None
        self.directory = Path(directory) if directory is not None else Path(
            tempfile.mkdtemp(prefix="localbib-addon-")
        )
        self._connections: list[sqlite3.Connection] = []

    def open_plugin_db(self, name: str) -> sqlite3.Connection:
        if not _DB_NAME.match(name) or ".." in name:
            raise ValueError(f"invalid database name: {name!r}")
        conn = sqlite3.connect(self.directory / f"{name}.db")
        self._connections.append(conn)
        return conn

    def close(self) -> None:
        for conn in self._connections:
            conn.close()
        self._connections.clear()
        if self._owned:
            shutil.rmtree(self.directory, ignore_errors=True)


# =============================================================================
# Registrations and the assembled PluginApi
# =============================================================================

class RecordingRegistry:
    """``UiRegistry`` + ``ApiRegistry`` that record every registration."""

    def __init__(self) -> None:
        self.nav_items: list[NavItem] = []
        self.routers: list[Any] = []
        self.notifications: list[tuple[str, str]] = []

    def register_nav_item(self, item: NavItem) -> None:
        self.nav_items.append(item)

    def notify(self, message: str, level: str = "info") -> None:
        self.notifications.append((level, message))

    def register_router(self, router: Any) -> None:
        self.routers.append(router)


@dataclass
class FakePluginApi:
    """The ``PluginApi`` :func:`make_api` returns. ``ui`` and ``routes`` are
    the same :class:`RecordingRegistry`."""

    ui: RecordingRegistry
    routes: RecordingRegistry
    llm: Optional[Any]
    library: Optional[Any]
    files: Optional[Any]
    storage: Optional[Any]
    settings: InMemorySettings
    permissions: frozenset[Permission] = field(default_factory=frozenset)
    api_version: int = API_VERSION


def make_api(
    permissions: Iterable[Union[str, Permission]] = (),
    *,
    library: Optional[Any] = None,
    llm: Optional[Any] = None,
    files: Optional[Any] = None,
    storage: Optional[Any] = None,
    settings: Optional[dict] = None,
    core_settings: Optional[CoreSettings] = None,
) -> FakePluginApi:
    """Assemble a ``PluginApi`` from a declared permission set.

    Services not unlocked by ``permissions`` are ``None`` even when passed —
    the same rule the core applies. A declared service you do not pass gets a
    default fake (empty library, a ``DummyLlm`` answering ``""``). ``files``
    and ``storage`` are reserved Berechtigungen that grant nothing (2.2.0):
    both attributes are always ``None``, like in the core, whatever you pass. ``settings`` seeds the plugin's own keys;
    ``core_settings`` (default :data:`DEFAULT_CORE_SETTINGS`) is visible
    through ``settings.core()`` only with ``settings.core`` declared. A
    library with a ``write_allowed`` attribute (``InMemoryLibrary``) gets it
    set from ``library.write``, so ``create_by_doi`` fails without it.
    """
    perms = parse_permissions(permissions)
    registry = RecordingRegistry()

    def gate(permission: Permission, given: Optional[Any], default: Callable[[], Any]) -> Optional[Any]:
        if permission not in perms:
            return None
        return given if given is not None else default()

    core = (core_settings or DEFAULT_CORE_SETTINGS) if Permission.SETTINGS_CORE in perms else None
    lib = gate(Permission.LIBRARY_READ, library, InMemoryLibrary)
    if lib is not None and hasattr(lib, "write_allowed"):
        # The core's rule: create_by_doi answers only with library.write.
        lib.write_allowed = Permission.LIBRARY_WRITE in perms
    return FakePluginApi(
        ui=registry,
        routes=registry,
        library=lib,
        llm=gate(Permission.LLM, llm, DummyLlm),
        # Reserved: the core hands nothing over for these, so neither do we.
        files=None,
        storage=None,
        settings=InMemorySettings(settings, core),
        permissions=perms,
    )
