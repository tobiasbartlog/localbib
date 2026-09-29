"""The common item model every migration source maps to (PURE, stdlib only).

PRD #173 gives every source — BibTeX, RIS, Zotero RDF, Citavi, a PDF folder —
one shape to produce: :class:`MigrationItem`. The adapters
(``bibtex_migration``, ``ris_import``, later ``zotero_rdf`` / ``citavi`` /
``pdf_folder``) translate their dialect into it; ``routers/migration.py`` is the
only place that persists (ADR-0006).

This module also owns the one genuinely fiddly piece of that translation: the
``file`` field. Every tool writes local attachment paths differently and none of
them documents its escaping, so :func:`resolve_attachment` does not *decide* the
dialect — it derives the plausible readings of a spec and lets the filesystem
pick: the first candidate that is an existing file wins. Nothing here raises; a
path that resolves to nothing is reported as missing, which is the acceptance
criterion of #174.

Dialects covered (all seen in real exports):

* Zotero      ``Full Text PDF:files/12/x.pdf:application/pdf``
* JabRef      ``:C\\:/path/x.pdf:pdf``            (leading empty description)
* Mendeley    ``:/home/me/lib/x.pdf:pdf``
* Citavi      ``Name:Q\\\\:\\\\\\\\path:PDF``     (colons *and* backslashes escaped)
* plain       ``C:/lit/x.pdf`` / ``files/12/x.pdf``
* URL         ``file:///C:/lit/x.pdf``
* several attachments in one field, separated by ``;``

Relative paths resolve against ``base_dir`` — the directory of the export file,
which is what every tool writes them relative to.

PURE per the invariant (CLAUDE.md): stdlib only, no DB, no network, no writes.
The single filesystem access is ``os.path.isfile`` — a read, and the only way to
answer "does this attachment exist?".
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import unquote

# What the option set calls itself on the wire; the router mirrors these in its
# Pydantic model so an unknown value never reaches the loop.
ATTACH_MODES = ("all", "selected", "none")
DUPLICATE_MODES = ("skip", "attach")
REFERENCE_MODES = ("all", "none", "later")


class MigrationUnreadable(Exception):
    """This export cannot be read *as this source* — wrong format, broken file.

    The adapter contract's one exception. It separates "the file is not what it
    claims to be" from every other failure, so ``routers/migration.py`` can
    answer ``error.migration_unreadable`` (the reader picked the wrong source
    or the export is truncated — something they can fix) instead of the generic
    ``error.migration_read_failed``. Adapters raise it; nothing else does.
    """


@dataclass
class MigrationItem:
    """One entry of a source export, source-agnostic.

    ``attachments`` holds absolute paths of files that **exist right now**;
    ``attachments_missing`` keeps the raw specs that resolved to nothing, so
    analyze can report "3 of 5 PDFs found" without the router re-parsing
    anything. ``raw`` keeps the untranslated source fields — they land in
    ``papers.raw_metadata`` so nothing an export carried is lost.
    """

    key: str = ""
    entry_type: str = "misc"
    title: str = ""
    authors: str = ""
    year: int | None = None
    doi: str = ""
    isbn: str = ""
    abstract: str = ""
    journal: str = ""
    publisher: str = ""
    tags: list[str] = field(default_factory=list)
    collection_path: list[str] = field(default_factory=list)
    notes: str = ""
    date_added: str | None = None
    attachments: list[str] = field(default_factory=list)
    attachments_missing: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class MigrationOptions:
    """The option set of the wizard's step 3 (PRD #173, Flow step 3).

    Three groups, exactly as the wizard shows them: what data comes across,
    what happens to PDFs, and whether references are extracted afterwards.
    Defaults are the "bring everything, touch nothing expensive" set: all data
    fields on, PDFs attached where they were found, no LLM, no network-heavy
    reference extraction.
    """

    # --- Data ---
    import_abstract: bool = True
    import_notes: bool = True
    import_collections: bool = True
    import_tags: bool = True
    import_date_added: bool = True
    keep_cite_keys: bool = True
    fill_missing: bool = True          # CrossRef/OpenAlex fill-in for empty fields
    llm_categorize: bool = False

    # --- PDFs ---
    attach_pdfs: str = "all"           # all | selected | none
    oa_fallback: bool = False          # try an OA download when no file was found
    duplicates: str = "skip"           # skip | attach (PDF onto the existing item)

    # --- References ---
    extract_references: str = "none"   # all | none | later

    def __post_init__(self):
        if self.attach_pdfs not in ATTACH_MODES:
            self.attach_pdfs = "all"
        if self.duplicates not in DUPLICATE_MODES:
            self.duplicates = "skip"
        if self.extract_references not in REFERENCE_MODES:
            self.extract_references = "none"


# ---------------------------------------------------------------------------
# Attachment resolution
# ---------------------------------------------------------------------------

def split_attachment_specs(field_value: str) -> list[str]:
    """Split a ``file`` field into single attachment specs.

    Separator is an unescaped ``;`` — a path may legitimately contain ``\\;``.
    Empty specs are dropped.
    """
    parts = _split_unescaped(field_value or "", ";")
    return [p.strip() for p in parts if p.strip()]


def resolve_attachment(raw_path: str, base_dir: str = "") -> str | None:
    """One attachment spec → an absolute path to an existing file, or ``None``.

    The spec may be a bare path, a ``file://`` URL, or one of the
    ``description:path:type`` dialects (see the module docstring). Candidates
    are tried in order of specificity and the first one that is an existing file
    wins; nothing raises, an unreadable or malformed spec simply yields
    ``None``.
    """
    for candidate in attachment_candidates(raw_path):
        resolved = _absolutize(candidate, base_dir)
        if resolved and _is_file(resolved):
            return resolved
    return None


def resolve_attachments(field_value: str, base_dir: str = "") -> tuple[list[str], list[str]]:
    """A whole ``file`` field → ``(existing absolute paths, unresolved specs)``.

    Duplicates are collapsed (two Zotero entries can point at the same file)
    while order is kept, so "the first PDF" stays the first the export named.
    """
    found: list[str] = []
    missing: list[str] = []
    for spec in split_attachment_specs(field_value):
        resolved = resolve_attachment(spec, base_dir)
        if resolved:
            if resolved not in found:
                found.append(resolved)
        else:
            missing.append(spec)
    return found, missing


def attachment_candidates(raw_path: str) -> list[str]:
    """The plausible readings of one attachment spec, most specific first.

    Public because it is the part worth testing on its own: which path a
    dialect *means* is a decision, whether that path exists is not.
    """
    spec = (raw_path or "").strip()
    if not spec:
        return []

    url_path = _from_file_url(spec)
    if url_path is not None:
        return [url_path]

    fields = _split_unescaped(spec, ":")
    raw_candidates: list[str] = []
    if len(fields) >= 3:
        # description:path:type — the classic form. Re-joining the middle keeps
        # an unescaped drive colon ("Name:C:/x.pdf:PDF") intact.
        raw_candidates.append(":".join(fields[1:-1]))
        raw_candidates.append(":".join(fields[:-1]))   # path:type, no description
        raw_candidates.append(":".join(fields[1:]))    # description:path, no type
    elif len(fields) == 2:
        raw_candidates.append(":".join(fields[1:]))
        raw_candidates.append(":".join(fields[:-1]))
    raw_candidates.append(spec)                        # plain path

    out: list[str] = []
    for candidate in raw_candidates:
        for variant in _unescape_variants(candidate):
            variant = variant.strip()
            if variant and variant not in out:
                out.append(variant)
    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _split_unescaped(text: str, delimiter: str) -> list[str]:
    """Split on ``delimiter`` while a backslash escapes the next character."""
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            buf.append(ch)
            buf.append(text[i + 1])
            i += 2
            continue
        if ch == delimiter:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


# Only these are escapes worth undoing. A backslash in front of anything else
# is a Windows path separator — undoing it would turn "C:\Users\x.pdf" into
# "C:Usersx.pdf", which is how a naive unescape loses every Windows attachment.
_ESCAPABLE = ":;\\"


def _unescape(text: str) -> str:
    """``\\:``, ``\\;`` and ``\\\\`` → their plain character, in one pass."""
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text) and text[i + 1] in _ESCAPABLE:
            out.append(text[i + 1])
            i += 2
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _unescape_variants(candidate: str) -> list[str]:
    """The candidate unescaped zero, one and two times.

    Twice is not paranoia: Citavi escapes the drive colon *and* doubles every
    backslash, so ``Q\\\\:\\\\\\\\path`` only becomes ``Q:\\path`` after a second
    pass. Trying all three and letting the filesystem decide is cheaper than
    guessing which tool wrote the file.
    """
    variants = [candidate]
    once = _unescape(candidate)
    if once != candidate:
        variants.append(once)
        twice = _unescape(once)
        if twice != once:
            variants.append(twice)
    return variants


def _from_file_url(spec: str) -> str | None:
    """``file://`` URL → local path, or ``None`` when it is not a file URL."""
    lowered = spec.lower()
    if not lowered.startswith("file:"):
        return None
    path = spec[len("file:"):]
    while path.startswith("/"):
        path = path[1:]
    path = unquote(path)
    # A Windows drive survived the leading-slash strip ("file:///C:/x" -> "C:/x");
    # a POSIX path lost its root and gets it back.
    if len(path) >= 2 and path[1] == ":":
        return path
    return "/" + path


def _absolutize(candidate: str, base_dir: str) -> str:
    """Absolute form of a candidate, resolved against ``base_dir`` when relative."""
    path = os.path.expanduser(candidate)
    if not os.path.isabs(path) and base_dir:
        path = os.path.join(base_dir, path)
    try:
        return os.path.abspath(path)
    except (OSError, ValueError):
        return ""


def _is_file(path: str) -> bool:
    """``os.path.isfile`` that never raises — a spec can hold anything."""
    try:
        return os.path.isfile(path)
    except (OSError, ValueError):
        return False
