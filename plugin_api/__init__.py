"""@localbib/plugin-api — the contract between the LocalBib core and its plugins.

Python-native translation of the architecture document (Abschnitt 5). The
original document assumes a TypeScript ``@localbib/plugin-api`` package; the
real codebase is Python/FastAPI, so the contract lives here as Protocols and
dataclasses. See ``docs/discovery.md`` (Weg A) for why.

This package contains **only** types and trivial dataclasses — no runtime
logic, no I/O, no imports from the core. It is the boundary every plugin
compiles against (P3: a plugin imports *only* from ``plugin_api``). The
``import-linter`` contract in ``.importlinter`` enforces that boundary.

Licensed **MIT** (see ``plugin_api/LICENSE``), unlike the AGPL-3.0 core: a
third-party plugin may license itself freely against this contract. While it
runs in-process with the AGPL core, AGPL-compatible licensing is recommended —
see the README and ADR-0015.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol, runtime_checkable

# Bump together with a breaking change to the interfaces below. Plugins declare
# the minimum version they require via ``PluginManifest.min_api_version``.
API_VERSION = 1


# =============================================================================
# Manifest & UI contributions
# =============================================================================

@dataclass(frozen=True)
class PluginManifest:
    """Identity of a plugin. Mirrors ``PluginManifest`` in Abschnitt 5."""

    id: str                      # e.g. "notes"
    name: str
    version: str
    min_api_version: int = 1


@dataclass(frozen=True)
class NavItem:
    """A navigation entry a plugin contributes to the LocalBib sidebar.

    ``view`` is the frontend view key the SPA maps to a Vue component; the core
    never renders the view itself, it only relays this descriptor to the SPA via
    ``GET /api/plugins/nav``.
    """

    id: str
    label: str
    icon: str                    # inline SVG markup, rendered via v-html
    route: str                   # hash-router path, e.g. "/notes"
    view: str                    # frontend component key, e.g. "notes"


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
    """Read-only access to the LocalBib core library (Abschnitt 5: ``library``).

    Phase 0b declares the surface; the concrete implementation is filled in as
    later phases need it. ``get_full_text`` may legitimately return ``None``
    (discovery A5): plugins must degrade to abstract-only.
    """

    def get_reference(self, citekey: str) -> Optional[dict]: ...

    def search_references(self, query: str, limit: int = 20) -> list[dict]: ...

    def get_abstract(self, citekey: str) -> Optional[str]: ...

    def get_full_text(self, citekey: str) -> Optional[str]: ...

    def export_bibtex(self) -> str:
        """Full library as one .bib string (stored Cite Keys). Feeds the
        manuscript preview compiler (docs/PRD-latex-editor.md)."""
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


class SettingsApi(Protocol):
    """Plugin-scoped settings."""

    def get(self, key: str) -> Optional[Any]: ...

    def set(self, key: str, value: Any) -> None: ...


class PluginApi(Protocol):
    """The object handed to a plugin's ``activate()``. Abschnitt 5: ``PluginApi``.

    ``ui``/``routes`` since Phase 0b; ``llm``/``library`` since Phase 4
    (LLM-Matching). Either may be ``None`` when the host provides no such
    service (e.g. router built in isolation for tests) — plugins must
    degrade gracefully. ``files``/``storage``/``settings`` remain declared
    contract members, wired up when a phase needs them.
    """

    api_version: int
    ui: UiRegistry
    routes: ApiRegistry
    llm: Optional[LlmApi]
    library: Optional[LibraryApi]


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
    "PluginManifest",
    "NavItem",
    "UiRegistry",
    "ApiRegistry",
    "LibraryApi",
    "LlmApi",
    "FilesApi",
    "StorageApi",
    "SettingsApi",
    "PluginApi",
    "LocalBibPlugin",
]
