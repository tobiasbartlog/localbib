"""People in a bibliographic field: split, join, read as BibTeX names (pure, stdlib only).

The library stores authors and editors as one string. Three forms are in the
wild and stay readable:

* ``"Family, Given; Family, Given"`` — the form every intake writes from now on
  (BibTeX, RIS, Zotero, OpenAlex; :func:`join_people`);
* ``"Family, Given and Family, Given"`` — a BibTeX field kept as written;
* ``"Family, Given, Family, Given"`` — CrossRef's form, which the library held
  for years. Stored rows are **not** rewritten (a comma-only string is
  ambiguous); :func:`split_people` reads it at export time.

The heuristic is the core twin of the ``split_authors`` an Add-on's
citation styles use: copied, not imported — a Bundle is never a core
dependency (P3) — and pinned by the same test cases. The core knows a few
more organization markers (grey literature: "Programme", "Bundesanstalt",
legal forms like "GmbH"), so an exported organization keeps its braces.

**A name written in braces is an organization** (``{Autobahn Westfalen}``):
the edit form says so, the BibTeX import keeps a braced corporate author as
written, and the export writes it braced. Separators inside braces separate
nobody. Storage keeps the braces; every *display* strips them
(:func:`display_people`). The SPA's form reads names with a JavaScript twin
of :func:`split_people`, pinned by ``tests/fixtures/people_vectors.json``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Union

#: The separator :func:`join_people` writes.
SEPARATOR = "; "

_PARTICLES = {"van", "von", "der", "den", "de", "del", "della", "di", "da", "du", "la", "le",
              "zu", "zum", "zur", "ter", "ten", "dos", "das"}
# An author that is an organization, not a person: CrossRef (and so the
# library) writes it on its own, without a given name.
_ORG_MARKERS = re.compile(
    r"(?<!\w)(consortium|organi[sz]ation|group|gruppe|arbeitsgruppe|arbeitskreis|institute?|"
    r"universit(?:y|ät|aet|é|at)|universidad|association|society|gesellschaft|committee|komitee|"
    r"collaboration|team|network|netzwerk|council|agency|foundation|stiftung|ministry|ministerium|"
    r"department|centre|center|zentrum|laborator(?:y|ies)|company|corporation|inc|ltd|gmbh|verband|"
    r"verein|commission|kommission|academy|akademie|alliance|federation|initiative|project|bundesamt)(?!\w)",
    re.IGNORECASE)
# Core additions to the twin's markers — the grey literature a .bib
# carries (UNEP, a Bundesanstalt, a GmbH): English body words, German
# compounds ("Umweltbundesamt", "Bundesministerium für …") and legal forms
# (case-sensitive, "AG" is no initial).
_ORG_MARKERS_MORE = re.compile(
    r"(?<!\w)(programme|program|nations|office|authority|administration|bureau|board|"
    r"bundes\w+|\w*(?:ministerium|amt|anstalt|verband|verwaltung|behörde|gesellschaft|institut|"
    r"zentrum|stiftung|agentur))(?!\w)", re.IGNORECASE)
_LEGAL_FORMS = re.compile(r"(?<!\w)(AG|SE|KG|GmbH|e\.\s?V\.|LLC|plc|Ltd\.?)(?!\w)")
_MAX_NAME_WORDS = 4   # more words (particles aside) than a person's name has: an organization


def has_org_marker(text: str) -> bool:
    """An organization word ("Institut", "Consortium", "GmbH", "AG" …)
    anywhere in ``text`` — comma or not."""
    return bool(_ORG_MARKERS.search(text) or _ORG_MARKERS_MORE.search(text) or _LEGAL_FORMS.search(text))


def is_braced(text: str) -> bool:
    """``{Whole Name}`` — one brace group from the first to the last character."""
    text = (text or "").strip()
    if not (text.startswith("{") and text.endswith("}")):
        return False
    depth = 0
    for i, ch in enumerate(text):
        depth += {"{": 1, "}": -1}.get(ch, 0)
        if depth == 0 and i < len(text) - 1:
            return False
    return True


def split_outside(text: str, separator: str) -> list[str]:
    """``text`` split at the regex ``separator`` where it stands outside
    braces (``r"\\s+and\\s+"`` splits a BibTeX name list)."""
    parts, depth, start = [], 0, 0
    for match in re.finditer(r"[{}]|" + separator, text):
        token = match.group(0)
        if token == "{":
            depth += 1
        elif token == "}":
            depth = max(depth - 1, 0)
        elif depth == 0:
            parts.append(text[start:match.start()])
            start = match.end()
    parts.append(text[start:])
    return parts


def organization(name: Union[str, None]) -> str:
    """An organization's name in the storage form: in braces (once), so no
    separator in it ever splits it — what a structured source (CrossRef's
    ``name`` alone, DataCite's ``Organizational``) writes; ``""`` for none."""
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    if not text:
        return ""
    return text if is_braced(text) else "{" + text + "}"


def display_people(value: Union[str, None]) -> str:
    """A stored authors/editors string as people read it: the braces that
    mark an organization taken out, nothing else changed."""
    return re.sub(r"[{}]", "", str(value or ""))


@dataclass(frozen=True)
class Name:
    last: str
    given: str = ""
    org: bool = False   # an organization: ``last`` is its whole name


def _words(text: str) -> list[str]:
    return [w for w in text.split() if w.casefold() not in _PARTICLES]


def is_organization(raw: str) -> bool:
    """A comma-free name with an organization marker (Consortium, Institut,
    University, …) or more words than a person's name has, particles aside.
    "Family, Given" is always a person ("Group, Hans"); a name in braces is
    always an organization."""
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if is_braced(text):
        return True
    if not text or "," in text:
        return False
    return has_org_marker(text) or len(_words(text)) > _MAX_NAME_WORDS


def parse_name(raw: str) -> Name:
    """"Family, Given" or "Given Family" (particles such as "van der" or
    "Da" belong to the family name); one word is a family name only; an
    organization stays whole — one in braces without them."""
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not text:
        return Name("")
    if is_braced(text):
        return Name(text[1:-1].strip(), org=True)
    if "," in text:
        last, _, given = text.partition(",")
        return Name(last.strip(), given.strip())
    if is_organization(text):
        return Name(text, org=True)
    tokens = text.split(" ")
    if len(tokens) == 1:
        return Name(tokens[0])
    i = len(tokens) - 1
    while i > 1 and tokens[i - 1].lower() in _PARTICLES:
        i -= 1
    return Name(" ".join(tokens[i:]), " ".join(tokens[:i]))


def _is_initial(word: str) -> bool:
    """"J.", "J", "J.-P.", "J.P." — initials, not a name ("Li" is a name)."""
    if len(word) == 1:
        return word.isalpha()
    return bool(re.fullmatch(r"[^\W\d_]\.(?:-?[^\W\d_]\.)*", word))


def _looks_like_given(part: str) -> bool:
    """Initials or one to four capitalized words, no organization marker:
    the given names after a family name in "Family, Given"."""
    words = part.split()
    if not words or len(words) > _MAX_NAME_WORDS or has_org_marker(part) or is_braced(part):
        return False
    return all(w[0].isalpha() and w[0].isupper() for w in words)


def _can_be_family(part: str) -> bool:
    """The part before a given name: one word always — even a marker word
    ("Group, Hans" is a person) —, several words unless they name an
    organization ("ACME Consortium"); never a name in braces."""
    if is_braced(part):
        return False
    return len(part.split()) == 1 or not is_organization(part)


def _looks_like_full_name(part: str) -> bool:
    """"Given Family" on its own: two or more words, not starting with a
    particle ("Da Silva" is a family name) and not ending in initials."""
    words = part.split()
    return (len(words) >= 2 and not is_organization(part)
            and words[0].casefold() not in _PARTICLES and not _is_initial(words[-1]))


_AND = r"\s+and\s+"


def _one_organization(text: str) -> bool:
    """A whole string that names one organization although it holds an
    " and " ("Food and Agriculture Organization"): no comma and no braces
    (those decide for themselves), :func:`is_organization` says so, and at
    least one " and " part is an organization by itself — "John Smith and
    Jane Doe" has more words than a name, yet both parts are people."""
    if "," in text or "{" in text or not is_organization(text):
        return False
    return any(is_organization(p) for p in split_outside(text, _AND))


def split_people(value: Union[str, Iterable[str], None]) -> list[str]:
    """One name per person (or organization), in order.

    ``;`` separates people, so does a BibTeX " and " — except inside one
    organization's name (:func:`_one_organization`). A comma-only string is
    CrossRef's form — "Family, Given" per person, organizations alone, joined
    by ", " — read from left to right: a part is the given name of the part
    before if it looks like one (initials or one to four capitalized words, no
    organization marker) and that part can be a family name. So two parts are
    one person ("García Márquez, Gabriel José"). Only an odd number of "Given
    Family" names, three or more, reads as such a list.

    A separator inside braces separates nobody: ``{Müller, Schmidt and
    Partner}`` is one organization."""
    if value is None:
        return []
    if not isinstance(value, str):
        return [str(v).strip() for v in value if str(v or "").strip()]
    text = value.strip()
    if not text:
        return []
    for separator in (";", _AND):
        if separator == _AND and _one_organization(re.sub(r"\s+", " ", text)):
            return [text]
        parts = split_outside(text, separator)
        if len(parts) > 1:
            return [p.strip() for p in parts if p.strip()]
    parts = [re.sub(r"\s+", " ", p).strip() for p in split_outside(text, ",") if p.strip()]
    if len(parts) <= 1:
        return parts
    people = [p for p in parts if not is_organization(p)]
    if len(people) >= 3 and len(people) % 2 and all(_looks_like_full_name(p) for p in people):
        return parts
    out: list[str] = []
    i = 0
    while i < len(parts):
        part = parts[i]
        nxt = parts[i + 1] if i + 1 < len(parts) else None
        if nxt is not None and _can_be_family(part) and _looks_like_given(nxt):
            out.append(f"{part}, {nxt}")
            i += 2
        else:
            out.append(part)
            i += 1
    return out


def join_people(people: Iterable[str]) -> str:
    """The library's storage form: ``"Family, Given; Family, Given"``."""
    return SEPARATOR.join(p.strip() for p in people if p and p.strip())


def normalize_people(value: Union[str, Iterable[str], None]) -> str:
    """Any accepted form → the storage form (BibTeX "and", CrossRef commas, a list)."""
    return join_people(split_people(value))
