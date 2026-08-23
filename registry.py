"""Minimal plugin registry for the LocalBib core (Phase 0b).

Responsibilities, deliberately small (handoff guardrail: "keine Kern-Umbauten
jenseits Registry + Toggle"):

* Load a plugin package **only when enabled** (``importlib`` gate). A disabled
  plugin's package is never imported — honouring the architecture's
  "deaktiviert ⇒ nicht im geladenen Bundle" on the side that counts here, the
  backend (discovery A6).
* Call ``activate(api)`` / ``deactivate()`` and keep each plugin's UI
  registrations in a *scoped* bucket, so ``deactivate()`` is residue-free by
  construction (Phase-0b acceptance criterion).

This module is side-effect-free at import time: it writes no DB, configures no
logging, touches no filesystem. Only ``webapp.py`` owns those.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Optional

from plugin_api import NavItem

logger = logging.getLogger(__name__)


class _Registrations:
    """The contributions of a single activated plugin. Cleared on deactivate."""

    def __init__(self) -> None:
        self.nav_items: list[NavItem] = []
        self.routers: list[Any] = []

    # --- UiRegistry protocol ------------------------------------------------
    def register_nav_item(self, item: NavItem) -> None:
        self.nav_items.append(item)

    def notify(self, message: str, level: str = "info") -> None:
        logger.info("[plugin notify:%s] %s", level, message)

    # --- ApiRegistry protocol -----------------------------------------------
    def register_router(self, router: Any) -> None:
        self.routers.append(router)

    # ------------------------------------------------------------------------
    def clear(self) -> None:
        self.nav_items.clear()
        self.routers.clear()


class _PluginApi:
    """Concrete PluginApi handed to a plugin's ``activate()``.

    ``ui``/``routes`` since Phase 0b. ``llm``/``library`` (Phase 4) are host
    services the core injects via ``PluginRegistry(services=...)`` — the
    registry itself stays free of core imports; the adapters live in
    ``webapp.py``. Missing services surface as ``None`` and plugins degrade.
    """

    api_version = 1

    def __init__(self, registrations: _Registrations, services: dict[str, Any]) -> None:
        self.ui = registrations
        self.routes = registrations
        self.llm = services.get("llm")
        self.library = services.get("library")


class PluginRegistry:
    """Tracks active plugins and aggregates their UI contributions."""

    def __init__(self, services: Optional[dict[str, Any]] = None) -> None:
        # module name -> (plugin instance, its scoped registrations)
        self._plugins: dict[str, tuple[object, _Registrations]] = {}
        # Host services exposed to plugins via PluginApi (e.g. "llm", "library").
        self._services: dict[str, Any] = services or {}

    def register_services(self, services: dict[str, Any]) -> None:
        """Inject/replace host services after construction.

        Lets the registry instance be owned by a neutral module (``context.py``)
        while the concrete service adapters — which depend on core internals —
        are constructed and injected by ``webapp.py``.
        """
        self._services.update(services)

    # --- lifecycle ----------------------------------------------------------
    async def activate(self, module_name: str) -> None:
        if module_name in self._plugins:
            return
        module = importlib.import_module(module_name)
        plugin = getattr(module, "plugin", None)
        if plugin is None:
            raise RuntimeError(
                f"Plugin-Modul '{module_name}' hat kein 'plugin'-Attribut."
            )
        registrations = _Registrations()
        await plugin.activate(_PluginApi(registrations, self._services))
        self._plugins[module_name] = (plugin, registrations)
        logger.info("Plugin '%s' aktiviert.", module_name)

    async def deactivate(self, module_name: str) -> None:
        entry = self._plugins.pop(module_name, None)
        if entry is None:
            return
        plugin, registrations = entry
        try:
            await plugin.deactivate()
        finally:
            # Drop scoped registrations even if the plugin's own teardown raised,
            # so the core is always left in the pre-activate state.
            registrations.clear()
        logger.info("Plugin '%s' deaktiviert.", module_name)

    async def sync(self, module_name: str, enabled: bool) -> None:
        """Bring the plugin's state in line with the toggle (idempotent)."""
        if enabled:
            await self.activate(module_name)
        else:
            await self.deactivate(module_name)

    # --- queries ------------------------------------------------------------
    def nav_items(self) -> list[NavItem]:
        items: list[NavItem] = []
        for _plugin, registrations in self._plugins.values():
            items.extend(registrations.nav_items)
        return items

    def routers(self) -> list[Any]:
        routers: list[Any] = []
        for _plugin, registrations in self._plugins.values():
            routers.extend(registrations.routers)
        return routers

    def is_active(self, module_name: str) -> bool:
        return module_name in self._plugins
