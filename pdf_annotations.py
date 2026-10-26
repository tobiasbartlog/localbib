"""Annotations a reader set in their own PDF reader, read from the PDF (#229).

The PDF is the only source (spec #222, A): LocalBib lists what the reader
marked, commented, framed or drew, and never writes to the file. This module
holds every rule that turns the raw annotation objects into list entries; the
router (``routers/annotations.py``) only finds the file, caches a reading in
memory and shapes the HTTP answer (ADR-0006).

Pure: opens the file read-only with PyMuPDF, writes nothing anywhere, logs
nothing, keeps no state between calls. PyMuPDF is not thread-safe: a caller
that may run these functions on several threads serialises them.

Rules, in the order a reading applies them:

* **What is listed** is decided by the PDF subtype alone - the markup
  annotations of the PDF norm (``LISTED``). Links, form fields and popups are
  other subtypes and never appear; that type boundary is the whole criterion
  for "added by hand", no heuristic.
* **Text under a mark** is extended to whole words, a word broken across a
  line end is joined (a syllable break loses its hyphen, ``High-``/``Risk``
  keeps it), NFKC folds ligatures (``ﬁ`` -> ``fi``), whitespace is collapsed,
  and a leading/trailing ``…`` says the quote starts/ends mid-sentence.
* **A comment** is the plain-text field, else the rich text without markup.
  An annotation that answers another one (``/IRT``) is a reply of it, not an
  entry of its own - unless it answers itself or round in a loop, or is only
  grouped with it (``/RT /Group``): those stay entries.
* **A double mark** - same page, subtype, colour and author, overlapping on
  the same lines, not two different comments - becomes one entry with
  ``merged``; replies are in time order, times compared as moments.
* **Sticky notes and carets** carry the text around their icon as context,
  closed shapes the text inside them as caption; shapes, handwriting and marks
  without readable text get a picture (``render_image``).
* **Sections** come from the embedded outline, else from bold short lines at a
  block start (never a figure/table/algorithm/listing caption), searched only
  a few pages back from each annotation.
* **Order**: page, then top to bottom, then left to right.

``locate`` (contract 2.6) shares the page geometry: where a passage starts on
one page, as the same page fractions an annotation's ``rect`` uses.
"""

from __future__ import annotations

import hashlib
import html
import os
import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import cmp_to_key
from typing import Optional

import fitz  # PyMuPDF

# PDF subtype -> filter group of the SPA ("kind").
LISTED: dict[str, str] = {
    "Highlight": "highlight", "Underline": "highlight",
    "StrikeOut": "highlight", "Squiggly": "highlight",
    "Text": "comment", "FreeText": "comment", "Caret": "comment",
    "Square": "shape", "Circle": "shape", "Line": "shape", "Polygon": "shape",
    "PolyLine": "shape", "Ink": "shape", "Stamp": "shape",
}
TEXT_MARKUP = frozenset({"Highlight", "Underline", "StrikeOut", "Squiggly"})
# Shapes that enclose an area: the text inside is their caption.
CAPTIONED = frozenset({"Square", "Circle", "Polygon"})
# Annotations whose appearance carries text of its own (a typed box, a stamp):
# PyMuPDF's text extraction includes it, so it is kept out of quotes, captions
# and the heading search.
OWN_TEXT = frozenset({"FreeText", "Stamp"})

QUOTE_MAX = 1500        # characters; the SPA clamps a quote to four lines anyway
CAPTION_MAX = 240
CONTEXT_BEFORE = 3      # words before the one under a note's icon ...
CONTEXT_AFTER = 7       # ... and after it: about one line of text
SECTION_LOOKBACK = 4    # pages searched back for a heading before an annotation
HEADING_MAX_CHARS = 120
HEADING_MAX_WORDS = 15
HEADING_MAX_LINES = 3
# One text extraction per page serves words and headings: the dict flags
# without images (which equal the words defaults), so no picture is decoded.
TEXT_FLAGS = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES

_HYPHENS = ("-", "­", "‐", "‑")
_CLOSERS = "\"'”’»)]"
_ABBREVIATIONS = frozenset({
    "e.g.", "i.e.", "al.", "fig.", "cf.", "vs.", "eq.", "no.", "nr.", "tab.",
    "z.b.", "bzw.", "abb.", "vgl.", "ca.", "etc.", "resp.", "sec.", "ch.",
})
_CAPTION_START = re.compile(
    r"^(fig\.?|figure|tab\.?|table|alg\.?|algorithm|listing|abb\.?|abbildung|tabelle)(\s|\d|$)",
    re.IGNORECASE)
_BOLD_FONT = re.compile(r"bold|black|heavy|semibold|demi|medi|cmbx|sfbx|-bd\b", re.IGNORECASE)
_SAFE_ID = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")
_PDF_DATE = re.compile(
    r"^(?:D:)?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?\s*(?:([Zz+\-])(\d{2})?'?(\d{2})?'?)?")
_DA_RGB = re.compile(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg\b")
_DA_GRAY = re.compile(r"([\d.]+)\s+g\b")


class UnreadablePdf(Exception):
    """The file cannot be opened for reading - password, damage, not a PDF."""


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class Reading:
    """One reading of a file: the API payload plus what the picture needs."""

    payload: dict
    # annotation id -> (page index, area in display coordinates)
    areas: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    """NFKC (ligatures, compatibility forms), no soft hyphens, one space."""
    text = unicodedata.normalize("NFKC", text or "").replace("­", "")
    return re.sub(r"\s+", " ", text).strip()


def _clip_text(text: str, limit: int) -> str:
    """Cut at a word boundary and say so with ``…``."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return (cut or text[:limit]) + "…"


def _ends_sentence(word: str) -> bool:
    bare = _norm(word).rstrip(_CLOSERS)
    if not bare or bare[-1] not in ".!?…":
        return False
    return bare.lower() not in _ABBREVIATIONS


def _hex(color) -> Optional[str]:
    """A PDF colour array (gray, RGB or CMYK, 0..1) as ``#RRGGBB``."""
    if not color or len(color) not in (1, 3, 4):
        return None
    values = [float(c) for c in color]
    if len(values) == 1:
        values = values * 3
    elif len(values) == 4:
        c, m, y, k = values
        values = [(1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)]
    r, g, b = (max(0, min(255, round(v * 255))) for v in values)
    return f"#{r:02X}{g:02X}{b:02X}"


def pdf_date_iso(raw: str) -> Optional[str]:
    """A PDF date (``D:20261009203918+02'00'``) as ISO 8601, ``None`` if not one."""
    m = _PDF_DATE.match((raw or "").strip())
    if not m:
        return None
    year, month, day, hour, minute, second, sign, tz_h, tz_m = m.groups()
    # Near year 1 or 9999 an offset cannot be converted to UTC (OverflowError when
    # comparing); no reader writes such a date, so it counts as none.
    if not 1900 <= int(year) <= 2999:
        return None
    try:
        moment = datetime(int(year), int(month or 1), int(day or 1),
                          int(hour or 0), int(minute or 0), int(second or 0))
    except ValueError:
        return None
    if sign in ("Z", "z"):
        moment = moment.replace(tzinfo=timezone.utc)
    elif sign in ("+", "-"):
        hours, minutes = int(tz_h or 0), int(tz_m or 0)
        # An offset no clock has (``+24'00'``) is no offset: the time stays as written.
        if hours < 24 and minutes < 60:
            offset = timedelta(hours=hours, minutes=minutes)
            moment = moment.replace(tzinfo=timezone(offset if sign == "+" else -offset))
    return moment.isoformat()


_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)


def _instant(iso: Optional[str]) -> datetime:
    """An ISO time of this module as a comparable instant: different offsets
    compare as moments, a time without offset counts as UTC, none as earliest."""
    if not iso:
        return _EARLIEST
    moment = datetime.fromisoformat(iso)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _plain_comment(text: str) -> Optional[str]:
    """Trim a comment line by line; empty means none."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [re.sub(r"[ \t]+", " ", unicodedata.normalize("NFKC", ln)).strip() for ln in lines]
    joined = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return joined or None


def _rich_text(rc: str) -> Optional[str]:
    """Acrobat's XHTML comment (``/RC``) without its markup."""
    if not rc:
        return None
    text = re.sub(r"(?i)<\s*br\s*/?\s*>|</\s*(p|div|li)\s*>", "\n", rc)
    text = re.sub(r"(?s)<\?xml.*?\?>|<[^>]+>", "", text)
    return _plain_comment(html.unescape(text))


def _key(doc, xref: int, key: str) -> tuple[str, str]:
    try:
        return doc.xref_get_key(xref, key)
    except Exception:
        return ("null", "null")


def _string_key(doc, xref: int, key: str) -> str:
    kind, value = _key(doc, xref, key)
    return value if kind == "string" else ""


def _rect_of(points) -> Optional[fitz.Rect]:
    pts = [p for p in points if p is not None]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return fitz.Rect(min(xs), min(ys), max(xs), max(ys))


def _quads(annot) -> list[fitz.Rect]:
    """The line rectangles of a text mark (QuadPoints), else its rect."""
    try:
        vertices = annot.vertices or []
    except Exception:
        vertices = []
    flat = [v for v in vertices if isinstance(v, (tuple, list)) and len(v) == 2
            and all(isinstance(c, (int, float)) for c in v)]
    rects = [_rect_of(flat[i:i + 4]) for i in range(0, len(flat) - len(flat) % 4, 4)]
    rects = [r for r in rects if r is not None and not r.is_empty]
    return rects or [fitz.Rect(annot.rect)]


def _same_line(a: fitz.Rect, b: fitz.Rect) -> bool:
    overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
    return overlap > 0.5 * max(0.1, min(a.height, b.height))


# ---------------------------------------------------------------------------
# Words of a page
# ---------------------------------------------------------------------------

@dataclass
class _Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block: int
    line: int

    @property
    def line_key(self) -> tuple[int, int]:
        return (self.block, self.line)


def _page_words(page, textpage, zones: list[fitz.Rect]) -> list[_Word]:
    """The page's words in reading order, minus annotation text (``OWN_TEXT``)."""
    words = []
    for x0, y0, x1, y1, text, block, line, _ in page.get_text("words", sort=False, textpage=textpage):
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if any(z.contains(fitz.Point(cx, cy)) for z in zones):
            continue
        words.append(_Word(x0, y0, x1, y1, text, block, line))
    return words


def _covered(word: _Word, quad: fitz.Rect) -> bool:
    if not _same_line(fitz.Rect(word.x0, word.y0, word.x1, word.y1), quad):
        return False
    overlap = min(word.x1, quad.x1) - max(word.x0, quad.x0)
    char = (word.x1 - word.x0) / max(1, len(word.text))
    # A quarter of a character: a mark that starts inside a word takes the
    # whole word, one that merely grazes the next word does not.
    return overlap >= 0.25 * char


def _extend_broken_words(words: list[_Word], picked: list[int]) -> list[int]:
    """Whole words across a line end: ``anal-`` takes ``ysis,`` and back."""
    if not picked:
        return picked
    first, last = picked[0], picked[-1]
    if first > 0:
        prev, cur = words[first - 1], words[first]
        if prev.block == cur.block and prev.line_key != cur.line_key and prev.text.endswith(_HYPHENS):
            picked = [first - 1] + picked
    if last + 1 < len(words):
        cur, nxt = words[last], words[last + 1]
        if nxt.block == cur.block and nxt.line_key != cur.line_key and cur.text.endswith(_HYPHENS):
            picked = picked + [last + 1]
    return picked


def _join(seq: list[_Word]) -> str:
    """Words to text: a space between them, a hyphenated line end joined."""
    out = ""
    prev = None
    for w in seq:
        if prev is None:
            out = w.text
        elif w.line_key != prev.line_key and prev.text.endswith(_HYPHENS) and len(prev.text) > 1:
            head = _norm(w.text)[:1]
            # A syllable break continues in lower case and loses its hyphen;
            # a compound (``High-``/``Risk``) keeps it.
            out = (out[:-1] + w.text) if (head.islower() or prev.text.endswith("­")) else (out + w.text)
        else:
            out += " " + w.text
        prev = w
    return _norm(out)


def _span_text(words: list[_Word], picked: list[int], limit: int) -> Optional[str]:
    """The picked words as a quote, with ``…`` where it starts or ends mid-sentence."""
    if not picked:
        return None
    text = _join([words[i] for i in picked])
    if not text:
        return None
    first, last = picked[0], picked[-1]
    lead = first > 0 and words[first - 1].block == words[first].block \
        and not _ends_sentence(words[first - 1].text)
    trail = last + 1 < len(words) and words[last + 1].block == words[last].block \
        and not _ends_sentence(words[last].text)
    text = _clip_text(text, limit)
    if lead:
        text = "…" + text
    if trail and not text.endswith("…"):
        text += "…"
    return text


def _quote(words: list[_Word], quads: list[fitz.Rect]) -> Optional[str]:
    picked = sorted({i for q in quads for i, w in enumerate(words) if _covered(w, q)})
    return _span_text(words, _extend_broken_words(words, picked), QUOTE_MAX)


def _context(words: list[_Word], rect: fitz.Rect) -> Optional[str]:
    """About one line of text around a note's icon (or a caret)."""
    if not words:
        return None
    cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2

    def dx(w):
        return 0.0 if w.x0 <= cx <= w.x1 else min(abs(w.x0 - cx), abs(w.x1 - cx))

    def dy(w):
        return 0.0 if w.y0 - 2 <= cy <= w.y1 + 2 else min(abs(w.y0 - cy), abs(w.y1 - cy))

    best = min(range(len(words)), key=lambda i: (dy(words[i]), dx(words[i])))
    if dy(words[best]) > 24:
        return None
    anchor = words[best]
    start = best
    while start > 0 and best - start < CONTEXT_BEFORE and words[start - 1].block == anchor.block:
        start -= 1
    end = best
    while end + 1 < len(words) and end - best < CONTEXT_AFTER and words[end + 1].block == anchor.block:
        end += 1
    picked = _extend_broken_words(words, list(range(start, end + 1)))
    return _span_text(words, picked, 160)


def _caption(words: list[_Word], rect: fitz.Rect) -> Optional[str]:
    """The text inside a framed area; blocks joined, a heading by a dash."""
    inside = [i for i, w in enumerate(words)
              if rect.contains(fitz.Point((w.x0 + w.x1) / 2, (w.y0 + w.y1) / 2))]
    if not inside:
        return None
    parts: list[str] = []
    run: list[_Word] = []
    for i in inside:
        if run and words[i].block != run[-1].block:
            parts.append(_join(run))
            run = []
        run.append(words[i])
    parts.append(_join(run))
    text = ""
    for part in filter(None, parts):
        if not text:
            text = part
        else:
            text += (" " if _ends_sentence(text) else " — ") + part
    return _clip_text(text, CAPTION_MAX) if text else None


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _is_bold(span: dict) -> bool:
    return bool(span.get("flags", 0) & 16) or bool(_BOLD_FONT.search(span.get("font", "")))


def _page_headings(page, textpage, zones: list[fitz.Rect]) -> list[tuple[float, str]]:
    """``(top, title)`` of every heading on the page, top to bottom.

    A heading is a run of bold lines at the start of a text block (at most
    three, e.g. a section number on its own line), short, at least body size,
    holding a letter, and not a figure, table, algorithm or listing caption.
    """
    blocks = page.get_text("dict", textpage=textpage)["blocks"]
    sizes = []
    for block in blocks:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].strip():
                    sizes.extend([span["size"]] * len(span["text"].strip()))
    body = statistics.median(sizes) if sizes else 0
    found = []
    for block in blocks:
        if block.get("type") != 0:
            continue
        bbox = fitz.Rect(block["bbox"])
        if any(z.intersects(bbox) for z in zones):
            continue
        texts, top, size = [], None, 0.0
        for line in block.get("lines", [])[:HEADING_MAX_LINES]:
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans or not all(_is_bold(s) for s in spans):
                break
            texts.append(" ".join(s["text"] for s in spans))
            size = max(size, max(s["size"] for s in spans))
            top = line["bbox"][1] if top is None else min(top, line["bbox"][1])
        title = _norm(" ".join(texts))
        if (not title or len(title) > HEADING_MAX_CHARS or len(title.split()) > HEADING_MAX_WORDS
                or not re.search(r"[^\W\d_]", title) or _CAPTION_START.match(title)
                or size < 0.95 * body):
            continue
        found.append((top, title))
    found.sort(key=lambda h: h[0])
    return found


def _outline(doc) -> list[tuple[int, float, str]]:
    """The embedded outline as ``(page index, top, title)``, in page order."""
    try:
        toc = doc.get_toc(simple=False)
    except Exception:
        return []
    entries = []
    for item in toc:
        if len(item) < 3:
            continue
        title, page_no = _norm(str(item[1])), int(item[2])
        if not title or page_no < 1 or page_no > doc.page_count:
            continue
        dest = item[3] if len(item) > 3 and isinstance(item[3], dict) else {}
        point = dest.get("to")
        top = float(point.y) if point is not None and hasattr(point, "y") else 0.0
        entries.append((page_no - 1, top, title))
    entries.sort(key=lambda e: (e[0], e[1]))
    return entries


# ---------------------------------------------------------------------------
# Raw annotations -> entries
# ---------------------------------------------------------------------------

@dataclass
class _Raw:
    xref: int
    page: int
    subtype: str
    rect: fitz.Rect          # unrotated page coordinates (as the words)
    quads: list
    color: Optional[str]
    comment: Optional[str]
    author: str
    created: Optional[str]
    modified: Optional[str]
    nm: str
    irt: Optional[int]
    group: bool = False      # ``/RT /Group``: part of the mark ``irt`` names, no reply
    replies: list = field(default_factory=list)
    members: list = field(default_factory=list)  # double marks merged into this one


def _irt(doc, xref: int) -> Optional[int]:
    kind, value = _key(doc, xref, "IRT")
    if kind == "xref":
        m = re.match(r"\s*(\d+)\s+\d+\s+R", value)
        return int(m.group(1)) if m else None
    return None


def _grouped(doc, xref: int) -> bool:
    kind, value = _key(doc, xref, "RT")
    return kind == "name" and value.lstrip("/") == "Group"


def _color(doc, annot, subtype: str) -> Optional[str]:
    colors = annot.colors or {}
    found = _hex(colors.get("stroke")) or _hex(colors.get("fill"))
    if found or subtype != "FreeText":
        return found
    da = _string_key(doc, annot.xref, "DA")
    m = _DA_RGB.search(da)
    if m:
        return _hex([float(v) for v in m.groups()])
    m = _DA_GRAY.search(da)
    return _hex([m.group(1)]) if m else None


def _raw(doc, page_index: int, annot) -> _Raw:
    info = annot.info or {}
    subtype = annot.type[1]
    comment = _plain_comment(info.get("content", "")) or _rich_text(_string_key(doc, annot.xref, "RC"))
    return _Raw(
        xref=annot.xref, page=page_index, subtype=subtype, rect=fitz.Rect(annot.rect),
        quads=_quads(annot) if subtype in TEXT_MARKUP else [fitz.Rect(annot.rect)],
        color=_color(doc, annot, subtype), comment=comment,
        author=_norm(info.get("title", "")),
        created=pdf_date_iso(info.get("creationDate", "")),
        modified=pdf_date_iso(info.get("modDate", "")),
        nm=(info.get("id") or "").strip(), irt=_irt(doc, annot.xref),
        group=_grouped(doc, annot.xref),
    )


def _threads(raws: list[_Raw]) -> list[_Raw]:
    """The entries, with every reply hung on the entry it answers.

    A reply (``/IRT``) hangs on the root of its thread, a reply to a reply on
    the annotation that started it. Only a real parent counts: an annotation
    answering itself, or one of several answering each other round in a loop,
    stays an entry (an answer to it still hangs on it). A grouped annotation
    (``/RT /Group``, e.g. the strike-out of Acrobat's "replace text") belongs
    to the mark of its primary and is no reply: it stays an entry and takes
    the primary's author, times and colour where it has none of its own.
    """
    by_xref = {r.xref: r for r in raws}

    def parent(r: _Raw) -> Optional[_Raw]:
        return None if r.group or r.irt is None else by_xref.get(r.irt)

    looped: set = set()
    for r in raws:
        position: dict = {}
        cur = r
        while cur is not None and cur.xref not in position:
            position[cur.xref] = len(position)
            cur = parent(cur)
        if cur is not None:          # came back to an annotation of this chain
            looped.update(x for x, i in position.items() if i >= position[cur.xref])

    for r in raws:
        primary = by_xref.get(r.irt) if r.group else None
        if primary is not None and primary is not r:
            r.author = r.author or primary.author
            r.created = r.created or primary.created
            r.modified = r.modified or primary.modified
            r.color = r.color or primary.color

    entries = []
    for r in raws:
        home = r
        while home.xref not in looped and parent(home) is not None:
            home = parent(home)
        if home is r:
            entries.append(r)
        elif r.comment:
            home.replies.append({"author": r.author or None, "created": r.created, "text": r.comment})
    return entries


def _merges_with(a: _Raw, b: _Raw) -> bool:
    if a.subtype != b.subtype or a.subtype not in TEXT_MARKUP or a.color != b.color:
        return False
    # Two readers marking the same words are two entries: each keeps its author.
    if a.author != b.author:
        return False
    if a.comment and b.comment and a.comment != b.comment:
        return False
    return any(_same_line(qa, qb) and min(qa.x1, qb.x1) > max(qa.x0, qb.x0)
               for qa in a.quads for qb in b.quads)


def _merge_double_marks(raws: list[_Raw]) -> list[_Raw]:
    kept: list[_Raw] = []
    for raw in raws:
        home = next((k for k in kept if k.page == raw.page
                     and any(_merges_with(m, raw) for m in [k] + k.members)
                     and not (k.comment and raw.comment and k.comment != raw.comment)), None)
        if home is None:
            kept.append(raw)
            continue
        home.members.append(raw)
        home.quads = home.quads + raw.quads
        home.rect = home.rect | raw.rect
        home.comment = home.comment or raw.comment
        home.replies.extend(raw.replies)
        home.created = min(filter(None, [home.created, raw.created]), key=_instant, default=None)
        home.modified = max(filter(None, [home.modified, raw.modified]), key=_instant, default=None)
    return kept


def _display_rect(page, rect: fitz.Rect) -> fitz.Rect:
    """Unrotated page coordinates -> what a viewer shows (page rotation applied)."""
    shown = rect * page.rotation_matrix
    shown.normalize()
    return shown


def _fraction(page, rect: fitz.Rect) -> dict:
    shown = _display_rect(page, rect)
    width, height = page.rect.width or 1, page.rect.height or 1

    def part(value, size):
        return round(max(0.0, min(1.0, value / size)), 4)

    x0, y0 = part(shown.x0, width), part(shown.y0, height)
    return {"x": x0, "y": y0,
            "w": round(max(0.0, part(shown.x1, width) - x0), 4),
            "h": round(max(0.0, part(shown.y1, height) - y0), 4)}


def _entry_id(raw: _Raw, taken: set) -> str:
    """The reader's own id (NM) where it is URL-safe, else one derived from it
    or from the object number. Unique within the file."""
    # Dots alone are no name for a URL: a browser resolves ``.``/``..`` away.
    if raw.nm and _SAFE_ID.match(raw.nm) and raw.nm.strip("."):
        ident = raw.nm
    elif raw.nm:
        ident = "nm-" + hashlib.sha1(raw.nm.encode("utf-8")).hexdigest()[:16]
    else:
        ident = f"x{raw.xref}"
    if ident in taken:
        base = ident = f"{ident}-x{raw.xref}"
        count = 2
        while ident in taken:    # a reader's own id may be exactly this fallback
            ident = f"{base}-{count}"
            count += 1
    taken.add(ident)
    return ident


def _order(a: tuple, b: tuple) -> int:
    """Page, then top to bottom, then left to right (same line: within 3 pt)."""
    (pa, ra), (pb, rb) = a, b
    if pa != pb:
        return -1 if pa < pb else 1
    if abs(ra.y0 - rb.y0) > 3:
        return -1 if ra.y0 < rb.y0 else 1
    return -1 if ra.x0 < rb.x0 else (1 if ra.x0 > rb.x0 else 0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def file_state(path: str) -> tuple[int, int, str]:
    """``(size, mtime_ns, ISO time)`` of the file: its *Stand*."""
    st = os.stat(path)
    iso = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    return st.st_size, st.st_mtime_ns, iso


def _open(path: str):
    try:
        doc = fitz.open(path)
    except RuntimeError as exc:
        # fitz.FileDataError / EmptyFileError / FileNotFoundError are RuntimeErrors:
        # no PDF at all is an expected state, so it carries no cause (not logged).
        raise UnreadablePdf(str(exc)) from None
    if not doc.is_pdf:
        doc.close()
        raise UnreadablePdf("not a PDF")
    # An empty user password still counts as readable.
    if doc.needs_pass and not doc.authenticate(""):
        doc.close()
        raise UnreadablePdf("password-protected")
    return doc


def read(path: str) -> Reading:
    """Read every listed annotation of the PDF at ``path``. Raises
    ``UnreadablePdf`` when the file cannot be opened or PyMuPDF fails on it
    while reading (damage it only meets on a page, a file exchanged meanwhile)."""
    try:
        _size, _mtime, stand = file_state(path)
    except OSError as exc:
        raise UnreadablePdf(str(exc)) from exc
    doc = _open(path)
    try:
        return _read(doc, stand)
    except RuntimeError as exc:   # MuPDF's failures; our own bugs pass through
        raise UnreadablePdf(f"{type(exc).__name__}: {exc}") from exc
    finally:
        doc.close()


def _read(doc, stand: str) -> Reading:
    raws: list[_Raw] = []
    zones: dict[int, list[fitz.Rect]] = {}
    for page_index in range(doc.page_count):
        page = doc[page_index]
        if not page.first_annot:
            continue
        for annot in page.annots():
            subtype = annot.type[1]
            if subtype in OWN_TEXT:
                zones.setdefault(page_index, []).append(fitz.Rect(annot.rect))
            if subtype in LISTED:
                raws.append(_raw(doc, page_index, annot))

    entries_raw = _merge_double_marks(_threads(raws))
    for r in entries_raw:
        r.replies.sort(key=lambda rep: _instant(rep["created"]))

    texts: dict[int, tuple] = {}
    words: dict[int, list[_Word]] = {}
    headings: dict[int, list[tuple[float, str]]] = {}

    def page_text(p):
        """The page and its one TextPage, shared by words and headings."""
        if p not in texts:
            page = doc[p]
            texts[p] = (page, page.get_textpage(flags=TEXT_FLAGS))
        return texts[p]

    def page_words(p):
        if p not in words:
            words[p] = _page_words(*page_text(p), zones.get(p, []))
        return words[p]

    def page_headings(p):
        if p not in headings:
            headings[p] = _page_headings(*page_text(p), zones.get(p, []))
        return headings[p]

    outline = _outline(doc)

    def section(p: int, y: float) -> Optional[str]:
        if outline:
            title = None
            for page_index, top, name in outline:
                if (page_index, top) <= (p, y):
                    title = name
                else:
                    break
            return title
        for back in range(0, SECTION_LOOKBACK + 1):
            q = p - back
            if q < 0:
                break
            found = [title for top, title in page_headings(q) if back > 0 or top <= y]
            if found:
                return found[-1]
        return None

    taken: set = set()
    placed = []
    areas = {}
    for r in entries_raw:
        page = doc[r.page]
        quote = context = caption = None
        if r.subtype in TEXT_MARKUP:
            quote = _quote(page_words(r.page), r.quads)
        elif r.subtype in ("Text", "Caret"):
            context = _context(page_words(r.page), r.rect)
        elif r.subtype in CAPTIONED:
            caption = _caption(page_words(r.page), r.rect)
        kind = LISTED[r.subtype]
        ident = _entry_id(r, taken)
        shown = _display_rect(page, r.rect)
        areas[ident] = (r.page, shown)
        placed.append(((r.page, shown), {
            "id": ident,
            "kind": kind,
            "subtype": r.subtype,
            "page": r.page + 1,
            "rect": _fraction(page, r.rect),
            "color": r.color,
            "quote": quote,
            "comment": r.comment,
            "replies": r.replies,
            "context": context,
            "caption": caption,
            "author": r.author or None,
            "created": r.created,
            "modified": r.modified,
            "merged": 1 + len(r.members),
            "has_image": kind == "shape" or (r.subtype in TEXT_MARKUP and not quote),
            "section": section(r.page, (r.rect.y0 + r.rect.y1) / 2),
        }))
    placed.sort(key=cmp_to_key(lambda a, b: _order(a[0], b[0])))
    entries = [entry for _, entry in placed]

    authors = {e["author"] for e in entries if e["author"]}
    authors |= {rep["author"] for e in entries for rep in e["replies"] if rep["author"]}
    return Reading(
        payload={
            "file_modified": stand,
            "pages": doc.page_count,
            "multiple_authors": len(authors) > 1,
            "has_sections": any(e["section"] for e in entries),
            "annotations": entries,
        },
        areas=areas,
    )


IMAGE_MAX_SIDE = 900  # pixels

# --- Finding a passage on a page (contract 2.6, ``GET /api/papers/{id}/locate``)
# The start of a passage is searched on the one page it is said to be on: its
# first words, then shorter starts. A start that occurs more than once on the
# page is no answer - never a guessed spot.
LOCATE_WORDS = (12, 8, 5)       # the starts tried, longest first
LOCATE_MIN_CHARS = 16           # a shorter start is too common to mean one place
LOCATE_MAX_BLOCK = 0.5          # a text block taller than this part of the page is no paragraph


def _starts(text: str) -> list[str]:
    words = _norm(text).split()
    out: list[str] = []
    for n in LOCATE_WORDS:
        start = " ".join(words[:n])
        if len(start) >= LOCATE_MIN_CHARS and start not in out:
            out.append(start)
    return out


def _occurrences(page_text: str, start: str) -> int:
    """How often ``start`` stands in the page text (case-folded, whitespace
    collapsed, hyphenated line ends joined) - what ``search_for`` matches."""
    flat = _norm(re.sub(r"-\s*\n\s*", "", page_text)).casefold()
    return flat.count(start.casefold())


def _paragraph(page, hits: list[fitz.Rect]) -> fitz.Rect:
    """The hits, grown to the text block (paragraph) the first one stands in."""
    area = fitz.Rect(hits[0])
    for hit in hits[1:]:
        area |= hit
    first = hits[0]
    # Hits and blocks are unrotated page coordinates: so is the height compared.
    height = page.cropbox.height or 1
    for block in page.get_text("blocks"):
        rect = fitz.Rect(block[:4])
        if block[6] == 0 and rect.intersects(first) \
                and rect.height <= LOCATE_MAX_BLOCK * height:
            area |= rect
            break
    return area


def locate(path: str, page_number: int, text: str) -> Optional[dict]:
    """The area of the passage starting with ``text`` on page ``page_number``
    (1-based) as page fractions ``{x, y, w, h}`` (origin top left, like an
    annotation's ``rect``), or ``None``: page not in the file, start not
    found, or found more than once. Raises ``UnreadablePdf`` like ``read``."""
    starts = _starts(text)
    if not starts:
        return None
    doc = _open(path)
    try:
        if not 1 <= page_number <= doc.page_count:
            return None
        page = doc[page_number - 1]
        page_text = page.get_text()
        for start in starts:
            hits = page.search_for(start)
            if not hits:
                continue
            if _occurrences(page_text, start) > 1:
                return None      # a shorter start only matches more often
            return _fraction(page, _paragraph(page, hits))
        return None
    except RuntimeError as exc:   # MuPDF's failures; our own bugs pass through
        raise UnreadablePdf(f"{type(exc).__name__}: {exc}") from exc
    finally:
        doc.close()


def render_image(path: str, page_index: int, area: fitz.Rect) -> bytes:
    """PNG of an annotation's area with a margin, its annotations drawn.
    Raises ``UnreadablePdf`` like ``read`` - also for a page the file no
    longer has because it was exchanged since the reading."""
    doc = _open(path)
    try:
        if not 0 <= page_index < doc.page_count:   # the file was exchanged since the reading
            raise UnreadablePdf("page no longer in the file") from None
        page = doc[page_index]
        margin = max(8.0, min(36.0, 0.1 * max(area.width, area.height)))
        clip = fitz.Rect(area.x0 - margin, area.y0 - margin, area.x1 + margin, area.y1 + margin) & page.rect
        if clip.is_empty:
            clip = page.rect
        zoom = max(1.0, min(3.0, IMAGE_MAX_SIDE / max(clip.width, clip.height, 1)))
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, annots=True)
        return pix.tobytes("png")
    except RuntimeError as exc:   # MuPDF's failures; our own bugs pass through
        raise UnreadablePdf(f"{type(exc).__name__}: {exc}") from exc
    finally:
        doc.close()
