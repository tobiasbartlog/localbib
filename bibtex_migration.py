"""Migration adapter: BibTeX (.bib) → :class:`MigrationItem` (PURE).

Thin layer on top of ``bibtex_import.parse_bib`` — the parser stays where it is,
this module only answers the question the plain BibTeX import never asked: what
did the *tool* that wrote this file put next to the bibliographic fields?

JabRef, Mendeley, Zotero and Citavi all export .bib, and all four smuggle their
library structure into non-standard fields:

===================  ==================================================
``keywords``         comma/semicolon separated tags (everyone)
``mendeley-tags``    Mendeley's own tag field
``groups``           JabRef/Mendeley collection, ``A/B`` for a subgroup
``note`` ``annote``  the reader's notes
``timestamp``        when the entry entered the library (JabRef)
``date-added``       the same, Mendeley/BibDesk spelling
``file``             local attachment paths, one dialect per tool
===================  ==================================================

PURE (CLAUDE.md invariant): no DB, no network, no writes. ``read_items`` reads
the export file itself — the adapter contract of PRD #173 ("the server reads
from disk and resolves attachments relative to it") — and nothing else.
"""

from __future__ import annotations

import os
import re

import bibtex_import
from migration_items import MigrationItem, resolve_attachments

# Field names checked in order; the first non-empty one wins.
TAG_FIELDS = ("keywords", "mendeley-tags", "keyword", "tags")
GROUP_FIELDS = ("groups", "collection", "citavi-groups")
NOTE_FIELDS = ("note", "annote", "annotation", "comment")
DATE_ADDED_FIELDS = ("timestamp", "date-added", "dateadded", "created")
FILE_FIELDS = ("file", "pdf", "local-url")

# Both list separators are in the wild: Zotero writes "a; b", JabRef "a, b".
_SEPARATOR_RE = re.compile(r"\s*[;,]\s*")


def read_items(path: str) -> list[MigrationItem]:
    """Read a .bib file and map every entry to a :class:`MigrationItem`.

    Attachment paths resolve against the directory of ``path`` — where every
    tool writes them relative to.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    return to_items(text, os.path.dirname(os.path.abspath(path)))


def to_items(text: str, base_dir: str = "") -> list[MigrationItem]:
    """BibTeX source → items, in file order.

    Two parses, on purpose: the normalised one for everything a human reads and
    a verbatim one for the ``file`` field, whose backslashes the LaTeX→Unicode
    pass would eat (see ``bibtex_import.parse_bib_verbatim``).
    """
    verbatim = bibtex_import.parse_bib_verbatim(text)
    return [
        _to_item(entry, verbatim.get(entry.get("key", ""), {}), base_dir)
        for entry in bibtex_import.parse_bib(text)
    ]


def _to_item(entry: dict, verbatim: dict, base_dir: str) -> MigrationItem:
    raw = dict(entry.get("raw") or {})
    # Paths win from the untouched record — everything else stays converted.
    for name, value in verbatim.items():
        if str(name).lower() in FILE_FIELDS:
            raw[name] = value
    lowered = {str(k).lower(): v for k, v in raw.items()}

    attachments, missing = resolve_attachments(_first(lowered, FILE_FIELDS), base_dir)
    return MigrationItem(
        key=entry.get("key", ""),
        entry_type=entry.get("entry_type", "misc"),
        title=entry.get("title", ""),
        authors=entry.get("authors", ""),
        year=entry.get("year"),
        doi=entry.get("doi", ""),
        isbn=entry.get("isbn", ""),
        abstract=entry.get("abstract", ""),
        journal=entry.get("journal", ""),
        publisher=entry.get("publisher", ""),
        tags=split_tags(_first(lowered, TAG_FIELDS)),
        collection_path=split_collection(_first(lowered, GROUP_FIELDS)),
        notes=_clean_note(_first(lowered, NOTE_FIELDS)),
        date_added=_first(lowered, DATE_ADDED_FIELDS) or None,
        attachments=attachments,
        attachments_missing=missing,
        raw=raw,
    )


def split_tags(value: str) -> list[str]:
    """``keywords`` field → tag list, de-duplicated, order kept.

    Both separators are in the wild (Zotero writes ``a; b``, JabRef ``a, b``),
    so both split. A tag containing a comma is a loss we accept — guessing per
    file would be worse than one predictable rule.
    """
    tags: list[str] = []
    for part in _SEPARATOR_RE.split(value or ""):
        tag = part.strip().strip("{}").strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def split_collection(value: str) -> list[str]:
    """``groups`` field → one collection path.

    JabRef writes ``Parent/Child`` for a nested group and separates several
    memberships with a comma. An item can live in many groups but a category
    path is one chain, so the **first** group becomes the path — the wizard
    shows what would be created before anything is written.
    """
    first = ""
    for part in _SEPARATOR_RE.split(value or ""):
        if part.strip():
            first = part.strip()
            break
    return [s.strip() for s in first.split("/") if s.strip()]


def _first(lowered: dict, names: tuple[str, ...]) -> str:
    for name in names:
        value = lowered.get(name)
        if value and str(value).strip():
            return str(value).strip()
    return ""


def _clean_note(value: str) -> str:
    """Strip the grouping braces BibTeX writers wrap multi-word notes in."""
    note = (value or "").strip()
    while note.startswith("{") and note.endswith("}"):
        note = note[1:-1].strip()
    return note
