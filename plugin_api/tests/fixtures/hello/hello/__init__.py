"""hello — the fixture Add-on of the Add-on Contract tests.

A Bundle in miniature: ``plugin.json`` at the Bundle root, this package under
the Add-on id, ``frontend/`` with one script, a stylesheet and two locales.
It imports only from ``plugin_api`` (and, lazily, FastAPI for its router), so
its tests run with the contract package and its fakes alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from plugin_api import PluginApi, load_manifest

BUNDLE_ROOT = Path(__file__).resolve().parent.parent

_GREETINGS = {"en": "Hello", "de": "Hallo"}


class HelloPlugin:
    manifest = load_manifest(BUNDLE_ROOT)

    def __init__(self) -> None:
        self._api: Optional[PluginApi] = None
        self._db: Any = None
        self._unsubscribe: Optional[Callable[[], None]] = None
        self.changes: list[dict] = []

    async def activate(self, api: PluginApi) -> None:
        self._api = api
        if self.manifest.nav is not None:
            api.ui.register_nav_item(self.manifest.nav)
        router = _build_router(self)
        if router is not None:
            api.routes.register_router(router)
        if api.library is not None:
            self._unsubscribe = api.library.on_change(self.changes.append)
        if api.storage is not None:
            self._db = api.storage.open_plugin_db("hello")
            self._db.execute("CREATE TABLE IF NOT EXISTS greetings (text TEXT NOT NULL)")

    async def deactivate(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._db is not None:
            self._db.close()
            self._db = None
        self._api = None

    # --- behaviour ---------------------------------------------------------
    def language(self) -> str:
        core = self._api.settings.core() if self._api and self._api.settings else None
        lang = core.ui_language if core is not None else ""
        return lang if lang in self.manifest.languages else self.manifest.default_language

    def greeting(self, citekey: str) -> str:
        """Greet the title of an Item; the LLM polishes it when allowed."""
        api = self._require_api()
        ref = api.library.get_reference(citekey) if api.library is not None else None
        subject = (ref or {}).get("title") or citekey
        salutation = (api.settings.get("salutation") if api.settings else None) or _GREETINGS[self.language()]
        text = f"{salutation}, {subject}!"
        if api.llm is not None:
            answer = api.llm.complete({
                "messages": [{"role": "user", "content": f"Rephrase warmly: {text}"}],
                "tier": "fast",
            })
            text = answer.get("content") or text
        if self._db is not None:
            self._db.execute("INSERT INTO greetings (text) VALUES (?)", (text,))
            self._db.commit()
        return text

    def history(self) -> list[str]:
        if self._db is None:
            return []
        return [row[0] for row in self._db.execute("SELECT text FROM greetings ORDER BY rowid")]

    def _require_api(self) -> PluginApi:
        if self._api is None:
            raise RuntimeError("hello is not active")
        return self._api


def _build_router(plugin: HelloPlugin) -> Any:
    try:
        from .api import build_router
    except ModuleNotFoundError as exc:
        if exc.name != "fastapi":
            raise
        return None  # contract-only test run: no web framework installed
    return build_router(plugin)


plugin = HelloPlugin()
