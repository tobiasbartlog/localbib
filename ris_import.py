"""RIS import: a real RIS parser + the migration adapter (PURE).

Replaces the hand-rolled loop that used to sit inside ``POST /api/import/ris``
(``routers/export.py``). That loop knew nine tags, silently dropped everything
else and could not read a wrapped line — which is most abstracts, because RIS
writers hard-wrap at 70-odd characters. The parser here is tag-complete: it
keeps **every** tag of a record, folds continuation lines back into their tag,
and lets the mapping layer decide what becomes a column, what becomes a tag,
and what stays in ``raw``.

Two consumers, one parser:

* :func:`to_legacy_entries` feeds ``POST /api/import/ris`` — same response
  shape as before, now with multi-line values and the extra tags in
  ``raw_metadata``.
* :func:`to_items` feeds the migration wizard (PRD #173): ``KW`` → tags,
  ``N1`` → notes, ``L1``/``L2`` → local attachments, ``VL``/``IS``/``SP``/``EP``
  → ``raw``.

PURE (CLAUDE.md invariant): no DB, no network, no writes. ``read_items`` reads
the export file, like every migration adapter.
"""

from __future__ import annotations

import os
import re

from migration_items import MigrationItem, resolve_attachment

# "TY  - JOUR" — two-character tag, two spaces, hyphen, space. Writers disagree
# about the trailing space on an empty value, so it is optional.
_TAG_RE = re.compile(r"^([A-Z][A-Z0-9])  -(?: (.*))?$")

# Tag → item field. Order inside a tuple is preference order: the first tag
# present wins, later ones only fill a gap.
TITLE_TAGS = ("TI", "T1", "CT", "BT")
AUTHOR_TAGS = ("AU", "A1", "A2", "A3", "A4")
YEAR_TAGS = ("PY", "Y1", "DA")
JOURNAL_TAGS = ("JO", "JF", "JA", "T2")
ABSTRACT_TAGS = ("AB", "N2")
ATTACHMENT_TAGS = ("L1", "L2", "L4")
NOTE_TAGS = ("N1",)
TAG_TAGS = ("KW",)

# RIS reference types → the BibTeX-ish entry types the rest of the app speaks.
ENTRY_TYPES = {
    "JOUR": "article",
    "EJOUR": "article",
    "BOOK": "book",
    "EBOOK": "book",
    "CHAP": "incollection",
    "CHAPTER": "incollection",
    "CONF": "inproceedings",
    "CPAPER": "inproceedings",
    "THES": "phdthesis",
    "RPRT": "techreport",
    "UNPB": "unpublished",
    "ELEC": "online",
    "GEN": "misc",
}


def read_items(path: str) -> list[MigrationItem]:
    """Read a .ris file and map every record to a :class:`MigrationItem`."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    return to_items(text, os.path.dirname(os.path.abspath(path)))


def parse_ris(text: str) -> list[dict[str, list[str]]]:
    """RIS source → one ``{tag: [values]}`` dict per record, in file order.

    A record runs from ``TY`` to ``ER``; a line without a tag continues the
    previous value (joined with a space — RIS writers wrap mid-sentence). A
    record is kept when it carries any tag at all, so a malformed export still
    surfaces in the wizard instead of vanishing.
    """
    records: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] | None = None
    last_tag = ""

    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip()
        match = _TAG_RE.match(line.strip()) if line.strip() else None

        if match is None:
            # Continuation of the value before it (wrapped abstract, note, …).
            if current is not None and last_tag and line.strip():
                current[last_tag][-1] = (current[last_tag][-1] + " " + line.strip()).strip()
            continue

        tag = match.group(1)
        value = (match.group(2) or "").strip()

        if tag == "TY":
            if current:
                records.append(current)
            current = {"TY": [value]}
            last_tag = "TY"
            continue
        if current is None:
            # Stray tag before the first TY — start a record anyway.
            current = {}
        if tag == "ER":
            records.append(current)
            current = None
            last_tag = ""
            continue
        current.setdefault(tag, []).append(value)
        last_tag = tag

    if current:
        records.append(current)
    return [r for r in records if r]


def to_items(text: str, base_dir: str = "") -> list[MigrationItem]:
    """RIS source → migration items, in file order."""
    return [_to_item(record, base_dir) for record in parse_ris(text)]


def to_legacy_entries(text: str) -> list[dict]:
    """RIS source → the flat entry dicts ``POST /api/import/ris`` has always
    persisted (``type``/``title``/``authors``/``year``/…), now carrying every
    other tag as an extra key so ``raw_metadata`` keeps the whole record.

    Records without a title are dropped — unchanged behaviour: a paper row
    whose title is empty is not something the library can show.
    """
    entries = []
    for record in parse_ris(text):
        title = _first(record, TITLE_TAGS)
        if not title:
            continue
        entry: dict = {"type": _first(record, ("TY",))}
        entry["title"] = title
        authors = _all(record, AUTHOR_TAGS)
        if authors:
            entry["authors"] = authors
        year = _year(record)
        if year is not None:
            entry["year"] = year
        for key, tags in (("doi", ("DO",)), ("journal", JOURNAL_TAGS),
                          ("abstract", ABSTRACT_TAGS), ("publisher", ("PB",)),
                          ("isbn", ("SN",))):
            value = _first(record, tags)
            if value:
                entry[key] = value
        # Everything the columns do not cover stays readable in raw_metadata.
        known = {"TY", "DO", "PB", "SN", "ER", *TITLE_TAGS, *AUTHOR_TAGS,
                 *YEAR_TAGS, *JOURNAL_TAGS, *ABSTRACT_TAGS}
        for tag, values in record.items():
            if tag not in known and values:
                entry[tag] = values[0] if len(values) == 1 else list(values)
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _to_item(record: dict[str, list[str]], base_dir: str) -> MigrationItem:
    attachments: list[str] = []
    missing: list[str] = []
    for tag in ATTACHMENT_TAGS:
        for value in record.get(tag, []):
            if not value.strip():
                continue
            resolved = resolve_attachment(value, base_dir)
            if resolved:
                if resolved not in attachments:
                    attachments.append(resolved)
            elif not _is_remote(value):
                # A plain http(s) link is not a missing local file — the OA
                # fallback is the path for those, not a red counter.
                missing.append(value)

    ris_type = _first(record, ("TY",)).upper()
    return MigrationItem(
        key=_first(record, ("ID",)),
        entry_type=ENTRY_TYPES.get(ris_type, "misc"),
        title=_first(record, TITLE_TAGS),
        authors="; ".join(_all(record, AUTHOR_TAGS)),
        year=_year(record),
        doi=_normalize_doi(_first(record, ("DO",))),
        isbn=_first(record, ("SN",)),
        abstract=_first(record, ABSTRACT_TAGS),
        journal=_first(record, JOURNAL_TAGS),
        publisher=_first(record, ("PB",)),
        tags=_dedupe(_all(record, TAG_TAGS)),
        collection_path=[],   # RIS has no collections
        notes="\n\n".join(v for v in _all(record, NOTE_TAGS) if v.strip()),
        date_added=_first(record, ("Y2",)) or None,
        attachments=attachments,
        attachments_missing=missing,
        raw={tag: (values[0] if len(values) == 1 else list(values))
             for tag, values in record.items()},
    )


def _first(record: dict[str, list[str]], tags: tuple[str, ...]) -> str:
    for tag in tags:
        for value in record.get(tag, []):
            if value.strip():
                return value.strip()
    return ""


def _all(record: dict[str, list[str]], tags: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for tag in tags:
        for value in record.get(tag, []):
            if value.strip():
                out.append(value.strip())
    return out


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _year(record: dict[str, list[str]]) -> int | None:
    for tag in YEAR_TAGS:
        for value in record.get(tag, []):
            match = re.search(r"(\d{4})", value)
            if match:
                return int(match.group(1))
    return None


def _normalize_doi(doi: str) -> str:
    doi = (doi or "").strip()
    if doi.lower().startswith("http"):
        doi = doi.split("doi.org/")[-1]
    return doi.rstrip(".")


def _is_remote(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered.startswith("http://") or lowered.startswith("https://")
