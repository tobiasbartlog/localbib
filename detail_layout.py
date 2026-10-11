"""The detail layout: which blocks an Item's detail view shows, and where.

Pure rule module (ADR-0006) in the family of ``llm_config`` / ``plugins_config``:
stdlib only, no I/O. ``routers/settings.py`` validates and stores the document
as ``DETAIL_LAYOUT`` in the ``.env`` (compact JSON) and ``webapp.py`` puts the
effective value into the first paint; the editor of #231 writes the same shape.

The document::

    {"v": 1,
     "main": ["abstract", "annotations"],
     "tabs": [{"name": "", "blocks": ["metadata", "customFields", "categories", "collections"]},
              {"name": "", "blocks": ["references"]},
              {"name": "", "blocks": ["notes"]}],
     "hidden": []}

``normalize`` is the one rule set, applied on write and on read:

* only known block ids; every block exactly once (the first occurrence wins,
  reading main, then the tabs in order, then hidden);
* an empty tab falls away; at most ``MAX_TABS`` tabs, the blocks of surplus
  tabs go into the last one;
* a tab name is trimmed and at most ``MAX_NAME`` characters; "" means
  "automatic" (the UI names the tab after its first block);
* a known block that is missing comes back at its default place: beside the
  blocks of its default group when one of them is shown, else at the end of the
  main column (main group) or as a tab of its own (tab group).

What is not even a layout (not JSON, not an object, ``main``/``tabs``/``hidden``
not lists, a tab not an object) raises ``InvalidLayout``; ``effective`` turns
that into the default layout for the reading side.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Optional

VERSION = 1
MAX_TABS = 4
MAX_NAME = 24

# The eight blocks, in their default reading order.
BLOCK_IDS = (
    "abstract", "annotations", "notes", "references",
    "metadata", "customFields", "categories", "collections",
)

_DEFAULT: dict[str, Any] = {
    "v": VERSION,
    "main": ["abstract", "annotations"],
    "tabs": [
        {"name": "", "blocks": ["metadata", "customFields", "categories", "collections"]},
        {"name": "", "blocks": ["references"]},
        {"name": "", "blocks": ["notes"]},
    ],
    "hidden": [],
}

# A block's default group: the blocks that belong beside it. Index 0 is the
# main column, the others the default tabs.
_GROUPS: tuple[tuple[str, ...], ...] = (
    ("abstract", "annotations"),
    ("metadata", "customFields", "categories", "collections"),
    ("references",),
    ("notes",),
)


class InvalidLayout(ValueError):
    """The value is not a layout document at all."""


def default_layout() -> dict:
    """A fresh copy of the default layout (callers may mutate it)."""
    return copy.deepcopy(_DEFAULT)


def _id_list(value: Any) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidLayout("not a list")
    return value


def _clean_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    # A lone surrogate is not text any encoder can write: drop it. The cut is
    # in code points (Python str), the UI counts the same way.
    value = "".join(ch for ch in value if not 0xD800 <= ord(ch) <= 0xDFFF)
    return value.strip()[:MAX_NAME].strip()


def _place_missing(main: list, tabs: list, hidden: list, block: str) -> None:
    """Put a missing ``block`` at its default place (see the module doc)."""
    group = next(g for g in _GROUPS if block in g)
    before = group[:group.index(block)]
    after = group[group.index(block) + 1:]
    containers = [main] + [t["blocks"] for t in tabs]
    for blocks in containers:
        # A container that holds a sibling of the group: after the nearest
        # sibling that precedes the block, else before the first that follows.
        precedes = [blocks.index(b) for b in before if b in blocks]
        follows = [blocks.index(b) for b in after if b in blocks]
        if precedes:
            blocks.insert(max(precedes) + 1, block)
            return
        if follows:
            blocks.insert(min(follows), block)
            return
    if group is _GROUPS[0]:
        main.append(block)
    elif len(tabs) < MAX_TABS:
        tabs.append({"name": "", "blocks": [block]})
    else:
        tabs[-1]["blocks"].append(block)


def normalize(raw: Any) -> dict:
    """The effective layout for ``raw`` (a parsed document); raises
    ``InvalidLayout`` when ``raw`` has the wrong basic shape."""
    if not isinstance(raw, dict):
        raise InvalidLayout("not an object")
    main_in = _id_list(raw.get("main"))
    hidden_in = _id_list(raw.get("hidden"))
    tabs_in = _id_list(raw.get("tabs"))
    for tab in tabs_in:
        if not isinstance(tab, dict):
            raise InvalidLayout("a tab is not an object")
        _id_list(tab.get("blocks"))

    seen: set[str] = set()

    def keep(ids: list) -> list:
        out = []
        for b in ids:
            if isinstance(b, str) and b in BLOCK_IDS and b not in seen:
                seen.add(b)
                out.append(b)
        return out

    main = keep(main_in)
    tabs = [{"name": _clean_name(t.get("name")), "blocks": keep(t.get("blocks") or [])}
            for t in tabs_in]
    hidden = keep(hidden_in)

    tabs = [t for t in tabs if t["blocks"]]
    for extra in tabs[MAX_TABS:]:
        tabs[MAX_TABS - 1]["blocks"].extend(extra["blocks"])
    del tabs[MAX_TABS:]

    for block in (b for g in _GROUPS for b in g):  # default reading order
        if block not in seen:
            _place_missing(main, tabs, hidden, block)
            seen.add(block)
    return {"v": VERSION, "main": main, "tabs": tabs, "hidden": hidden}


def parse(text: Any) -> dict:
    """``normalize`` for the stored/sent text; ``InvalidLayout`` for anything
    that is not JSON of the right basic shape."""
    if not isinstance(text, str):
        raise InvalidLayout("not text")
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise InvalidLayout("not JSON") from exc
    return normalize(raw)


def effective(text: Optional[str]) -> dict:
    """The layout in force for a stored value: the normalized document, or the
    default when nothing (usable) is stored."""
    if not text:
        return default_layout()
    try:
        return parse(text)
    except InvalidLayout:
        return default_layout()


def rules() -> dict:
    """What the editor needs to know: the default layout and the limits, so the
    UI keeps no second copy."""
    return {"default": default_layout(), "max_tabs": MAX_TABS, "max_name": MAX_NAME}


def dumps(layout: dict) -> str:
    """The compact JSON a layout is stored and served as."""
    return json.dumps(layout, separators=(",", ":"), ensure_ascii=False)
