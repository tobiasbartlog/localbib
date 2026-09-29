"""Service: match a folder of PDFs onto metadata-only items (PURE).

The Mendeley path of the migration (PRD #173, Slice 4) — and the repair step
for every run whose attachments were missing. A reader points at the folder
their old manager kept its files in; this module answers one question per PDF:
*which item in the library is this the missing file of?*

Three answers, in this order:

1. **already_owned** — the SHA256 is already a paper's ``file_hash``. The
   library has this exact file; attaching it again would be a duplicate, so it
   is reported and never offered as a match.
2. **matched** — the PDF's own text names an item: a DOI found by
   :func:`doi_discovery.extract_doi_from_text` (strategy ``doi``), or one of
   the first text lines fuzzily hitting a title (strategy ``title``). Failing
   both, the *filename* is tried against the titles (strategy ``filename``)
   after author/year decoration such as ``Smith - 2019 - `` is stripped — the
   shape Zotero, Mendeley and Citavi all name their exports in.
3. **unmatched** — nothing qualified. The caller offers these as new items.

Candidates are deliberately only the papers **without a file**
(``filename = ''``): a migration attaches what is missing, it never competes
with a PDF the library already has. The restriction is structural — the
candidate rows are copied into an in-memory view and
:func:`paper_matcher.match` runs against *that*, so this module inherits the
library's one matching rule (exact DOI → rapidfuzz ``token_sort_ratio`` ≥ 85)
instead of re-deciding it. A paper that one PDF claimed leaves the view, so the
next PDF competes for the items that are still open.

PURE per the side-effect-free invariant (CLAUDE.md, ADR-0006): it reads the
PDFs and the connection it is *handed*, and writes nothing — no DB write, no
file moved, no connection opened. The router attaches (``paper_ingest``).
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field

from doi_discovery import extract_doi_from_text
from literature_manager import Config, compute_file_hash, extract_text_from_pdf
from paper_matcher import match as match_ref

# How many of the leading text lines are offered as a title guess. The first
# line of a PDF is the title often, but not always — a journal banner or a
# running head can sit above it, so the next candidates get their chance too.
MAX_TITLE_GUESSES = 3

# Leading lines are scanned this far down before giving up on a title.
TITLE_SCAN_LINES = 15


@dataclass(frozen=True)
class FolderMatch:
    """One PDF and the metadata-only item it belongs to.

    ``pdf_path`` is the stable id: the client ticks matches off by path and
    sends exactly the confirmed subset back to the commit.
    """

    pdf_path: str
    pdf_name: str
    paper_id: int
    paper_title: str
    strategy: str          # "doi" | "title" | "filename"
    confidence: float


@dataclass(frozen=True)
class OwnedPdf:
    """A PDF the library already holds, byte for byte."""

    pdf_path: str
    pdf_name: str
    paper_id: int
    paper_title: str


@dataclass
class FolderMatchResult:
    """What a scan found. ``scanned`` counts the PDFs looked at, so a caller
    can tell "nothing matched" from "nothing was there"."""

    matches: list[FolderMatch] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    already_owned: list[OwnedPdf] = field(default_factory=list)
    scanned: int = 0


def list_pdfs(folder: str, recursive: bool = False) -> list[str]:
    """Every ``*.pdf`` under ``folder``, sorted. Recursion is opt-in — a
    reader's Downloads folder is not a library, and walking it by accident
    costs minutes of text extraction."""
    found: list[str] = []
    if recursive:
        for root, _dirs, files in os.walk(folder):
            found.extend(os.path.join(root, f) for f in files
                         if f.lower().endswith(".pdf"))
    else:
        try:
            entries = os.listdir(folder)
        except OSError as e:
            logging.warning("PDF-Ordner %s nicht lesbar: %s", folder, e)
            return []
        found = [os.path.join(folder, f) for f in entries
                 if f.lower().endswith(".pdf")
                 and os.path.isfile(os.path.join(folder, f))]
    return sorted(found)


def scan(folder: str, conn: sqlite3.Connection, *, recursive: bool = False,
         limit: int | None = None) -> FolderMatchResult:
    """Match every PDF under ``folder`` against the library's items without a
    file. Reads only; the caller decides what to attach."""
    result = FolderMatchResult()
    paths = list_pdfs(folder, recursive)
    if limit is not None:
        paths = paths[:limit]
    if not paths:
        return result

    candidates = _candidate_view(conn)
    try:
        for path in paths:
            result.scanned += 1
            owner = _owner_of(path, conn)
            if owner is not None:
                result.already_owned.append(
                    OwnedPdf(pdf_path=path, pdf_name=os.path.basename(path),
                             paper_id=owner["id"], paper_title=owner["title"] or "")
                )
                continue
            found = _match_one(path, candidates)
            if found is None:
                result.unmatched.append(path)
                continue
            result.matches.append(found)
            # One item can only miss one file: take it out of the running so
            # the next PDF is matched against what is still open.
            candidates.execute("DELETE FROM papers WHERE id = ?", (found.paper_id,))
    finally:
        candidates.close()
    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _candidate_view(conn: sqlite3.Connection) -> sqlite3.Connection:
    """An in-memory copy of the *matchable* papers — those without a file.

    ``paper_matcher.match`` searches whatever ``papers`` table it is given;
    handing it this view is how the restriction becomes structural instead of
    a filter applied after the fact. The real connection is only read.
    """
    view = sqlite3.connect(":memory:")
    view.row_factory = sqlite3.Row
    view.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT, doi TEXT)")
    rows = conn.execute(
        """SELECT id, COALESCE(title, '') AS title, COALESCE(doi, '') AS doi
           FROM papers WHERE filename IS NULL OR filename = ''"""
    ).fetchall()
    view.executemany(
        "INSERT INTO papers (id, title, doi) VALUES (?, ?, ?)",
        [(r["id"], r["title"], r["doi"]) for r in rows],
    )
    return view


def _owner_of(path: str, conn: sqlite3.Connection):
    """The paper whose ``file_hash`` is this file's SHA256, or ``None``."""
    try:
        file_hash = compute_file_hash(path)
    except OSError as e:
        logging.warning("PDF %s nicht lesbar: %s", path, e)
        return None
    return conn.execute(
        "SELECT id, title FROM papers WHERE file_hash = ?", (file_hash,)
    ).fetchone()


def _match_one(path: str, candidates: sqlite3.Connection) -> FolderMatch | None:
    """DOI → title lines → filename. The first answer wins."""
    text = _read_text(path)
    doi = extract_doi_from_text(text) if text else None

    if doi:
        hit = match_ref({"doi": doi, "title": ""}, candidates)
        if hit.matched_paper_id is not None:
            return _as_match(path, hit.matched_paper_id, "doi",
                             hit.match_confidence, candidates)

    best_id: int | None = None
    best_conf = 0.0
    for guess in _title_guesses(text):
        hit = match_ref({"doi": "", "title": guess}, candidates)
        if hit.matched_paper_id is not None and hit.match_confidence > best_conf:
            best_id, best_conf = hit.matched_paper_id, hit.match_confidence
    if best_id is not None:
        return _as_match(path, best_id, "title", best_conf, candidates)

    from_name = _filename_title(path)
    if from_name:
        hit = match_ref({"doi": "", "title": from_name}, candidates)
        if hit.matched_paper_id is not None:
            return _as_match(path, hit.matched_paper_id, "filename",
                             hit.match_confidence, candidates)
    return None


def _as_match(path: str, paper_id: int, strategy: str, confidence: float,
              candidates: sqlite3.Connection) -> FolderMatch:
    row = candidates.execute(
        "SELECT title FROM papers WHERE id = ?", (paper_id,)
    ).fetchone()
    return FolderMatch(
        pdf_path=path,
        pdf_name=os.path.basename(path),
        paper_id=paper_id,
        paper_title=(row["title"] if row else "") or "",
        strategy=strategy,
        confidence=round(float(confidence), 3),
    )


def _read_text(path: str) -> str:
    """The first pages as text. A PDF that cannot be read costs its match,
    never the scan — the next file is still someone's missing attachment."""
    try:
        return extract_text_from_pdf(path, Config.MAX_OCR_PAGES) or ""
    except Exception as e:
        logging.warning("Textextraktion fuer %s fehlgeschlagen: %s", path, e)
        return ""


def _title_guesses(text: str) -> list[str]:
    """The leading text lines that could be a title, best guess first."""
    guesses: list[str] = []
    for raw in (text or "").splitlines()[:TITLE_SCAN_LINES]:
        line = re.sub(r"\s+", " ", raw).strip()
        if len(line) < 10 or _is_boilerplate(line):
            continue
        guesses.append(line[:200])
        if len(guesses) >= MAX_TITLE_GUESSES:
            break
    return guesses


def _is_boilerplate(line: str) -> bool:
    """Journal banners, DOI lines and page furniture are not titles."""
    lowered = line.lower()
    if "doi.org" in lowered or lowered.startswith("doi:") or "http" in lowered:
        return True
    if "@" in line:
        return True
    digits = sum(c.isdigit() for c in line)
    return digits > len(line) / 3


# "Smith - 2019 - ", "Smith et al. - 2019 - ", "2019_Smith_", "Smith_2019_"
_PREFIX_PATTERNS = (
    re.compile(r"^.{0,80}?\s[-–]\s(?:19|20)\d{2}\s[-–]\s"),
    re.compile(r"^(?:19|20)\d{2}[_\-\s]+"),
    re.compile(r"^[A-Za-zÀ-ÿ'\.\- ]{2,40}[_\-\s]+(?:19|20)\d{2}[_\-\s]+"),
)


def _filename_title(path: str) -> str:
    """The filename as a title guess: extension, author/year decoration and
    separators stripped. ``Smith - 2019 - A Study of Things.pdf`` →
    ``A Study of Things``."""
    stem = os.path.splitext(os.path.basename(path))[0]
    for pattern in _PREFIX_PATTERNS:
        stripped = pattern.sub("", stem, count=1)
        if stripped != stem and len(stripped) >= 10:
            stem = stripped
            break
    stem = re.sub(r"\s*\(\d+\)$", "", stem)          # "... (1)" from a re-download
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem
