"""Export building block (PRD #200 / #202): BibTeX, RIS, CSV and the ZIP plan.

Pure and transport-free (ADR-0006): every function takes the Items (paper
dicts as ``Database.get_all_papers`` returns them) plus whatever lookups the
caller already read, and returns text, bytes or a plan. Nothing here opens a
file, a connection or a socket — ``routers/export.py`` reads the rows, checks
which PDFs exist on disk and writes the ZIP from the plan.

* ``bibtex_text`` / ``ris_text`` — the existing builders, shared by the
  ``.bib``/``.ris`` downloads and the ZIP so both say the same thing.
* ``csv_bytes`` — core metadata incl. Cite Key and Categories, every Custom
  Field, and the Collection note when a Collection is the scope. UTF-8 with a
  BOM so Excel opens umlauts correctly.
* ``zip_plan`` — which PDF goes in under which name (``<citekey>.pdf``,
  collisions get ``-2``, ``-3`` …, an Item without a Cite Key falls back to
  the legacy key or ``item-<id>``), and the ``fehlende_pdfs.txt`` listing.
"""

from __future__ import annotations

import csv
import io
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Optional

import bibtex_builder
from cite_key_generator import base_key as _legacy_cite_key_base

BOM = "﻿"
MISSING_PDFS_NAME = "fehlende_pdfs.txt"
DEFAULT_BASENAME = "literatur_export"

CSV_CORE_COLUMNS = (
    "id", "cite_key", "title", "authors", "year", "journal", "publisher",
    "doi", "isbn", "categories", "has_pdf", "abstract",
)
NOTE_COLUMN = "collection_note"

_FILENAME_UNSAFE = re.compile(r"[^\w.-]+", re.UNICODE)
# Spreadsheet formula triggers (CSV injection): metadata from CrossRef or a
# PDF must never execute as a formula when the file is opened in Excel.
_FORMULA_START = ("=", "+", "@", "\t", "\r")


# ---------------------------------------------------------------------------
# Cite keys and file names
# ---------------------------------------------------------------------------

def cite_key(paper: dict) -> str:
    """Stored Cite Key; legacy fallback for rows from before the backfill."""
    return paper.get("cite_key") or _legacy_cite_key_base(
        paper.get("authors") or "", paper.get("year")
    )


def safe_basename(text: str, fallback: str = DEFAULT_BASENAME) -> str:
    """An ASCII-safe file stem (Content-Disposition, ZIP members)."""
    text = unicodedata.normalize("NFKD", (text or "").replace("ß", "ss"))
    text = text.encode("ascii", "ignore").decode("ascii")
    stem = _FILENAME_UNSAFE.sub("_", text.strip()).strip("._")
    return stem[:80] or fallback


def _pdf_stem(paper: dict) -> str:
    key = (paper.get("cite_key") or "").strip()
    if not key and (paper.get("authors") or paper.get("year")):
        key = _legacy_cite_key_base(paper.get("authors") or "", paper.get("year"))
    return safe_basename(key, fallback=f"item-{paper.get('id')}")


# ---------------------------------------------------------------------------
# BibTeX / RIS
# ---------------------------------------------------------------------------

def bibtex_text(papers: Iterable[dict]) -> str:
    entries = [bibtex_builder.format_entry(p, cite_key(p)) for p in papers]
    return "\n\n".join(entries) + "\n"


def ris_text(papers: Iterable[dict], all_dir: str) -> str:
    """RIS (Citavi, Zotero, Mendeley). ``all_dir`` resolves the ``L1`` link."""
    lines = []
    for p in papers:
        lines.append("TY  - JOUR")
        if p.get("title"):
            lines.append(f"T1  - {p['title']}")
        if p.get("authors"):
            for author in re.split(r"[;]", p["authors"]):
                author = author.strip()
                if author:
                    lines.append(f"AU  - {author}")
        if p.get("year"):
            lines.append(f"PY  - {p['year']}///")
        if p.get("doi"):
            lines.append(f"DO  - {p['doi']}")
        if p.get("journal"):
            lines.append(f"JO  - {p['journal']}")
        if p.get("abstract"):
            lines.append(f"AB  - {p['abstract']}")
        if p.get("publisher"):
            lines.append(f"PB  - {p['publisher']}")
        if p.get("isbn"):
            lines.append(f"SN  - {p['isbn']}")
        if p.get("filename"):
            filepath = os.path.join(all_dir, p["filename"])
            lines.append(f"L1  - file://{filepath}")
        lines.append("ER  - ")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def _cell(value) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_FORMULA_START):
        return "'" + text
    return text


def csv_bytes(
    papers: Iterable[dict],
    *,
    categories: Optional[dict[int, list[str]]] = None,
    custom_fields: Iterable[dict] = (),
    custom_values: Optional[dict[int, dict[int, str]]] = None,
    notes: Optional[dict[int, str]] = None,
) -> bytes:
    """One row per Item. ``custom_fields`` are ``{id, name}`` in column order;
    ``notes`` given (a Collection is the scope) adds the note column."""
    categories = categories or {}
    custom_values = custom_values or {}
    fields = list(custom_fields)
    header = list(CSV_CORE_COLUMNS) + [f["name"] for f in fields]
    if notes is not None:
        header.append(NOTE_COLUMN)

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(header)
    for p in papers:
        pid = p.get("id")
        row = [
            pid,
            cite_key(p),
            p.get("title"),
            p.get("authors"),
            p.get("year"),
            p.get("journal"),
            p.get("publisher"),
            p.get("doi"),
            p.get("isbn"),
            "; ".join(categories.get(pid, [])),
            "yes" if p.get("filename") else "no",
            p.get("abstract"),
        ]
        values = custom_values.get(pid, {})
        row += [values.get(f["id"], "") for f in fields]
        if notes is not None:
            row.append(notes.get(pid, ""))
        writer.writerow([_cell(v) for v in row])
    return (BOM + buf.getvalue()).encode("utf-8")


# ---------------------------------------------------------------------------
# ZIP plan
# ---------------------------------------------------------------------------

@dataclass
class ZipPlan:
    """What the ZIP holds: ``pdfs`` as ``(paper_id, member name)`` pairs, the
    Items without a PDF, and the text of ``fehlende_pdfs.txt``."""

    pdfs: list[tuple[int, str]] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)
    missing_text: str = ""


def pdf_member_names(papers: Iterable[dict]) -> dict[int, str]:
    """``{paper_id: "<citekey>.pdf"}``; a collision (case-insensitive, as on
    Windows) gets ``-2``, ``-3`` … in list order."""
    names: dict[int, str] = {}
    taken: set[str] = set()
    for p in papers:
        stem = _pdf_stem(p)
        name, n = f"{stem}.pdf", 1
        while name.lower() in taken:
            n += 1
            name = f"{stem}-{n}.pdf"
        taken.add(name.lower())
        names[p["id"]] = name
    return names


def zip_plan(papers: Iterable[dict], pdf_available: Iterable[int]) -> ZipPlan:
    """``pdf_available``: ids whose PDF the caller found on disk."""
    papers = list(papers)
    have = set(pdf_available)
    with_pdf = [p for p in papers if p.get("id") in have]
    names = pdf_member_names(with_pdf)
    plan = ZipPlan(pdfs=[(p["id"], names[p["id"]]) for p in with_pdf])
    plan.missing = [p for p in papers if p.get("id") not in have]
    lines = []
    for p in plan.missing:
        year = f" ({p['year']})" if p.get("year") else ""
        lines.append(f"{cite_key(p)}\t{(p.get('title') or '').strip()}{year}")
    plan.missing_text = "\r\n".join(lines) + ("\r\n" if lines else "")
    return plan
