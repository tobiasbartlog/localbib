"""Python reads the SPA's catalog - it keeps none of its own (ADR-0018 addendum, #214).

The tray icon's menu, its one notification and the "port is taken" message are
drawn by Python, not by the browser. They still come from
``static/locales/core.<lang>.js``, the very files the SPA loads: there is one
catalog per language, and a second one in Python would drift from it the way
two catalogs always do. ``tests/js/i18n_parity.test.js`` therefore guards
these keys like every other one.

The catalog files are plain JS assigning a flat object, one
``"key": "value",`` per line with JSON string escapes. Only lines of exactly
that shape are read, and only keys under ``prefix`` (``tray.`` for the
starter); everything else in the file is JavaScript and ignored.

A missing key renders as its key path in the SPA's visible form
``⟦tray.open⟧`` (ADR-0018, decision 6) - never as text of the other language,
never as an empty menu entry.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict

#: One catalog entry: ``"key": "value",`` on its own line.
_ENTRY = re.compile(r'^\s*"([^"\\]+)"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,?\s*$')

#: How a missing key shows - the same form as the SPA's ``t()``.
MISSING = "\u27e6{key}\u27e7"


def parse(text: str, prefix: str = "") -> Dict[str, str]:
    """The ``key -> text`` entries of one catalog file under ``prefix``."""
    out: Dict[str, str] = {}
    for line in text.splitlines():
        match = _ENTRY.match(line)
        if not match or not match.group(1).startswith(prefix):
            continue
        raw = match.group(2)
        try:
            out[match.group(1)] = json.loads(f'"{raw}"')
        except ValueError:
            out[match.group(1)] = raw
    return out


def catalog_path(locales_dir: str, lang: str) -> str:
    return os.path.join(locales_dir, f"core.{lang}.js")


class Catalog:
    """The ``prefix`` keys of each language's catalog, read once per language."""

    def __init__(self, locales_dir: str, prefix: str = "") -> None:
        self.locales_dir = locales_dir
        self.prefix = prefix
        self._cache: Dict[str, Dict[str, str]] = {}

    def strings(self, lang: str) -> Dict[str, str]:
        if lang not in self._cache:
            try:
                with open(catalog_path(self.locales_dir, lang), encoding="utf-8") as fh:
                    self._cache[lang] = parse(fh.read(), self.prefix)
            except OSError:
                # No catalog for that language: every key shows as its path.
                self._cache[lang] = {}
        return self._cache[lang]

    def text(self, lang: str, key: str, **params: object) -> str:
        """The text of ``key`` with its ``{name}`` placeholders filled in."""
        value = self.strings(lang).get(key)
        if value is None:
            return MISSING.format(key=key)
        for name, param in params.items():
            value = value.replace("{" + name + "}", str(param))
        return value
