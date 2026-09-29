"""Migration adapter: Zotero RDF export → :class:`MigrationItem` (PURE).

The export a reader produces with *Export Library… → Format: Zotero RDF* and
*Export Files* ticked: one ``.rdf`` document next to a ``files/`` tree holding
the attachments. It is the only Zotero format that carries the whole library —
collections, tags, notes and the local PDFs — which is why PRD #173 migrates
from it rather than from Zotero's BibTeX. Which RDF predicate becomes which
field is written where it is applied, in :func:`_to_item`.

Three decisions worth naming up here:

* **An item lives in many collections, a category path is one chain.** The
  first collection an item appears in becomes ``collection_path``; the others
  are kept under ``zotero:otherCollections`` in ``raw``, so nothing is lost and
  the wizard still shows one predictable tree.
* **Zotero has no cite key.** ``key`` is therefore *derived* (first author's
  surname + year, uniquified) rather than carrying an ``rdf:about`` URI into
  the library's ``cite_key`` column. The URI stays in ``raw``.
* **Parsing matches local names, not namespaces** (exports differ in which
  DC/PRISM revision they declare) — hence the ``_tag``/``_attr`` helpers at the
  bottom. Anything that is not well-formed RDF raises
  :class:`MigrationUnreadable` → ``error.migration_unreadable``, never a 500.

PURE (CLAUDE.md invariant): stdlib only (``xml.etree``), no DB, no network, no
writes. ``read_items`` reads the export, like every migration adapter.
"""

from __future__ import annotations

import html
import os
import re
import unicodedata
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from migration_items import MigrationItem, MigrationUnreadable, resolve_attachment

# Elements that are never a library item: structure, containers, agents.
NON_ITEM_TAGS = {"Collection", "Attachment", "Memo", "Note", "Journal", "Series",
                 "Periodical", "Person", "Organization", "Seq", "Bag", "Alt", "li"}

# ``z:itemType`` → the BibTeX-ish entry types the rest of the app speaks. The
# finer Zotero type wins over the coarse element tag when both are present.
ITEM_TYPES = {"journalArticle": "article", "magazineArticle": "article",
              "newspaperArticle": "article", "preprint": "unpublished",
              "book": "book", "bookSection": "incollection", "document": "misc",
              "conferencePaper": "inproceedings", "thesis": "phdthesis",
              "report": "techreport", "manuscript": "unpublished",
              "webpage": "online", "blogPost": "online"}

ELEMENT_TYPES = {"Article": "article", "Book": "book", "Document": "misc",
                 "BookSection": "incollection", "Thesis": "phdthesis",
                 "ConferenceProceedings": "inproceedings",
                 "Report": "techreport", "Manuscript": "unpublished"}

_BLOCK_RE = re.compile(r"(?i)</(p|div|li|h[1-6])>|<br\s*/?>")
_TAG_RE = re.compile(r"<[^>]+>")


def read_items(path: str) -> list[MigrationItem]:
    """Read an export; ``path`` is the ``.rdf`` or the folder holding it."""
    target = rdf_file(path)
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        raise MigrationUnreadable(f"RDF file not readable: {e}") from e
    return to_items(text, os.path.dirname(os.path.abspath(target)))


def rdf_file(path: str) -> str:
    """The ``.rdf`` behind a path the reader chose. A folder is accepted when it
    holds exactly one — zero or several is refused, not guessed."""
    target = os.path.abspath(os.path.expanduser((path or "").strip()))
    if not os.path.isdir(target):
        return target
    found = sorted(f for f in os.listdir(target) if f.lower().endswith(".rdf"))
    if len(found) != 1:
        raise MigrationUnreadable(
            f"expected exactly one .rdf in {target}, found {len(found)}")
    return os.path.join(target, found[0])


def to_items(text: str, base_dir: str = "") -> list[MigrationItem]:
    """RDF source → migration items, in document order."""
    root = _parse(text)
    index: dict = {}
    containers: set = set()
    for el in root.iter():
        about = _ident(_attr(el, "about"))
        if about:
            index.setdefault(about, el)
        for part in _children(el, "isPartOf"):
            containers.add(_ident(_attr(part, "resource")))
    paths = collection_paths(root)
    used: set[str] = set()
    return [_to_item(el, index, paths, base_dir, used)
            for el in root if _is_item(el, containers)]


def collection_paths(root) -> dict[str, list[list[str]]]:
    """``z:Collection`` tree → ``{item id: [path, …]}`` with nested titles. A
    sub-collection is its own element the parent points at with
    ``dcterms:hasPart`` — the same predicate that holds the items, so the two
    are told apart by whether the target is itself a collection."""
    ids: dict[int, str] = {}
    titles: dict[str, str] = {}
    members: dict[str, list[str]] = {}
    for el in root.iter():
        if _tag(el) == "Collection":
            ids[id(el)] = _ident(_attr(el, "about")) or f"collection:{len(ids)}"
    for el in root.iter():
        if _tag(el) == "Collection":
            titles[ids[id(el)]] = _field(el, "title")
            members[ids[id(el)]] = _dedupe(_part_target(p, ids)
                                           for p in _children(el, "hasPart"))

    parent = {m: cid for cid, refs in members.items() for m in refs if m in titles}
    by_item: dict[str, list[list[str]]] = {}
    for cid, refs in members.items():
        path = _path_of(cid, parent, titles)
        for ref in refs:
            if ref in titles or not path:
                continue
            if path not in by_item.setdefault(ref, []):
                by_item[ref].append(path)
    return by_item


def html_to_text(value: str) -> str:
    """A Zotero note's HTML → plain text, block structure kept as newlines."""
    text = _TAG_RE.sub("", _BLOCK_RE.sub("\n", value or ""))
    lines = [line.strip() for line in html.unescape(text).splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _to_item(el, index: dict, paths: dict, base_dir: str, used: set) -> MigrationItem:
    about = _ident(_attr(el, "about"))
    raw = _raw(el)
    raw["zotero:about"] = _attr(el, "about")
    authors = _people(el, "authors", index)
    for name in ("editors", "contributors"):
        if _people(el, name, index):
            raw[f"zotero:{name}"] = "; ".join(_people(el, name, index))

    doi, isbn = _identifiers(el, raw, about)
    item_paths = paths.get(about, [])
    if len(item_paths) > 1:
        raw["zotero:otherCollections"] = ["/".join(p) for p in item_paths[1:]]
    attachments, missing = _attachments(el, index, base_dir)
    year = _year(_field(el, "date"))

    return MigrationItem(
        key=_key(authors, year, about, used),
        entry_type=(ITEM_TYPES.get(_field(el, "itemType"))
                    or ELEMENT_TYPES.get(_tag(el), "misc")),
        title=_field(el, "title"),
        authors="; ".join(authors),
        year=year,
        doi=doi,
        isbn=isbn,
        abstract=_field(el, "abstract"),
        journal=_journal(el, index),
        publisher=_field(el, "publisher"),
        # A manual tag is plain text, an automatic one sits in a z:AutomaticTag.
        tags=_dedupe(_value(node) for node in _children(el, "subject")),
        collection_path=list(item_paths[0]) if item_paths else [],
        notes=_notes(el, index),
        date_added=_field(el, "dateSubmitted") or None,
        attachments=attachments,
        attachments_missing=missing,
        raw=raw,
    )


def _identifiers(el, raw: dict, about: str) -> tuple[str, str]:
    """``dc:identifier``/``bib:doi`` → (doi, isbn); URLs and ISSNs land in raw.
    Zotero writes identifiers as prefixed strings ("DOI 10.1/x", "ISBN 978-…")
    and a URL as a nested ``dcterms:URI``; the item's own ``rdf:about`` is a
    doi.org URL often enough to be worth reading as a last resort."""
    doi = _normalize_doi(_field(el, "doi"))
    isbn = ""
    for node in _children(el, "identifier"):
        value = _value(node)
        lowered = value.lower()
        if lowered.startswith("doi"):
            doi = doi or _normalize_doi(value[3:].lstrip(" :"))
        elif lowered.startswith("isbn"):
            isbn = isbn or value[4:].lstrip(" :").strip()
        elif lowered.startswith("issn"):
            raw.setdefault("zotero:issn", value[4:].lstrip(" :").strip())
        elif lowered.startswith("http"):
            raw.setdefault("zotero:url", value)
    if not doi and "doi.org/" in about.lower():
        doi = _normalize_doi(about)
    return doi, isbn


def _journal(el, index: dict) -> str:
    """``dcterms:isPartOf`` → the container's title. The container is nested
    (``bib:Journal``, ``bib:Book``) or a separate element pointed at — both come
    out of Zotero, depending on whether the journal has an ISSN."""
    for node in _children(el, "isPartOf"):
        target = index.get(_ident(_attr(node, "resource")))
        for sub in list(node) + ([target] if target is not None else []):
            if _field(sub, "title"):
                return _field(sub, "title")
        if (node.text or "").strip():
            return node.text.strip()
    return ""


def _notes(el, index: dict) -> str:
    """Notes → one plain-text block (linked by ``isReferencedBy``, or nested)."""
    return "\n\n".join(_dedupe(
        html_to_text(_field(n, "value") or _value(n))
        for n in _linked(el, "isReferencedBy", index, ("Memo", "Note"))))


def _attachments(el, index: dict, base_dir: str) -> tuple[list[str], list[str]]:
    """``z:Attachment`` → existing absolute paths + the specs that resolved to
    nothing. Snapshots and other non-PDFs are skipped: the library stores one
    PDF per item, not a web archive."""
    found: list[str] = []
    missing: list[str] = []
    for node in _linked(el, "link", index, ("Attachment",)):
        spec = _attachment_spec(node)
        mime = (_field(node, "type") or _attr(node, "type")).lower()
        is_pdf = mime == "application/pdf" if mime else spec.lower().endswith(".pdf")
        if not spec or not is_pdf:
            continue
        # Zotero percent-escapes the filename in rdf:resource while the file on
        # disk has the spaces. Both readings are tried, the filesystem decides.
        resolved = (resolve_attachment(spec, base_dir)
                    or resolve_attachment(unquote(spec), base_dir))
        if resolved and resolved not in found:
            found.append(resolved)
        elif not resolved and spec not in missing:
            missing.append(spec)
    return found, missing


def _attachment_spec(node) -> str:
    """``<rdf:resource rdf:resource=…/>``, else a non-anchor ``rdf:about``."""
    for sub in _children(node, "resource"):
        if _attr(sub, "resource"):
            return _attr(sub, "resource")
    about = _attr(node, "about")
    return "" if about.startswith("#") else about


def _people(el, name: str, index: dict) -> list[str]:
    """``bib:authors`` → ``["Surname, Given", …]``, in the sequence's order."""
    people: list = []
    for holder in _children(el, name):
        people += _linked(holder, None, index, ("Person",))
    return _dedupe(", ".join(p for p in (_field(n, "surname"),
                                         _field(n, "givenName")) if p)
                   or _field(n, "name") for n in people)


def _linked(el, predicate: str | None, index: dict, tags: tuple) -> list:
    """The ``tags`` elements this one owns — nested, or pointed at by an
    ``rdf:resource`` (on ``predicate``, or anywhere below). Both are in use."""
    refs = _children(el, predicate) if predicate else list(el.iter())
    out = [index[key] for key in (_ident(_attr(n, "resource")) for n in refs)
           if key in index]
    out += [sub for sub in el.iter() if sub is not el and _tag(sub) in tags]
    return [node for node in out if _tag(node) in tags]


def _key(authors: list[str], year: int | None, about: str, used: set) -> str:
    """A stable, readable key — Zotero RDF has no cite key of its own. NFKD
    first: without it "Müller" loses its vowel instead of becoming "muller"."""
    folded = unicodedata.normalize(
        "NFKD", authors[0].split(",")[0].lower()) if authors else ""
    surname = re.sub(r"[^a-z0-9]", "", folded)
    base = (f"{surname}{year or ''}" if surname
            else re.sub(r"[^A-Za-z0-9_]", "_", about)[:40].strip("_")) or "zotero"
    key, counter = base, 1
    while key in used:
        counter += 1
        key = f"{base}{counter}"
    used.add(key)
    return key


def _raw(el) -> dict:
    """Every direct child of the item, flattened — nothing an export carried is
    lost, even when no column wants it."""
    raw: dict = {}
    for child in el:
        name, value = _tag(child), _value(child)
        if not value:
            continue
        prev = raw.get(name)
        raw[name] = value if prev is None else (
            prev + [value] if isinstance(prev, list) else [prev, value])
    return raw


def _dedupe(values) -> list[str]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out


def _year(value: str) -> int | None:
    match = re.search(r"(\d{4})", value or "")
    return int(match.group(1)) if match else None


def _normalize_doi(doi: str) -> str:
    doi = (doi or "").strip()
    if "doi.org/" in doi.lower():
        doi = doi.split("doi.org/", 1)[-1]
    return doi.rstrip(".")


def _parse(text: str):
    try:
        root = ET.fromstring(text or "")
    except ET.ParseError as e:
        raise MigrationUnreadable(f"not well-formed XML: {e}") from e
    if _tag(root) != "RDF":
        raise MigrationUnreadable(f"not an RDF document: <{root.tag}>")
    return root


def _is_item(el, containers: set) -> bool:
    """A top-level element is an item unless it is structure. Zotero uses
    ``rdf:Description`` for every type without a ``bib:`` class (webpage, blog
    post, software), so an element is judged by what it carries, not only by
    its tag; a container pointed at with ``isPartOf`` is never an item."""
    if _tag(el) in NON_ITEM_TAGS or not _attr(el, "about"):
        return False
    item_type = _field(el, "itemType")
    if _ident(_attr(el, "about")) in containers and not item_type:
        return False
    return bool(item_type or _field(el, "title"))


def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


def _attr(el, name: str) -> str:
    for key, value in el.attrib.items():
        if key.rsplit("}", 1)[-1] == name:
            return value
    return ""


def _children(el, name: str) -> list:
    return [child for child in el if _tag(child) == name]


def _field(el, name: str) -> str:
    for child in _children(el, name):
        if _value(child):
            return _value(child)
    return ""


def _value(el) -> str:
    """Its own text, or that of the node Zotero wraps it in (``rdf:value``)."""
    if (el.text or "").strip():
        return el.text.strip()
    for sub in el.iter():
        if sub is not el and _tag(sub) in ("value", "name", "title") \
                and (sub.text or "").strip():
            return sub.text.strip()
    return ""


def _ident(about: str) -> str:
    return (about or "").lstrip("#").strip()


def _part_target(part, ids: dict) -> str:
    # What a hasPart points at — by reference or nested inline.
    ref = _ident(_attr(part, "resource"))
    for sub in part:
        ref = ref or ids.get(id(sub)) or _ident(_attr(sub, "about"))
        break
    return ref


def _path_of(cid: str, parent: dict, titles: dict) -> list[str]:
    chain: list[str] = []
    seen: set[str] = set()
    while cid and cid not in seen:
        seen.add(cid)
        if titles.get(cid, "").strip():
            chain.append(titles[cid].strip())
        cid = parent.get(cid, "")
    return list(reversed(chain))
