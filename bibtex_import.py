"""BibTeX import — parse a .bib file into paper-shaped dicts and extract
the cite keys actually used in a LaTeX source.

Pure module, no I/O and no DB access (like ``paper_matcher`` /
``cite_key_generator``); ``webapp.py`` orchestrates matching and persistence.
See CONTEXT.md "BibTeX Import" and ADR-0003.
"""

from __future__ import annotations

import re

import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode

# \cite-family commands: \cite, \citep, \citet, \parencite, \textcite,
# \autocite, \footcite, \nocite, \citeauthor, starred and capitalised
# variants, with up to two optional [..] arguments. biblatex multicites
# (\cites{a}{b}) only yield their first key list — acceptable for a filter.
_CITE_RE = re.compile(
    r"\\[a-zA-Z]*[Cc]ite[a-zA-Z]*\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^{}]+)\}"
)


def parse_bib(text: str) -> list[dict]:
    """Parse BibTeX source into normalised entry dicts, in file order.

    Each entry: ``{key, entry_type, title, authors, year, doi, isbn,
    abstract, journal, publisher, raw}`` — ``authors`` joined with "; " in
    "Last, First" form (the library's storage format), ``raw`` the original
    field dict. Entries without a key are skipped.
    """
    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    parser.customization = convert_to_unicode
    database = bibtexparser.loads(text, parser=parser)

    entries = []
    for record in database.entries:
        key = (record.get("ID") or "").strip()
        if not key:
            continue
        entries.append({
            "key": key,
            "entry_type": (record.get("ENTRYTYPE") or "misc").lower(),
            "title": _clean(record.get("title")),
            "authors": format_authors(record.get("author", "")),
            "year": _year(record.get("year") or record.get("date")),
            "doi": normalize_doi(record.get("doi", "")),
            "isbn": _clean(record.get("isbn")),
            "abstract": _clean(record.get("abstract")),
            "journal": _clean(record.get("journal") or record.get("booktitle")),
            "publisher": _clean(record.get("publisher")),
            "raw": {k: v for k, v in record.items()},
        })
    return entries


def extract_cited_keys(tex: str) -> set[str]:
    """Cite keys referenced by \\cite-family commands in LaTeX source
    (comment-stripped, comma lists split)."""
    text = re.sub(r"(?<!\\)%.*", "", tex)  # drop line comments, keep \%
    keys: set[str] = set()
    for match in _CITE_RE.finditer(text):
        for key in match.group(1).split(","):
            key = key.strip()
            if key and key != "*":  # \nocite{*} is not a key
                keys.add(key)
    return keys


def format_authors(author_field: str) -> str:
    """BibTeX author field -> the library's "Last, First; Last, First" form."""
    names = []
    for name in re.split(r"\s+and\s+", author_field.strip()):
        literal = name.strip().startswith("{") and name.strip().endswith("}")
        name = _clean(name)
        if not name:
            continue
        if literal:  # braced org name, e.g. {Deutsche Bahn AG} — never flipped
            names.append(name)
        elif "," in name:
            names.append(re.sub(r"\s*,\s*", ", ", name))
        else:
            parts = name.split()
            if len(parts) > 1:
                names.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
            else:
                names.append(name)
    return "; ".join(names)


def normalize_doi(doi: str) -> str:
    doi = (doi or "").strip()
    if doi.startswith("http"):
        doi = doi.split("doi.org/")[-1]
    return doi.rstrip(".")


def _clean(value) -> str:
    """LaTeX field value -> plain text: drop grouping braces and leftover
    commands (accents were already resolved by convert_to_unicode)."""
    if not value:
        return ""
    text = re.sub(r"\\[a-zA-Z]+\s*", " ", str(value))
    text = text.replace("{", "").replace("}", "")
    text = text.replace("~", " ").replace("\\&", "&")
    return re.sub(r"\s+", " ", text).strip()


def _year(value) -> int | None:
    match = re.search(r"\d{4}", str(value or ""))
    return int(match.group(0)) if match else None
