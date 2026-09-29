"""localbib-plugin-api — the Add-on Contract between the LocalBib core and its Add-ons.

Python-native translation of the architecture document (Abschnitt 5): the
contract lives here as Protocols and dataclasses. Since contract 2 this folder
is also a standalone package (``pyproject.toml`` next to this file, semver
major = ``API_VERSION``, changes in ``CHANGELOG.md``) so an Add-on can develop
and test without the core — see ``plugin_api.testing`` for the fakes.

Stdlib-only, no imports from the core. Besides types it holds exactly one
piece of logic, the manifest validator (``plugin_api.manifest``), which reads
the ``manifest.schema.json`` shipped in the package. It is the boundary every
plugin compiles against (P3: a plugin imports *only* from ``plugin_api``). The
``import-linter`` contract in ``.importlinter`` enforces that boundary.

Licensed **MIT** (see ``LICENSE``), unlike the AGPL-3.0 core: a third-party
plugin may license itself freely against this contract. While it runs
in-process with the AGPL core, AGPL-compatible licensing is recommended.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from plugin_api.manifest import (
    GATED_SERVICES,
    MANIFEST_FILENAME,
    PERMISSIONS,
    SLOTS,
    SETTING_TYPES,
    FrontendSpec,
    ManifestError,
    NavItem,
    Permission,
    PermissionInfo,
    PluginManifest,
    SettingField,
    granted_services,
    load_manifest,
    load_schema,
    parse_permissions,
    validate_manifest,
)

# The Add-on Contract version. Bump together with a breaking change to the
# interfaces below; the package's semver major equals it. Plugins declare the
# version they are built against via ``PluginManifest.api_version``
# (``api_version`` in ``plugin.json``). See CHANGELOG.md for 1 -> 2.
API_VERSION = 2


# =============================================================================
# Registry interfaces (what the core offers a plugin during activate())
# =============================================================================

class UiRegistry(Protocol):
    """UI extension points. ``register_*`` calls are undone on deactivate()."""

    def register_nav_item(self, item: NavItem) -> None: ...

    def notify(self, message: str, level: str = "info") -> None: ...


class ApiRegistry(Protocol):
    """Backend extension point: a plugin contributes HTTP routes that the core
    mounts on activate and unmounts on deactivate.

    Python-native addition to Abschnitt 5: in the original TS design plugins ran
    in the same process as the SPA and registered views directly; on this stack
    (Python server + Vue SPA) a plugin's own views need server endpoints, and the
    core cannot import the plugin to define them (P3). So the plugin builds the
    router and hands it over here. ``router`` is framework-typed (FastAPI
    ``APIRouter``) but kept opaque to the core registry."""

    def register_router(self, router: Any) -> None: ...


class LibraryApi(Protocol):
    """Access to the LocalBib core library (Abschnitt 5: ``library``).

    The handle exists with ``library.read``; every method but
    :meth:`create_by_doi` reads. ``get_full_text`` may legitimately return
    ``None`` (discovery A5): plugins must degrade to abstract-only.
    """

    def create_by_doi(
        self,
        doi: str,
        *,
        title: str = "",
        authors: "list[str] | str | None" = None,
        year: Optional[int] = None,
        journal: str = "",
        abstract: str = "",
    ) -> dict:
        """Add one Item to the library from its DOI (the core's DOI intake,
        ADR-0016). Needs ``library.write`` on top of ``library.read``; without
        it the call raises ``PermissionError``.

        The DOI is identity: an Item with that DOI already in the library is
        returned untouched (``created`` False). A new Item gets the metadata
        you pass, the core fills what you left empty from CrossRef/OpenAlex
        and tries an Open-Access PDF on a best-effort basis; the Item's origin
        is recorded as your Add-on's id. Blocking network I/O — call it from a
        worker thread or a sync route, not from the event loop.

        Returns ``{"doi": str (normalised), "created": bool, "paper_id": int,
        "citekey": str, "pdf": "fetched" | "none"}``. Raises ``ValueError``
        for an empty DOI or when the Item could not be created. Additive in
        contract 2.0.0 — guard with ``getattr(library, "create_by_doi", None)``
        if you support older hosts."""
        ...

    def get_reference(self, citekey: str) -> Optional[dict]: ...

    def search_references(self, query: str, limit: int = 20) -> list[dict]: ...

    def get_abstract(self, citekey: str) -> Optional[str]: ...

    def get_full_text(self, citekey: str) -> Optional[str]: ...

    def export_bibtex(self) -> str:
        """Full library as one .bib string (stored Cite Keys). Feeds the
        manuscript preview compiler (the LaTeX editor)."""
        ...

    def get_core_projects(self) -> list[dict]:
        """Core paper-grouping projects (being retired, ADR-0004), read-only:
        ``[{"id": int, "name": str, "description": str, "refs": [citekey, ...]}]``
        where ``refs`` are the grouped papers' stored Cite Keys. Feeds a plugin's
        startup migration to research projects (P3: the plugin never reads the
        core SQLite directly). Additive since issue #51 — plugins must degrade
        when the host predates it (``getattr(..., None)``)."""
        ...

    def on_change(self, callback: Callable[[dict], None]) -> Callable[[], None]: ...


class LlmApi(Protocol):
    """The core's central LLM service (keys, limits, logging live in the core).

    ``complete`` takes ``{"messages": [...], "temperature": float?, "timeout": int?,
    "model": str?, "tier": str?}`` and returns ``{"content": str}`` (markdown
    fences already stripped). When ``model`` is omitted, the core uses its
    configured default.

    ``tier`` is how a plugin states the *kind* of task, not the model:
    ``"fast"`` routes the request to the host's fast tier (many small,
    schematic calls — e.g. a per-paper concept extraction), anything
    else stays on the default reasoning tier. The mapping tier → model lives in
    the core's Model Routing table; a plugin never names a model unless it truly
    must. Additive since #121 — a host predating it ignores the key and
    answers on its default tier, which is correct, only pricier.

    ``embed`` returns one vector per input text. The core may not have an
    embeddings endpoint configured (discovery A4) — then it raises
    ``NotImplementedError`` and callers must degrade (e.g. lexical ranking).

    ``embed_model`` names the configured embedding model so a plugin can key its
    vector cache model-sharply (a model change ⇒ re-embed). Additive since
    #110 — access it defensively (``getattr(llm, "embed_model", None)``)
    so a host predating it still works.
    """

    def complete(self, request: dict) -> dict: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_model(self) -> str: ...


class FilesApi(Protocol):
    def watch(self, directory: str, callback: Callable[[dict], None]) -> Callable[[], None]: ...


class StorageApi(Protocol):
    def open_plugin_db(self, name: str) -> Any: ...  # returns a sqlite3.Connection-like handle


@dataclass(frozen=True)
class CoreSettings:
    """The named core read set behind the ``settings.core`` permission.

    Values are the user's, possibly empty (a fresh install has no mailto);
    an empty string means "not configured", never "not allowed".
    """

    mailto: str                  # the user's polite-pool address (CrossRef/OpenAlex)
    openalex_api_key: str
    ui_language: str             # e.g. "en", "de"
    base_dir: str                # the library base folder


class SettingsApi(Protocol):
    """Settings in the plugin's own namespace, plus the core read set.

    ``get``/``set`` need no permission and only ever see the plugin's own keys.
    ``core()`` returns :class:`CoreSettings` when the plugin declared
    ``settings.core`` and ``None`` otherwise.
    """

    def get(self, key: str) -> Optional[Any]: ...

    def set(self, key: str, value: Any) -> None: ...

    def core(self) -> Optional[CoreSettings]: ...


class PluginApi(Protocol):
    """The object handed to a plugin's ``activate()``. Abschnitt 5: ``PluginApi``.

    ``ui``/``routes`` are always present. The gated services (``library``,
    ``llm``, ``files``, ``storage`` — see ``GATED_SERVICES``) are handed over
    only when the manifest declares the matching Berechtigung and are ``None``
    otherwise; they may also be ``None`` when the host has no such service
    (e.g. no embeddings endpoint). Plugins must degrade gracefully.
    ``settings`` is the plugin-scoped store; its ``core()`` answers only with
    ``settings.core`` declared. ``plugin_api.testing.make_api`` builds this
    object from a permission set for tests without the core.
    """

    api_version: int
    ui: UiRegistry
    routes: ApiRegistry
    llm: Optional[LlmApi]
    library: Optional[LibraryApi]
    files: Optional[FilesApi]
    storage: Optional[StorageApi]
    settings: Optional[SettingsApi]


# =============================================================================
# The plugin itself
# =============================================================================

@runtime_checkable
class LocalBibPlugin(Protocol):
    """A LocalBib plugin. The core loads it by importing its package and reading
    a module-level ``plugin`` attribute that satisfies this protocol.

    ``deactivate()`` must return the system to the pre-``activate()`` state — the
    core's registry drops all scoped registrations, plugins undo anything else
    (watchers, subscriptions) themselves.
    """

    manifest: PluginManifest

    async def activate(self, api: PluginApi) -> None: ...

    async def deactivate(self) -> None: ...


__all__ = [
    "API_VERSION",
    "MANIFEST_FILENAME",
    "PluginManifest",
    "NavItem",
    "SettingField",
    "FrontendSpec",
    "Permission",
    "PermissionInfo",
    "PERMISSIONS",
    "GATED_SERVICES",
    "SLOTS",
    "SETTING_TYPES",
    "ManifestError",
    "granted_services",
    "parse_permissions",
    "load_manifest",
    "load_schema",
    "validate_manifest",
    "UiRegistry",
    "ApiRegistry",
    "LibraryApi",
    "LlmApi",
    "FilesApi",
    "StorageApi",
    "CoreSettings",
    "SettingsApi",
    "PluginApi",
    "LocalBibPlugin",
]
