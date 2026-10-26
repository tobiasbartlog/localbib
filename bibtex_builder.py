"""BibTeX rendering. Pure module, no I/O (like citation_graph_builder).

Single source for every place LocalBib emits BibTeX: the per-paper
endpoint, the .bib export, the add-ons' bibliographies and the
auto-generated bibliography fed to the LaTeX preview compiler
(docs/PRD-latex-editor.md).

What an entry carries follows ``bib_fields``: the entry type comes from the
Item's ``item_type`` (else ``bib_fields.infer_type``), the fields from its
columns. Two dialects:

* ``biblatex`` (default) — ``@online``, ``@patent``, ``@dataset``,
  ``@software``, ``@thesis`` with ``type``; ``date`` and ``urldate``.
* ``bibtex`` — the classic styles' types only: those four become ``@misc``
  with ``howpublished``, a thesis ``@phdthesis``/``@mastersthesis``; ``year``
  + ``month``, the access date in ``note``.

A standard (Norm) is ``@techreport`` in both, with ``number``,
``institution`` and ``type`` — the label comes from the caller's ``labels``
(the SPA catalog via ``bib_export``), never from a string in here beyond the
English default. Field names stay classic (``journal``, ``address``,
``school``): biblatex reads them as aliases.

Authors and editors go through ``author_names``: CrossRef's comma form
becomes one person per name, an organization is braced. ``pages`` is the
page range and never the PDF's page count.
"""

import json
import re
from typing import Dict, List, Mapping, Optional

import author_names
import bib_fields

_REPLACEMENTS = [
    ("\\", "\\textbackslash{}"),
    ("{", "\\{"),
    ("}", "\\}"),
    ("&", "\\&"),
    ("%", "\\%"),
    ("#", "\\#"),
    ("_", "\\_"),
    ("~", "\\textasciitilde{}"),
    ("^", "\\textasciicircum{}"),
    ("ä", '{\\"a}'),
    ("ö", '{\\"o}'),
    ("ü", '{\\"u}'),
    ("Ä", '{\\"A}'),
    ("Ö", '{\\"O}'),
    ("Ü", '{\\"U}'),
    ("ß", "{\\ss}"),
]

# The export's words, English default; ``bib_export.labels`` reads them from
# the catalog (``bibExport.*``) in the UI language.
DEFAULT_LABELS = {
    "standard": "Standard",
    "patent": "Patent {number}",
    "accessed": "Accessed {date}",
    "version": "Version {version}",
}

_BIBTEX_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_ARXIV_ID = re.compile(r"^(?:arxiv:)?(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)$", re.I)
_MASTERS = re.compile(r"master|mathesis|magister|diplom|bachelor|\bm\.?\s?(?:a|sc|eng)\b", re.I)
_RANGE_DASH = re.compile(r"\s*(?:-+|–|—)\s*")


def escape_latex(text: str) -> str:
    """Escapet Sonderzeichen fuer BibTeX/LaTeX."""
    if not text:
        return ""
    for old, new in _REPLACEMENTS:
        text = text.replace(old, new)
    return text


def _s(paper: Mapping, name: str) -> str:
    value = paper.get(name)
    return "" if value is None else str(value).strip()


def bib_name(name: str) -> str:
    """One person as "Family, Given"; an organization in braces, so BibTeX
    reads it as one name instead of a family name "Consortium"."""
    n = author_names.parse_name(name)
    if n.org:
        return "{" + escape_latex(n.last) + "}"
    return escape_latex(f"{n.last}, {n.given}" if n.given else n.last)


def people_field(value) -> str:
    """An authors/editors string in any stored form → BibTeX ``A and B``;
    one that names nobody (``bib_fields.is_empty``, e.g. ", ;") → ``""``."""
    if (value is None or isinstance(value, str)) and bib_fields.is_empty("authors", value):
        return ""
    names = (bib_name(p) for p in author_names.split_people(value))
    return " and ".join(n for n in names if n)


def holder_field(value) -> str:
    """A patent's ``institution`` (the form's "Holder") → biblatex's
    ``holder``. It is a name list, yet the column is a plain field like an
    institution: holders are separated by ";" only (never by a comma or an
    " and ", which belong to a company's name), and each one that reads as an
    organization — braced, or with a marker such as "GmbH", "AG", "Institute"
    even beside a comma — stays one braced name; a person ("Müller, Hans",
    "Hans Müller") is written as a person."""
    out = []
    for part in author_names.split_outside(str(value or ""), ";"):
        part = re.sub(r"\s+", " ", part).strip()
        if not part:
            continue
        if author_names.is_organization(part) or author_names.has_org_marker(part):
            name = part[1:-1].strip() if author_names.is_braced(part) else part
            out.append("{" + escape_latex(name) + "}")
        else:
            out.append(bib_name(part))
    return " and ".join(out)


def page_range(pages: str) -> str:
    """``45-67`` / ``45–67`` → ``45--67``; a single page stays."""
    return _RANGE_DASH.sub("--", pages.strip())


def _keywords(tags) -> str:
    if not tags:
        return ""
    try:
        parsed = json.loads(tags) if isinstance(tags, str) else tags
    except (TypeError, ValueError):
        parsed = [tags]
    if not isinstance(parsed, list):
        parsed = [parsed]
    return ", ".join(str(t).strip() for t in parsed if str(t).strip())


def entry_type(paper: Mapping, dialect: str = bib_fields.DEFAULT_DIALECT) -> str:
    """The ``@type`` of an Item in ``dialect``."""
    item = bib_fields.ITEM_TYPES[bib_fields.effective_type(paper)]
    if dialect != "bibtex":
        if item.id == "report" and not _s(paper, "thesis_type"):
            return "techreport"   # biblatex: @report needs a type, @techreport has one
        return item.biblatex
    if item.id == "thesis":
        return "mastersthesis" if _MASTERS.search(_s(paper, "thesis_type")) else "phdthesis"
    return item.bibtex


# Thesis types that are entry-type keys, not words: classic BibTeX prints a
# ``type`` verbatim (its @phdthesis/@mastersthesis already say it), biblatex
# localizes only its own keys (``phdthesis``, ``mathesis``).
_THESIS_KEYS = {"phdthesis": "phdthesis", "mastersthesis": "mathesis", "mathesis": "mathesis"}


def _type_label(kind: str, thesis_type: str, bibtex: bool) -> str:
    """The ``type`` field of a thesis, report or standard (else ``""``)."""
    if kind not in ("thesis", "report", "standard"):
        return ""
    key = _THESIS_KEYS.get(thesis_type.lower())
    if key and kind == "thesis":
        return "" if bibtex else key
    return thesis_type


def _fields(paper: Mapping, dialect: str, labels: Mapping[str, str]) -> List[tuple]:
    """``(name, value, escape)`` in output order, empty values dropped later."""
    kind = bib_fields.effective_type(paper)
    bibtex = dialect == "bibtex"
    as_misc = bibtex and bib_fields.ITEM_TYPES[kind].bibtex == "misc" and kind != "misc"
    def get(name: str) -> str:
        return _s(paper, name)

    journal, booktitle = get("journal"), get("booktitle")
    if kind in ("chapter", "conference"):
        booktitle, journal = booktitle or journal, ""
    if kind == "article":
        number = get("issue") or get("number")
    else:
        number = get("number") or get("issue")

    pub_date = bib_fields.normalize_date(get("pub_date"))
    # One emptiness rule (bib_fields.is_empty): a year of 0 is no year.
    stored_year = bib_fields.year_value(paper.get("year"))
    year = str(stored_year) if stored_year else (pub_date[:4] if pub_date else "")
    if pub_date[:4] != year:
        pub_date = ""   # a date of another year than the Item's: the year wins
    notes = [get("note_bib")]

    out: List[tuple] = [
        ("author", people_field(get("authors")), False),
        ("editor", people_field(get("editors")), False),
        ("title", get("title"), True),
        ("journal", journal, True),
        ("booktitle", booktitle, True),
        ("eventtitle", "" if bibtex else get("event"), True),
        ("edition", get("edition"), True),
        ("volume", get("volume"), True),
        ("number", number, True),
        ("series", get("series"), True),
        ("pages", page_range(get("pages")) if get("pages") else "", True),
    ]

    # type + institution: the field each entry type knows them by. A report
    # without a type is @techreport, which brings its own.
    type_label = _type_label(kind, get("thesis_type"), bibtex)
    if kind == "standard":
        type_label = type_label or labels["standard"]
    out.append(("type", type_label, True))
    institution = get("institution")
    if kind == "patent" and not bibtex:
        # The form calls it "Holder": biblatex's holder, a name list in which
        # an organization stays one braced name (holder_field).
        out.append(("holder", holder_field(institution), False))
    else:
        inst_field = {"thesis": "school", "report": "institution", "standard": "institution"}.get(
            kind, "organization")
        out.append((inst_field, institution, True))
    out += [("publisher", get("publisher"), True), ("address", get("place"), True)]

    if bibtex:
        month = int(pub_date[5:7]) if len(pub_date) >= 7 else 0
        out.append(("year", year, False))
        out.append(("month", _BIBTEX_MONTHS[month - 1] if month else "", "macro"))
    else:
        out.append(("date", pub_date, False) if len(pub_date) > 4 else ("year", year, False))

    out += [("isbn", get("isbn"), False), ("issn", get("issn"), False),
            ("doi", get("doi"), False)]

    eprint = get("eprint")
    if eprint:
        arxiv = _ARXIV_ID.match(eprint)
        out.append(("eprint", arxiv.group(1) if arxiv else eprint, False))
        if arxiv or get("doi").lower().startswith("10.48550/"):
            out.append(("archiveprefix", "arXiv", False) if bibtex else ("eprinttype", "arxiv", False))

    url, accessed = get("url"), bib_fields.normalize_date(get("accessed"))
    if as_misc:
        # Classic @misc knows no url: it goes into howpublished — unless a
        # patent's number takes that place, then url stays for natbib & Co.
        how = labels["patent"].replace("{number}", number) if kind == "patent" and number else ""
        out.append(("howpublished", escape_latex(how) if how else (f"\\url{{{url}}}" if url else ""), False))
        if how:
            out.append(("url", url, False))
    else:
        out.append(("url", url, False))
    if bibtex:
        if accessed:
            notes.append(labels["accessed"].replace("{date}", accessed))
        if get("version"):
            notes.append(labels["version"].replace("{version}", get("version")))
    else:
        out += [("urldate", accessed, False), ("version", get("version"), True)]

    out += [
        ("language", get("language"), True),
        ("note", "; ".join(n for n in notes if n), True),
        ("keywords", _keywords(paper.get("tags")), True),
        ("abstract", get("abstract")[:2000], True),
    ]
    return out


def format_entry(paper: Dict, key: Optional[str] = None, *,
                 dialect: str = bib_fields.DEFAULT_DIALECT,
                 labels: Optional[Mapping[str, str]] = None) -> str:
    """One entry for a paper dict; key defaults to paper['cite_key']."""
    key = key or paper.get("cite_key") or ""
    dialect = dialect if dialect in bib_fields.DIALECTS else bib_fields.DEFAULT_DIALECT
    labels = {**DEFAULT_LABELS, **(labels or {})}
    lines = [f"@{entry_type(paper, dialect)}{{{key},"]
    for name, value, escape in _fields(paper, dialect, labels):
        if not value:
            continue
        if escape == "macro":
            lines.append(f"  {name} = {value},")
        else:
            lines.append(f"  {name} = {{{escape_latex(value) if escape else value}}},")
    lines.append("}")
    return "\n".join(lines)


def build(papers: List[Dict], *, dialect: str = bib_fields.DEFAULT_DIALECT,
          labels: Optional[Mapping[str, str]] = None) -> str:
    """Full .bib file content for a list of paper dicts with cite_keys."""
    if not papers:
        return ""
    return "\n\n".join(format_entry(p, dialect=dialect, labels=labels) for p in papers) + "\n"
