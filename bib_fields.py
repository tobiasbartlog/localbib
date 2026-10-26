"""Item types and bibliographic fields — the one table (pure, stdlib only).

Leaf module like ``detail_layout``: every place that needs to know which
fields an Item of a type carries asks here — the ``PUT /api/papers/{id}``
whitelist, the Refresh Review, the metadata apply, the BibTeX builder, the
importers and the SPA (``GET /api/papers/item-types``). There is no second
copy.

* :data:`ITEM_TYPES` — per type the fields in form order, the fields a
  bibliography needs (``required``; ``"a|b"`` means either) and the entry type
  per export dialect.
* :data:`WRITABLE_FIELDS` — the metadata columns a user or a reviewed
  proposal may write (the eight historic ones plus :data:`BIB_COLUMNS`).
* :func:`effective_type` / :func:`infer_type` — an Item's type; rows from
  before the type column get the old guess, extended by ``booktitle`` and
  ``thesis_type``.
* :func:`missing_fields` — what the bibliography still lacks.
* :func:`columns_from_raw` / :func:`import_columns` — an import's raw record
  (BibTeX, RIS tags, Zotero RDF) as column values; also the one-time backfill
  of existing rows.

Dates: ``pub_date`` and ``accessed`` hold ISO dates, as precise as known
(``2024``, ``2024-03``, ``2024-03-15``); ``year`` stays and follows
``pub_date``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from author_names import join_people, split_people

CORE_FIELDS = ("title", "authors", "year", "doi", "isbn", "abstract", "journal", "publisher")
"""The eight metadata columns the library always had."""

BIB_COLUMNS = (
    "item_type", "volume", "issue", "pages", "pub_date", "editors", "booktitle",
    "edition", "place", "series", "issn", "url", "accessed", "institution",
    "thesis_type", "number", "event", "version", "language", "eprint", "note_bib",
)
"""The bibliographic columns (all ``TEXT DEFAULT ''``)."""

WRITABLE_FIELDS = CORE_FIELDS + BIB_COLUMNS
"""Metadata columns a user edit or a reviewed proposal may write."""

DATE_FIELDS = ("pub_date", "accessed")

DIALECTS = ("biblatex", "bibtex")
DEFAULT_DIALECT = "biblatex"


@dataclass(frozen=True)
class ItemType:
    id: str
    fields: tuple          # form order
    required: tuple        # "a|b": either one
    biblatex: str          # entry type per dialect
    bibtex: str
    # What a field is called for this type when its general name does not fit
    # (a conference paper's booktitle is its proceedings): field → label id,
    # the SPA's catalog key ``bib.label.<id>``. A field not named here is
    # called ``bib.field.<field>``.
    labels: tuple = ()

    def label_ids(self) -> Dict[str, str]:
        return dict(self.labels)


_TAIL = ("language", "abstract", "note_bib")

ITEM_TYPES: Dict[str, ItemType] = {t.id: t for t in (
    ItemType("article",
             ("title", "authors", "journal", "year", "pub_date", "volume", "issue", "pages",
              "doi", "issn", "url", "accessed") + _TAIL,
             ("authors", "title", "journal", "year", "volume", "pages"), "article", "article"),
    ItemType("book",
             ("title", "authors", "editors", "edition", "place", "publisher", "year", "pub_date",
              "series", "volume", "isbn", "doi", "url", "accessed") + _TAIL,
             ("authors|editors", "title", "publisher", "year"), "book", "book"),
    ItemType("chapter",
             ("title", "authors", "booktitle", "editors", "edition", "place", "publisher", "year",
              "pub_date", "pages", "series", "volume", "isbn", "doi", "url", "accessed") + _TAIL,
             ("authors", "title", "booktitle", "publisher", "year", "pages"),
             "incollection", "incollection"),
    ItemType("conference",
             ("title", "authors", "booktitle", "event", "editors", "place", "publisher", "year",
              "pub_date", "pages", "series", "volume", "isbn", "doi", "url", "accessed") + _TAIL,
             ("authors", "title", "booktitle", "year"), "inproceedings", "inproceedings",
             (("booktitle", "proceedings"),)),
    ItemType("thesis",
             ("title", "authors", "thesis_type", "institution", "place", "year", "pub_date",
              "doi", "isbn", "url", "accessed") + _TAIL,
             ("authors", "title", "thesis_type", "institution", "year"), "thesis", "phdthesis",
             (("institution", "university"),)),
    ItemType("report",
             ("title", "authors", "institution", "thesis_type", "number", "place", "year",
              "pub_date", "doi", "url", "accessed") + _TAIL,
             ("authors|institution", "title", "institution", "year"), "report", "techreport",
             (("thesis_type", "reportType"), ("number", "reportNumber"))),
    ItemType("preprint",
             ("title", "authors", "year", "pub_date", "eprint", "version", "publisher", "doi",
              "url", "accessed") + _TAIL,
             ("authors", "title", "year", "eprint|doi|url"), "online", "misc",
             (("publisher", "preprintServer"),)),
    ItemType("webpage",
             ("title", "authors", "institution", "url", "accessed", "year", "pub_date") + _TAIL,
             ("title", "url", "accessed"), "online", "misc",
             (("institution", "website"),)),
    ItemType("standard",
             ("title", "number", "institution", "year", "pub_date", "publisher", "place", "url",
              "accessed") + _TAIL,
             ("title", "number", "institution", "year"), "techreport", "techreport",
             (("institution", "standardsBody"), ("number", "standardNumber"))),
    ItemType("patent",
             ("title", "authors", "number", "institution", "place", "year", "pub_date", "url",
              "accessed") + _TAIL,
             ("authors", "title", "number", "year"), "patent", "misc",
             (("authors", "inventors"), ("institution", "holder"), ("number", "patentNumber"))),
    ItemType("dataset",
             ("title", "authors", "publisher", "year", "pub_date", "version", "doi", "url",
              "accessed") + _TAIL,
             ("authors", "title", "publisher", "year", "doi|url"), "dataset", "misc",
             (("publisher", "repository"),)),
    ItemType("software",
             ("title", "authors", "version", "publisher", "year", "pub_date", "doi", "url",
              "accessed") + _TAIL,
             ("authors", "title", "year", "doi|url"), "software", "misc"),
    ItemType("misc",
             ("title", "authors", "year", "pub_date", "publisher", "place", "doi", "url",
              "accessed") + _TAIL,
             ("title",), "misc", "misc"),
)}


def wire_table() -> dict:
    """The table as the SPA reads it (``GET /api/papers/item-types``)."""
    return {
        "types": [{"id": t.id, "fields": list(t.fields), "required": list(t.required),
                   "entry_types": {"biblatex": t.biblatex, "bibtex": t.bibtex},
                   "labels": t.label_ids()}
                  for t in ITEM_TYPES.values()],
        "fields": list(WRITABLE_FIELDS),
        "date_fields": list(DATE_FIELDS),
        "dialects": list(DIALECTS),
    }


# ---------------------------------------------------------------------------
# Type of an Item
# ---------------------------------------------------------------------------

def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value).strip()


def infer_type(paper: Mapping[str, Any]) -> str:
    """The type of a row that has none: the export's old guess (journal →
    article, ISBN + publisher → book, else misc), extended by a thesis type
    and a book title (with editors or an ISBN a chapter, else a conference
    paper)."""
    if _text(paper.get("thesis_type")):
        return "thesis"
    if _text(paper.get("booktitle")):
        return "chapter" if (_text(paper.get("editors")) or _text(paper.get("isbn"))) else "conference"
    if _text(paper.get("journal")):
        return "article"
    if _text(paper.get("isbn")) and _text(paper.get("publisher")):
        return "book"
    return "misc"


def effective_type(paper: Mapping[str, Any]) -> str:
    """The stored ``item_type`` when it is one of :data:`ITEM_TYPES`, else :func:`infer_type`."""
    stored = _text(paper.get("item_type"))
    return stored if stored in ITEM_TYPES else infer_type(paper)


def year_value(value: Any) -> Optional[int]:
    """A stored year as int; ``0`` (an old "unknown"), junk and nothing are no year."""
    text = _text(value)
    try:
        year = int(text[:4]) if text else None
    except ValueError:
        return None
    return year if year and year > 0 else None


def is_empty(field: str, value: Any) -> bool:
    """The one emptiness rule of a field (missing fields, the export, the
    lookup's fills): a year of ``0`` is no year; an author or editor string
    made of commas, semicolons and spaces only names nobody (#227)."""
    if field == "year":
        return year_value(value) is None
    text = _text(value)
    if field in ("authors", "editors"):
        return not re.sub(r"[\s,;]+", "", text)
    return not text


def _filled(paper: Mapping[str, Any], name: str) -> bool:
    if name == "year":
        return not (is_empty("year", paper.get("year")) and is_empty("pub_date", paper.get("pub_date")))
    return not is_empty(name, paper.get(name))


def missing_fields(paper: Mapping[str, Any]) -> List[str]:
    """The required fields of the Item's type that are empty, in table order;
    for an either-or group its first field."""
    out = []
    for group in ITEM_TYPES[effective_type(paper)].required:
        names = group.split("|")
        if not any(_filled(paper, n) for n in names):
            out.append(names[0])
    return out


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

# Month names and their abbreviations (English, German), whole words only:
# "Marine" is no March.
_MONTHS = {
    "jan": 1, "january": 1, "januar": 1, "jänner": 1, "jaenner": 1,
    "feb": 2, "february": 2, "februar": 2,
    "mar": 3, "march": 3, "mär": 3, "märz": 3, "maer": 3, "maerz": 3,
    "apr": 4, "april": 4,
    "may": 5, "mai": 5,
    "jun": 6, "june": 6, "juni": 6,
    "jul": 7, "july": 7, "juli": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "okt": 10, "oktober": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12, "dez": 12, "dezember": 12,
}
_ISO_DATE = re.compile(r"^(\d{4})(?:[-/.](\d{1,2})(?:[-/.](\d{1,2}))?)?[-/.]*(?:[T ].*)?$")


def _valid(year: int, month: Optional[int], day: Optional[int]) -> bool:
    if month is not None and not 1 <= month <= 12:
        return False
    if day is not None:
        days = [31, 29 if (year % 4 == 0 and (year % 100 or year % 400 == 0)) else 28,
                31, 30, 31, 30, 31, 31, 30, 31, 30, 31][(month or 1) - 1]
        if not 1 <= day <= days:
            return False
    return True


def normalize_date(text: Any) -> str:
    """``2024``, ``2024-3``, ``2024/03/15/`` (RIS), ``2024-03-15T…`` → the ISO
    form as precise as given; anything else (or an impossible date) → ``""``."""
    value = _text(text)
    match = _ISO_DATE.match(value)
    if not match:
        return ""
    year = int(match.group(1))
    month = int(match.group(2)) if match.group(2) else None
    day = int(match.group(3)) if match.group(3) else None
    if not _valid(year, month, day):
        return ""
    out = f"{year:04d}"
    if month:
        out += f"-{month:02d}"
        if day:
            out += f"-{day:02d}"
    return out


def month_number(value: Any) -> Optional[int]:
    """A BibTeX ``month`` (``3``, ``mar``, ``March``, ``März``, ``Sept.``) →
    1–12; a word that is no month name or abbreviation as a whole → ``None``."""
    text = _text(value).casefold().strip("{} .")
    if text.isdigit():
        number = int(text)
        return number if 1 <= number <= 12 else None
    return _MONTHS.get(text)


def year_of(pub_date: Any) -> Optional[int]:
    match = re.match(r"^(\d{4})", _text(pub_date))
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# The rules of every metadata write
# ---------------------------------------------------------------------------

class InvalidValue(ValueError):
    """A value the rules refuse: ``code`` is the catalog's error key, ``params``
    its placeholders — the caller answers 422 with both."""

    def __init__(self, code: str, params: Mapping[str, Any]):
        super().__init__(code)
        self.code = code
        self.params = dict(params)


def checked_changes(changes: Mapping[str, Any], stored: Mapping[str, Any]) -> Dict[str, Any]:
    """The rules every write of metadata fields follows — ``PUT
    /api/papers/{id}`` and the reviewed proposal
    (``validation_policy.validate_apply``, so the Refresh Review too). One
    helper, so the paths cannot drift:

    * ``None`` means "not given" and is dropped; a bibliographic column is a
      stripped string ("" clears it);
    * ``item_type`` is one of :data:`ITEM_TYPES` (or "" for the guess);
    * ``pub_date``/``accessed`` are ISO dates as precise as given;
    * ``year`` follows a given ``pub_date`` — the two never disagree. A new
      year alone outdates a stored date of another year: it is cleared, or the
      export (which prefers the date) would keep the old one. A cleared date
      without a year beside it clears the year too (the form shows the year
      in the date field): ``year`` is then ``""``, which the writer stores as
      ``NULL`` — the library's "no year".

    Returns the changes to write; raises :class:`InvalidValue`."""
    out = {k: v for k, v in changes.items() if v is not None}
    for field in BIB_COLUMNS:
        if field not in out:
            continue
        value = _text(out[field])
        if field == "item_type" and value and value not in ITEM_TYPES:
            raise InvalidValue("error.invalid_item_type", {"value": value})
        if field in DATE_FIELDS and value:
            normalized = normalize_date(value)
            if not normalized:
                raise InvalidValue("error.invalid_date", {"field": field, "value": value})
            value = normalized
        out[field] = value
    if out.get("pub_date"):
        out["year"] = year_of(out["pub_date"])
    elif "pub_date" in out and "year" not in out:
        out["year"] = ""   # the date field held the year: cleared with it
    elif "year" in out and "pub_date" not in out:
        year = year_of(out["year"])
        stored_date = _text(stored.get("pub_date"))
        if year is not None and stored_date and year_of(stored_date) != year:
            out["pub_date"] = ""
    return out


def valid_changes(changes: Mapping[str, Any], stored: Mapping[str, Any]) -> Dict[str, Any]:
    """:func:`checked_changes` for a write nobody reviews value by value (an
    enrichment, the Refresh Review's gap fills): a value the rules refuse is
    dropped instead of failing the write, the rest follows the rules. A
    cleared year (``""``) comes back as ``None`` — the library's "no year"."""
    pending = dict(changes)
    while pending:
        try:
            out = checked_changes(pending, stored)
        except InvalidValue as exc:
            field = exc.params.get("field") or "item_type"
            if field not in pending:
                return {}
            pending.pop(field)
            continue
        if out.get("year") == "":
            out["year"] = None
        return out
    return {}


# ---------------------------------------------------------------------------
# Imports: a source's raw record → column values
# ---------------------------------------------------------------------------

# Entry types of every source (BibTeX/biblatex, RIS ``TY``, Zotero ``itemType``),
# lower-cased, → Item type.
ENTRY_TYPE_MAP = {
    # BibTeX / biblatex
    "article": "article", "periodical": "article",
    "book": "book", "mvbook": "book", "booklet": "book", "collection": "book",
    "mvcollection": "book", "proceedings": "book", "mvproceedings": "book", "reference": "book",
    "incollection": "chapter", "inbook": "chapter", "inreference": "chapter",
    "bookinbook": "chapter", "suppbook": "chapter", "suppcollection": "chapter",
    "chapter": "chapter",
    "inproceedings": "conference", "conference": "conference",
    "thesis": "thesis", "phdthesis": "thesis", "mastersthesis": "thesis",
    "report": "report", "techreport": "report",
    "online": "webpage", "electronic": "webpage", "www": "webpage", "webpage": "webpage",
    "patent": "patent", "dataset": "dataset", "data": "dataset",
    "software": "software", "code": "software", "standard": "standard",
    "preprint": "preprint", "misc": "misc", "unpublished": "misc", "manual": "misc",
    # RIS
    "jour": "article", "ejour": "article", "mgzn": "article", "news": "article",
    "ebook": "book", "edbook": "book", "chap": "chapter", "echap": "chapter",
    "conf": "conference", "cpaper": "conference", "thes": "thesis", "rprt": "report",
    "elec": "webpage", "web": "webpage", "blog": "webpage", "pat": "patent",
    "dbase": "dataset", "comp": "software", "stand": "standard", "gen": "misc", "unpb": "misc",
    # Zotero
    "journalarticle": "article", "magazinearticle": "article", "newspaperarticle": "article",
    "booksection": "chapter", "conferencepaper": "conference", "blogpost": "webpage",
    "forumpost": "webpage", "computerprogram": "software", "document": "misc",
    "manuscript": "misc",
}

# A thesis type BibTeX says through its entry type only.
_THESIS_TYPES = {"phdthesis": "phdthesis", "mastersthesis": "mastersthesis"}
_ISSN = re.compile(r"^\d{4}-?\d{3}[\dXx]$")
_RIS_CODE = re.compile(r"^[A-Z]{3,6}$")


def item_type_for(entry_type: Any) -> str:
    """A source's entry type → Item type, ``""`` when unknown."""
    return ENTRY_TYPE_MAP.get(_text(entry_type).lower(), "")


def _lowered(raw: Mapping[str, Any]) -> Dict[str, Any]:
    return {str(k).lower(): v for k, v in (raw or {}).items()}


def _pick(lk: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = _text(lk.get(name))
        if value:
            return value
    return ""


def _people(lk: Mapping[str, Any], *names: str) -> str:
    """Every person under the first name that has any — a repeated RIS tag
    (``ED``) comes as a list, one person each — in the storage form."""
    for name in names:
        value = lk.get(name)
        if isinstance(value, (list, tuple)):
            people = [p for v in value for p in split_people(_text(v))]
        else:
            people = split_people(_text(value))
        if people:
            return join_people(people)
    return ""


def _legacy_ris(lk: Mapping[str, Any]) -> bool:
    """A row of the old ``POST /api/import/ris``: ``type`` holds the RIS code
    (upper case), the other tags sit beside it — no BibTeX or Zotero type."""
    legacy = _text(lk.get("type"))
    return ("entrytype" not in lk and "itemtype" not in lk
            and bool(_RIS_CODE.match(legacy)) and bool(item_type_for(legacy)))


def _source_type(lk: Mapping[str, Any], entry_type: str) -> str:
    """The most specific entry type a record names: Zotero's ``itemType``,
    BibTeX's ``ENTRYTYPE``, RIS's ``TY``, else the caller's, else an old RIS
    import's ``type`` (an upper-case code, not a thesis type)."""
    for name in ("itemtype", "entrytype", "ty"):
        if item_type_for(lk.get(name)):
            return _text(lk.get(name))
    if item_type_for(entry_type):
        return entry_type
    legacy = _text(lk.get("type"))
    if _RIS_CODE.match(legacy) and item_type_for(legacy):
        return legacy
    return entry_type or ""


def _pub_date(lk: Mapping[str, Any], is_ris: bool) -> str:
    """A date more precise than the year: biblatex ``date``, RIS ``DA``/``PY``,
    Zotero ``date``, or BibTeX ``year`` + ``month``."""
    for name in (("da", "py", "y1") if is_ris else ("date",)):
        value = normalize_date(lk.get(name))
        if len(value) > 4:
            return value
    if not is_ris:
        year = year_of(lk.get("year"))
        month = month_number(lk.get("month"))
        if year and month:
            return f"{year:04d}-{month:02d}"
    return ""


def columns_from_raw(raw: Optional[Mapping[str, Any]], entry_type: str = "") -> Dict[str, str]:
    """The bibliographic columns a raw import record carries — only filled
    ones. Reads BibTeX/biblatex field names, RIS tags and Zotero RDF names;
    never invents a value (a thesis type only from the entry type that says
    it). ``note`` is not read: the migration takes it as the reader's notes."""
    lk = _lowered(raw or {})
    is_ris = "ty" in lk or _legacy_ris(lk)
    source = _source_type(lk, entry_type)
    item_type = item_type_for(source)
    out: Dict[str, str] = {}
    if item_type:
        out["item_type"] = item_type

    if is_ris:
        start, end = _pick(lk, "sp"), _pick(lk, "ep")
        sn = _pick(lk, "sn")
        values = {
            "volume": _pick(lk, "vl"),
            "issue": _pick(lk, "is"),
            "pages": f"{start}-{end}" if start and end else start,
            "place": _pick(lk, "cy"),
            "edition": _pick(lk, "et"),
            "series": _pick(lk, "t3"),
            "url": _pick(lk, "ur"),
            "issn": sn if _ISSN.match(sn) else "",
            "language": _pick(lk, "la"),
            "editors": _people(lk, "ed"),
        }
        if item_type in ("chapter", "conference"):
            values["booktitle"] = _pick(lk, "bt", "t2")
    else:
        number = _pick(lk, "number")
        values = {
            "volume": _pick(lk, "volume"),
            "issue": _pick(lk, "issue") or (number if item_type in ("article", "") else ""),
            "number": "" if item_type in ("article", "") else number,
            "pages": _pick(lk, "pages", "page"),
            "editors": _people(lk, "editor", "zotero:editors"),
            "booktitle": _pick(lk, "booktitle"),
            "edition": _pick(lk, "edition"),
            "place": _pick(lk, "address", "location"),
            "series": _pick(lk, "series"),
            "institution": _pick(lk, "institution", "school", "organization"),
            "url": _pick(lk, "url", "zotero:url"),
            "accessed": normalize_date(_pick(lk, "urldate")),
            "issn": _pick(lk, "issn", "zotero:issn"),
            "event": _pick(lk, "eventtitle"),
            "version": _pick(lk, "version"),
            "language": _pick(lk, "language"),
            "eprint": _pick(lk, "eprint"),
        }
        if item_type in ("thesis", "report"):
            values["thesis_type"] = _pick(lk, "type") or _THESIS_TYPES.get(source.lower(), "")
    values["pub_date"] = _pub_date(lk, is_ris)
    out.update({k: v for k, v in values.items() if v})
    return out


def import_columns(entry_type: str, raw: Optional[Mapping[str, Any]],
                   journal: str = "") -> Dict[str, str]:
    """:func:`columns_from_raw` for a new row of an import, plus the venue in
    the right column: a chapter's or conference paper's container is its
    ``booktitle``, never its ``journal`` (the BibTeX preview, RIS ``T2`` and
    Zotero's ``isPartOf`` all name it as the journal). Returns ``journal: ""``
    when it moved, so the caller's row takes it from here."""
    out = columns_from_raw(raw, entry_type)
    if out.get("item_type") in ("chapter", "conference") and journal:
        booktitle = out.get("booktitle") or journal
        out["booktitle"] = booktitle
        if journal == booktitle:
            out["journal"] = ""
    return out
