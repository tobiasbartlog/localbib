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
from bibtexparser.latexenc import latex_to_unicode

from author_names import is_braced, split_outside

# Where ``parse_bib``'s customization parks the author field as written.
_VERBATIM_AUTHOR = "_localbib_author_verbatim"

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
    parser.customization = _keep_verbatim_author
    database = bibtexparser.loads(text, parser=parser)

    entries = []
    for record in database.entries:
        verbatim_author = record.pop(_VERBATIM_AUTHOR, None)
        key = (record.get("ID") or "").strip()
        if not key:
            continue
        entries.append({
            "key": key,
            "entry_type": (record.get("ENTRYTYPE") or "misc").lower(),
            "title": _clean(record.get("title")),
            "authors": format_authors(verbatim_author or record.get("author", "")),
            "year": _year(record.get("year") or record.get("date")),
            "doi": normalize_doi(record.get("doi", "")),
            "isbn": _clean(record.get("isbn")),
            "abstract": _clean(record.get("abstract")),
            "journal": _clean(record.get("journal") or record.get("booktitle")),
            "publisher": _clean(record.get("publisher")),
            "raw": {k: v for k, v in record.items()},
        })
    return entries


def parse_bib_verbatim(text: str) -> dict[str, dict]:
    """Cite key → the entry's fields **exactly as written**, no LaTeX→Unicode pass.

    ``parse_bib`` runs ``convert_to_unicode`` because a bibliographic field is
    LaTeX: ``M\\"uller`` must become ``Müller``. A local file path is not LaTeX,
    and the same pass eats it — ``C:\\citavi\\y.pdf`` turns into a combining
    cedilla. The migration adapters (PRD #173) therefore read the untouched
    record for the ``file`` field and keep the converted one for everything a
    human reads. Entries without a key are skipped, like in ``parse_bib``.
    """
    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    database = bibtexparser.loads(text, parser=parser)
    out: dict[str, dict] = {}
    for record in database.entries:
        key = (record.get("ID") or "").strip()
        if key:
            out[key] = dict(record)
    return out


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
    """BibTeX author field -> the library's "Last, First; Last, First" form.

    Give it the field **as written** (``parse_bib`` does): a name wrapped in
    braces whole — ``{United Nations Environment Programme}`` — is an
    organization and keeps its braces (the library's mark for one, see
    ``author_names``), so the entry exports as it came in; an " and " inside
    braces separates nobody. The LaTeX→Unicode pass happens here, per name,
    because it drops exactly those braces."""
    names = []
    for name in split_outside(author_field.strip(), r"\s+and\s+"):
        literal = is_braced(name.strip())
        name = _clean(latex_to_unicode(name))
        if not name:
            continue
        if literal:  # braced org name, e.g. {Deutsche Bahn AG} — never flipped
            names.append("{" + name + "}")
        elif "," in name:
            names.append(re.sub(r"\s*,\s*", ", ", name))
        else:
            parts = name.split()
            if len(parts) > 1:
                names.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
            else:
                names.append(name)
    return "; ".join(names)


# A DOI as people write it: a resolver link in front (any doi.org host, with
# or without scheme, any case) or the `doi:` scheme, a sentence's full stop
# behind. The SPA's DOI list (#227) mirrors this rule; the shared vectors in
# tests/fixtures/doi_vectors.json pin both sides.
_DOI_PREFIX_RE = re.compile(r"^(?:doi:\s*|(?:https?://)?(?:[a-z0-9-]+\.)*doi\.org/)", re.IGNORECASE)


def normalize_doi(doi: str) -> str:
    return _DOI_PREFIX_RE.sub("", (doi or "").strip()).rstrip(".")


def _keep_verbatim_author(record: dict) -> dict:
    """``convert_to_unicode`` with the author field kept as written, too
    (it strips the braces that mark an organization)."""
    verbatim = record.get("author")
    record = convert_to_unicode(record)
    if verbatim is not None:
        record[_VERBATIM_AUTHOR] = verbatim
    return record


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
