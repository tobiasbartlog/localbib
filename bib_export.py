"""The running app's BibTeX options: dialect setting + export words from the catalog.

Neutral module (like ``host_services``): ``bibtex_builder`` and
``services.collection_export`` stay pure and take ``dialect``/``labels`` as
arguments; every caller that exports for the user (the per-Item endpoint, the
.bib/.zip export, the add-ons' bibliographies, the CLI) passes
``**bib_export.options()`` so all of them write the same thing.

``BIB_DIALECT`` is a setting (``.env``, default in ``Config.reload_from_env``).
The words an entry needs (a standard's ``type``, "Accessed …" in classic
BibTeX) come from the SPA's catalog, ``bibExport.*`` — Python keeps no catalog
of its own (ADR-0018 addendum, like the backup README).
"""

from __future__ import annotations

import os
from typing import Dict

import ui_catalog
from config import Config

_PREFIX = "bibExport."
_LOCALES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "locales")
_catalogs: Dict[str, ui_catalog.Catalog] = {}


def labels(lang: str, locales_dir: str = _LOCALES_DIR) -> Dict[str, str]:
    """``{standard, patent, accessed, version}`` in ``lang``; a key missing
    from the catalog is left out, so the builder's English default applies."""
    catalog = _catalogs.setdefault(locales_dir, ui_catalog.Catalog(locales_dir, _PREFIX))
    return {key[len(_PREFIX):]: text for key, text in catalog.strings(lang).items()}


def options() -> dict:
    """``dialect`` and ``labels`` for ``bibtex_builder.format_entry``."""
    return {"dialect": Config.BIB_DIALECT, "labels": labels(Config.UI_LANGUAGE)}
