"""Annotations read from an Item's PDF (#229, spec #222 A) - over HTTP.

Every test builds its PDF with PyMuPDF, files it as an Item and asks the API,
like ``test_pdf_attach_api.py``. The cases are the spec's Nahtstelle 1: quote
and comment, whole words with ``…``, hyphenation and ligature, the merged
double mark, the sticky note's context, replies, shapes with caption and
picture, ink, what is never listed, sections from the outline and from bold
headings, the order, the error codes and a new *Stand* after the file changed,
which the Item's detail carries too (its preview URL is built from it).
Since the review of #235 also: loops and groups in reply chains, readers that
are not merged, times compared as moments, URL-safe unique ids, PyMuPDF
failures as the error code, and concurrent requests that read one at a time.
"""
from __future__ import annotations

import mimetypes
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fitz
import pytest

from literature_manager import Config
import pdf_annotations

REPO = Path(__file__).resolve().parent.parent
REGULAR = fitz.Font("tiro")   # keeps ligature glyphs (ﬁ, ﬂ), unlike Helvetica
BOLD = fitz.Font("tibo")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text(page, lines, *, x=50, y=100, size=10, leading=13, font=REGULAR):
    """Lines of text, one block if the leading is normal."""
    tw = fitz.TextWriter(page.rect)
    for i, line in enumerate(lines):
        tw.append((x, y + i * leading), line, font=font, fontsize=size)
    tw.write_text(page)


def _word(page, word, index=0):
    """The rectangle of a word exactly as the page holds it (ligatures included)."""
    hits = [fitz.Rect(w[:4]) for w in page.get_text("words") if w[4] == word]
    return hits[index]


def _highlight(page, rects, *, comment="", author="Reader A", color=(1, 1, 0), kind="highlight"):
    add = {"highlight": page.add_highlight_annot, "underline": page.add_underline_annot,
           "strike": page.add_strikeout_annot, "squiggly": page.add_squiggly_annot}[kind]
    annot = add(rects if isinstance(rects, list) else [rects])
    annot.set_info(content=comment, title=author, creationDate=fitz.get_pdf_now())
    annot.set_colors(stroke=color)
    annot.update()
    return annot


def _new_doc(pages=1):
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=420, height=600)
    return doc


def _set(doc, annot, **keys):
    """Raw PDF keys of an annotation, e.g. ``NM="(a)"`` or ``IRT="5 0 R"``."""
    for key, value in keys.items():
        doc.xref_set_key(annot.xref, key, value)


def _reply(doc, page, target, text, created, *, author="Reader B"):
    """A sticky note answering ``target`` (``/IRT``), created at a PDF date."""
    note = page.add_text_annot((300, 90), text)
    note.set_info(title=author)
    note.update()
    _set(doc, note, IRT=f"{target.xref} 0 R", CreationDate=f"({created})")
    return note


_counter = {"n": 0}


def _item(db, doc_or_bytes, *, filename=None) -> int:
    """File the PDF as an Item and return its id."""
    _counter["n"] += 1
    name = filename or f"annot_{_counter['n']}.pdf"
    data = doc_or_bytes if isinstance(doc_or_bytes, bytes) else doc_or_bytes.tobytes()
    (Path(Config.ALL_DIR) / name).write_bytes(data)
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title, authors, year) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"annot-hash-{_counter['n']}", name, name, "Annotated Item", "Doe, J.", 2026),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _list(client, paper_id):
    resp = client.get(f"/api/papers/{paper_id}/annotations")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Quotes and comments
# ---------------------------------------------------------------------------

def test_highlight_with_comment_lists_quote_comment_and_metadata(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["Introduction first. The model reads every page carefully.",
                 "A second sentence follows here."])
    rect = _word(page, "The") | _word(page, "carefully.")
    _highlight(page, rect, comment="Check this claim", author="Reader A", color=(1, 0.933, 0.345))
    body = _list(client, _item(db, doc))

    assert body["pages"] == 1
    assert body["multiple_authors"] is False
    assert re.match(r"\d{4}-\d{2}-\d{2}T", body["file_modified"])
    [entry] = body["annotations"]
    assert entry["kind"] == "highlight"
    assert entry["subtype"] == "Highlight"
    assert entry["page"] == 1
    assert entry["quote"] == "The model reads every page carefully."
    assert entry["comment"] == "Check this claim"
    assert entry["author"] == "Reader A"
    assert entry["color"] == "#FFEE58"
    assert entry["merged"] == 1
    assert entry["has_image"] is False
    assert entry["replies"] == []
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", entry["created"])
    assert entry["id"]
    rect = entry["rect"]
    assert 0 < rect["x"] < 1 and 0 < rect["y"] < 1 and 0 < rect["w"] < 1 and 0 < rect["h"] < 1


def test_without_plain_text_the_rich_text_comment_is_used_without_markup(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["Only the rich text carries this comment."])
    mark = _highlight(page, _word(page, "rich"))
    doc.xref_set_key(mark.xref, "Contents", "()")
    doc.xref_set_key(mark.xref, "RC", "(<?xml version=\"1.0\"?><body><p>Rich <b>bold</b> &amp; plain</p></body>)")
    [entry] = _list(client, _item(db, doc))["annotations"]
    assert entry["comment"] == "Rich bold & plain"


def test_a_mark_starting_mid_word_takes_whole_words_and_says_so(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["Results show that tokenization dominates the overall cost of analysis today."])
    start, end = _word(page, "tokenization"), _word(page, "overall")
    rect = fitz.Rect(start.x0 + start.width / 2, start.y0, end.x0 + end.width / 2, end.y1)
    _highlight(page, rect)
    [entry] = _list(client, _item(db, doc))["annotations"]
    assert entry["quote"] == "…tokenization dominates the overall…"


def test_hyphenation_and_ligatures_are_joined(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["Larger challenges required more tokens for thorough anal-",
                 "ysis, reﬂecting the workload of long functions."])
    _text(page, ["We send every High-", "Risk Function to the model first."], y=200)
    _highlight(page, [_word(page, "thorough") | _word(page, "anal-"),
                      _word(page, "ysis,") | _word(page, "reﬂecting")])
    _highlight(page, [_word(page, "High-"), _word(page, "Risk")], color=(0, 1, 0))
    first, second = _list(client, _item(db, doc))["annotations"]
    # Syllable break joined without its hyphen, the ligature folded (ﬂ -> fl).
    assert first["quote"] == "…thorough analysis, reflecting…"
    # A compound broken at the line end keeps its hyphen.
    assert second["quote"] == "…High-Risk…"


def test_a_double_mark_is_one_entry_but_two_comments_stay_apart(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["The pipeline extracts functions and ranks them by risk."])
    _text(page, ["Every ranked function goes to the language model."], y=200)
    words = _word(page, "extracts") | _word(page, "ranks")
    _highlight(page, words)
    _highlight(page, fitz.Rect(words.x0 + 4, words.y0, words.x1 - 4, words.y1))   # the same again
    _highlight(page, words, color=(0, 1, 0))                                     # another colour
    line2 = _word(page, "ranked") | _word(page, "language")
    _highlight(page, line2, color=(0, 0, 1), comment="one thought")
    _highlight(page, line2, color=(0, 0, 1), comment="another thought")
    entries = _list(client, _item(db, doc))["annotations"]

    yellow = [e for e in entries if e["color"] == "#FFFF00"]
    assert len(yellow) == 1 and yellow[0]["merged"] == 2
    assert yellow[0]["quote"] == "…extracts functions and ranks…"
    assert [e["merged"] for e in entries if e["color"] == "#00FF00"] == [1]
    blue = [e for e in entries if e["color"] == "#0000FF"]
    assert sorted(e["comment"] for e in blue) == ["another thought", "one thought"]


def test_marks_of_different_readers_are_not_merged(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["Both readers marked exactly the same words here."])
    words = _word(page, "marked") | _word(page, "words")
    _highlight(page, words, author="Reader A")
    _highlight(page, words, author="Reader B")
    body = _list(client, _item(db, doc))
    assert sorted(e["author"] for e in body["annotations"]) == ["Reader A", "Reader B"]
    assert [e["merged"] for e in body["annotations"]] == [1, 1]
    assert body["multiple_authors"] is True


def test_merged_marks_keep_their_replies_in_time_order(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["Every function is ranked by its risk score."])
    words = _word(page, "function") | _word(page, "ranked")
    first = _highlight(page, words)
    second = _highlight(page, words)
    _reply(doc, page, first, "late answer", "D:20261009120000Z")
    _reply(doc, page, second, "early answer", "D:20261009110000Z")
    [entry] = _list(client, _item(db, doc))["annotations"]
    assert entry["merged"] == 2
    assert [r["text"] for r in entry["replies"]] == ["early answer", "late answer"]


def test_times_with_different_offsets_are_compared_as_moments(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["Two readers in two time zones marked the same words."])
    words = _word(page, "marked") | _word(page, "same")
    east = _highlight(page, words)
    west = _highlight(page, words)
    # 10:00 at +02:00 is 08:00 UTC: earlier than 09:00 UTC, though it sorts later as text.
    _set(doc, east, CreationDate="(D:20261009100000+02'00')", M="(D:20261009100000+02'00')")
    _set(doc, west, CreationDate="(D:20261009090000Z)", M="(D:20261009090000Z)")
    _reply(doc, page, east, "first", "D:20261009100000+02'00'")
    _reply(doc, page, east, "second", "D:20261009090000Z")
    [entry] = _list(client, _item(db, doc))["annotations"]
    assert entry["merged"] == 2
    assert entry["created"] == "2026-10-09T10:00:00+02:00"
    assert entry["modified"] == "2026-10-09T09:00:00+00:00"
    assert [r["text"] for r in entry["replies"]] == ["first", "second"]


def test_an_impossible_time_zone_offset_keeps_the_time_without_one(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["A mark made by a reader with a broken clock."])
    mark = _highlight(page, _word(page, "broken"))
    _set(doc, mark, CreationDate="(D:20261009203918+24'00')", M="(D:20261009203918+02'75')")
    [entry] = _list(client, _item(db, doc))["annotations"]
    assert entry["created"] == "2026-10-09T20:39:18"
    assert entry["modified"] == "2026-10-09T20:39:18"


# ---------------------------------------------------------------------------
# Notes, replies, shapes
# ---------------------------------------------------------------------------

def test_a_sticky_note_carries_the_line_under_its_icon(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["The first line opens the paragraph about costs.",
                 "This refined statistical mechanism allows researchers",
                 "to clearly understand the token usage of the model."])
    target = _word(page, "allows")
    note = page.add_text_annot((target.x0 + target.width / 2 - 10, target.y0 - 4), "Einfach so ein Kommentar")
    note.set_info(title="Reader A")
    note.update()
    [entry] = _list(client, _item(db, doc))["annotations"]
    assert entry["kind"] == "comment"
    assert entry["subtype"] == "Text"
    assert entry["comment"] == "Einfach so ein Kommentar"
    assert entry["quote"] is None
    assert "allows" in entry["context"]
    assert entry["context"].startswith("…") and entry["context"].endswith("…")
    assert entry["has_image"] is False


def test_a_reply_hangs_on_the_annotation_it_answers(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["Token counts were logged for every session of the run."])
    mark = _highlight(page, _word(page, "Token") | _word(page, "logged"), comment="Why logged?")
    reply = page.add_text_annot((300, 90), "Because of the cost metric.")
    reply.set_info(title="Reader B", creationDate=fitz.get_pdf_now())
    reply.update()
    doc.xref_set_key(reply.xref, "IRT", f"{mark.xref} 0 R")
    body = _list(client, _item(db, doc))
    [entry] = body["annotations"]
    assert entry["comment"] == "Why logged?"
    assert [(r["author"], r["text"]) for r in entry["replies"]] == [("Reader B", "Because of the cost metric.")]
    assert body["multiple_authors"] is True


def test_an_annotation_answering_itself_or_round_in_a_loop_stays_an_entry(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["Alpha words here and beta words there, gamma at the end."])
    own = _highlight(page, _word(page, "Alpha"), comment="answers itself")
    a = _highlight(page, _word(page, "beta"), comment="loop a", color=(0, 1, 0))
    b = _highlight(page, _word(page, "gamma"), comment="loop b", color=(0, 0, 1))
    _set(doc, own, IRT=f"{own.xref} 0 R")
    _set(doc, a, IRT=f"{b.xref} 0 R")
    _set(doc, b, IRT=f"{a.xref} 0 R")
    _reply(doc, page, a, "into the loop", "D:20261009120000Z")
    entries = _list(client, _item(db, doc))["annotations"]
    assert [e["comment"] for e in entries] == ["answers itself", "loop a", "loop b"]
    # A real answer to a member of the loop still hangs on it.
    assert [[r["text"] for r in e["replies"]] for e in entries] == [[], ["into the loop"], []]


def test_a_grouped_annotation_is_an_entry_of_its_own_not_a_reply(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["The old wording stays in the text for now."])
    old = _word(page, "old") | _word(page, "wording")
    caret = page.add_caret_annot(fitz.Point(old.x1 + 1, old.y1))
    caret.set_info(content="the new wording", title="Reader A", creationDate="D:20261009120000Z")
    caret.update()
    # Acrobat's "replace text": the strike-out is grouped with the caret and
    # carries neither a comment nor an author of its own.
    strike = page.add_strikeout_annot(old)
    _set(doc, strike, IRT=f"{caret.xref} 0 R", RT="/Group")
    entries = _list(client, _item(db, doc))["annotations"]
    by_subtype = {e["subtype"]: e for e in entries}
    assert sorted(by_subtype) == ["Caret", "StrikeOut"]
    struck = by_subtype["StrikeOut"]
    assert struck["quote"] == "…old wording…"
    assert struck["comment"] is None
    assert struck["author"] == "Reader A"
    assert struck["created"] == "2026-10-09T12:00:00+00:00"
    assert by_subtype["Caret"]["comment"] == "the new wording"
    assert by_subtype["Caret"]["replies"] == []


def test_a_rectangle_gets_its_text_as_caption_and_a_picture(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["4 Experiments"], y=100, size=12, font=BOLD)
    _text(page, ["All experiments ran on one workstation.", "The model was reached through an API."], y=125)
    square = page.add_rect_annot(fitz.Rect(40, 85, 380, 150))
    square.set_colors(stroke=(1, 0, 0))
    square.update()
    paper_id = _item(db, doc)
    [entry] = _list(client, paper_id)["annotations"]
    assert entry["kind"] == "shape"
    assert entry["subtype"] == "Square"
    assert entry["color"] == "#FF0000"
    assert entry["has_image"] is True
    assert entry["caption"].startswith("4 Experiments — All experiments ran on one workstation.")

    img = client.get(f"/api/papers/{paper_id}/annotations/{entry['id']}/image")
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"
    assert img.content.startswith(b"\x89PNG")


def test_handwriting_is_a_picture(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["Some text the pen went over."])
    page.add_ink_annot([[(60, 120), (90, 140), (120, 118)]])
    [entry] = _list(client, _item(db, doc))["annotations"]
    assert entry["subtype"] == "Ink"
    assert entry["kind"] == "shape"
    assert entry["has_image"] is True
    assert entry["quote"] is None and entry["caption"] is None


def test_links_and_popups_are_never_listed(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["See the publisher page for details."])
    page.insert_link({"kind": fitz.LINK_URI, "from": _word(page, "publisher"), "uri": "https://example.org"})
    mark = _highlight(page, _word(page, "details."), comment="popup holder")
    mark.set_popup(fitz.Rect(200, 200, 300, 260))
    mark.update()
    entries = _list(client, _item(db, doc))["annotations"]
    assert [e["subtype"] for e in entries] == ["Highlight"]


# ---------------------------------------------------------------------------
# Ids
# ---------------------------------------------------------------------------

def test_an_id_of_dots_alone_is_replaced_and_its_picture_found(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["Two framed words."])
    for nm, word in ((".", "Two"), ("..", "framed")):
        square = page.add_rect_annot(_word(page, word) + (-2, -2, 2, 2))
        _set(doc, square, NM=f"({nm})")
    paper_id = _item(db, doc)
    entries = _list(client, paper_id)["annotations"]
    assert len(entries) == 2
    for entry in entries:
        # "." and ".." are path segments a browser resolves away.
        assert entry["id"].strip(".")
        img = client.get(f"/api/papers/{paper_id}/annotations/{entry['id']}/image")
        assert img.status_code == 200
        assert img.content.startswith(b"\x89PNG")


def test_ids_stay_unique_when_a_fallback_id_is_already_taken(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["First words.", "Second words.", "Third words."], leading=40)
    marks = [_highlight(page, _word(page, word)) for word in ("First", "Second", "Third")]
    # The first reader's own id is exactly the fallback the third one would get.
    for mark, nm in zip(marks, [f"a-x{marks[2].xref}", "a", "a"]):
        _set(doc, mark, NM=f"({nm})")
    entries = _list(client, _item(db, doc))["annotations"]
    ids = [e["id"] for e in entries]
    assert len(ids) == 3 and len(set(ids)) == 3


# ---------------------------------------------------------------------------
# Sections and order
# ---------------------------------------------------------------------------

def test_sections_come_from_the_embedded_outline(client, db):
    doc = _new_doc(2)
    _text(doc[0], ["Opening words of the paper."], y=80)
    _text(doc[0], ["Method words come later on."], y=300)
    _text(doc[1], ["Second page words."], y=80)
    doc.set_toc([
        [1, "1 Introduction", 1, {"kind": fitz.LINK_GOTO, "to": fitz.Point(50, 60), "page": 0}],
        [1, "2 Method", 1, {"kind": fitz.LINK_GOTO, "to": fitz.Point(50, 280), "page": 0}],
        [2, "2.1 Details", 2],
    ])
    _highlight(doc[0], _word(doc[0], "Opening"))
    _highlight(doc[0], _word(doc[0], "Method"))
    _highlight(doc[1], _word(doc[1], "Second"))
    body = _list(client, _item(db, doc))
    assert body["has_sections"] is True
    assert [e["section"] for e in body["annotations"]] == ["1 Introduction", "2 Method", "2.1 Details"]


def test_sections_come_from_bold_headings_but_never_from_captions(client, db):
    doc = _new_doc(7)
    _text(doc[0], ["2 Method"], y=60, size=11, font=BOLD)
    _text(doc[0], ["The method reads every page of the document."], y=80)
    _text(doc[0], ["Table 1. Results of the run"], y=200, size=11, font=BOLD)
    _text(doc[0], ["After the table the method goes on."], y=230)
    _text(doc[6], ["A page far behind the last heading."], y=80)
    _highlight(doc[0], _word(doc[0], "reads"))
    _highlight(doc[0], _word(doc[0], "goes"))
    _highlight(doc[6], _word(doc[6], "far"))
    body = _list(client, _item(db, doc))
    assert body["has_sections"] is True
    # The caption is no heading; seven pages on, the search has given up.
    assert [e["section"] for e in body["annotations"]] == ["2 Method", "2 Method", None]


def test_without_headings_there_are_no_sections(client, db):
    doc = _new_doc()
    _text(doc[0], ["Plain text without any heading at all."])
    _highlight(doc[0], _word(doc[0], "Plain"))
    body = _list(client, _item(db, doc))
    assert body["has_sections"] is False
    assert body["annotations"][0]["section"] is None


def test_entries_follow_page_then_top_to_bottom_then_left_to_right(client, db):
    doc = _new_doc(2)
    _text(doc[0], ["Alpha beta gamma delta epsilon."], y=80)
    _text(doc[0], ["Lower line of the page."], y=400)
    _text(doc[1], ["Second page line."], y=80)
    _highlight(doc[1], _word(doc[1], "Second"), comment="4")
    _highlight(doc[0], _word(doc[0], "Lower"), comment="3")
    _highlight(doc[0], _word(doc[0], "delta"), comment="2", color=(0, 1, 0))
    _highlight(doc[0], _word(doc[0], "Alpha"), comment="1", color=(0, 0, 1))
    entries = _list(client, _item(db, doc))["annotations"]
    assert [e["comment"] for e in entries] == ["1", "2", "3", "4"]
    assert [e["page"] for e in entries] == [1, 1, 1, 2]


# ---------------------------------------------------------------------------
# Errors and freshness
# ---------------------------------------------------------------------------

def test_an_unknown_item_is_reported(client, db):
    resp = client.get("/api/papers/999999/annotations")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "error.item_not_found"


def test_an_item_without_pdf_is_reported(client, db):
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title) VALUES (?, '', '', ?)",
            ("annot-no-pdf", "Only Metadata"))
        conn.commit()
        paper_id = cur.lastrowid
    finally:
        conn.close()
    resp = client.get(f"/api/papers/{paper_id}/annotations")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "error.no_pdf_for_item"


def test_an_unknown_annotation_is_reported(client, db):
    doc = _new_doc()
    _text(doc[0], ["Some marked words."])
    _highlight(doc[0], _word(doc[0], "marked"))
    paper_id = _item(db, doc)
    resp = client.get(f"/api/papers/{paper_id}/annotations/no-such-id/image")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "error.annotation_not_found"


def test_a_password_protected_pdf_is_unreadable_but_an_empty_password_is_not(client, db):
    doc = _new_doc()
    _text(doc[0], ["Secret words in a locked file."])
    _highlight(doc[0], _word(doc[0], "Secret"))
    locked = _item(db, doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner"))
    resp = client.get(f"/api/papers/{locked}/annotations")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "error.annotations.unreadable"

    owner_only = _item(db, doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="owner"))
    assert len(_list(client, owner_only)["annotations"]) == 1


def test_a_pdf_that_fails_while_being_read_is_unreadable_not_a_server_error(client, db, monkeypatch):
    doc = _new_doc()
    _text(doc[0], ["A page whose annotations cannot be parsed."])
    _highlight(doc[0], _word(doc[0], "parsed."))
    paper_id = _item(db, doc)

    def damaged(self, *args, **kwargs):
        raise RuntimeError("cannot parse annotation")

    monkeypatch.setattr(fitz.Page, "annots", damaged)
    resp = client.get(f"/api/papers/{paper_id}/annotations")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "error.annotations.unreadable"


def test_a_failure_while_reading_is_logged_but_a_file_that_is_no_pdf_is_not(client, db, monkeypatch, caplog):
    doc = _new_doc()
    _text(doc[0], ["Words."])
    _highlight(doc[0], _word(doc[0], "Words."))
    broken = _item(db, doc)
    junk = _item(db, b"this is not a pdf at all")
    empty = _item(db, b"")

    for paper_id in (junk, empty):
        with caplog.at_level("WARNING"):
            resp = client.get(f"/api/papers/{paper_id}/annotations")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "error.annotations.unreadable"
    assert not [r for r in caplog.records if "Annotations" in r.getMessage()]

    def damaged(self, *args, **kwargs):
        raise RuntimeError("cannot parse annotation")

    monkeypatch.setattr(fitz.Page, "annots", damaged)
    with caplog.at_level("WARNING"):
        assert client.get(f"/api/papers/{broken}/annotations").status_code == 409
    assert [r for r in caplog.records if "Annotations" in r.getMessage()]


def test_an_error_of_our_own_is_not_mistaken_for_an_unreadable_pdf(client, db, monkeypatch):
    import pdf_annotations

    doc = _new_doc()
    _text(doc[0], ["Words."])
    _highlight(doc[0], _word(doc[0], "Words."))
    paper_id = _item(db, doc)

    def bug(*args, **kwargs):
        raise KeyError("our own mistake")

    monkeypatch.setattr(pdf_annotations, "_read", bug)
    try:
        resp = client.get(f"/api/papers/{paper_id}/annotations")
    except KeyError:
        return   # the test client re-raises server errors: not a 409
    assert resp.status_code == 500


def test_an_annotation_dated_near_year_one_leaves_the_rest_of_the_list_intact(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["Old dated words and recent words."])
    target = _highlight(page, _word(page, "Old"))
    _reply(doc, page, target, "ancient", "D:00010101000000+01'00'")
    _reply(doc, page, target, "recent", "D:20261009203918+02'00'")
    _highlight(page, _word(page, "recent"))
    paper_id = _item(db, doc)
    entries = _list(client, paper_id)["annotations"]
    assert len(entries) == 2
    first = next(e for e in entries if e["quote"].startswith("Old"))
    assert [r["text"] for r in first["replies"]].count("recent") == 1
    assert len(first["replies"]) == 2


def test_a_picture_of_a_page_the_file_no_longer_has_is_unreadable(client, db):
    two_pages = _new_doc(2)
    _text(two_pages[1], ["Words on the second page."])
    two_pages[1].add_rect_annot(fitz.Rect(40, 85, 200, 120))
    one_page = _new_doc(1)
    _text(one_page[0], ["Only one page now."])
    versions = [two_pages.tobytes(), one_page.tobytes()]
    size = max(len(v) for v in versions) + 16
    # The same size and time: the file was exchanged under the cached Stand.
    first, second = (v + b"\n%" + b" " * (size - len(v) - 2) for v in versions)
    paper_id = _item(db, first, filename="annot_exchanged.pdf")
    path = Path(Config.ALL_DIR) / "annot_exchanged.pdf"
    [entry] = _list(client, paper_id)["annotations"]
    stamp = path.stat()
    path.write_bytes(second)
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    img = client.get(f"/api/papers/{paper_id}/annotations/{entry['id']}/image")
    assert img.status_code == 409
    assert img.json()["detail"] == "error.annotations.unreadable"


def test_pdf_work_of_concurrent_requests_runs_one_at_a_time(client, db, monkeypatch):
    import pdf_annotations

    doc = _new_doc()
    page = doc[0]
    _text(page, ["Six framed words wait here for their pictures."])
    for word in ("Six", "framed", "words", "wait", "here", "their"):
        page.add_rect_annot(_word(page, word) + (-1, -1, 1, 1))
    paper_id = _item(db, doc)

    guard = threading.Lock()
    state = {"active": 0, "peak": 0, "reads": 0}

    def tracked(work, *, counts_as_read=False):
        def run(*args, **kwargs):
            with guard:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
                state["reads"] += counts_as_read
            try:
                time.sleep(0.05)   # long enough for an unserialised second call to overlap
                return work(*args, **kwargs)
            finally:
                with guard:
                    state["active"] -= 1
        return run

    monkeypatch.setattr(pdf_annotations, "read", tracked(pdf_annotations.read, counts_as_read=True))
    monkeypatch.setattr(pdf_annotations, "render_image", tracked(pdf_annotations.render_image))

    with ThreadPoolExecutor(max_workers=6) as pool:
        lists = list(pool.map(lambda _: client.get(f"/api/papers/{paper_id}/annotations"), range(6)))
    assert [r.status_code for r in lists] == [200] * 6
    # Requests that waited for the first reading take it from the cache.
    assert state["reads"] == 1

    ids = [e["id"] for e in lists[0].json()["annotations"]]
    assert len(ids) == 6
    with ThreadPoolExecutor(max_workers=6) as pool:
        images = list(pool.map(
            lambda ident: client.get(f"/api/papers/{paper_id}/annotations/{ident}/image"), ids))
    assert [r.status_code for r in images] == [200] * 6
    assert all(r.content.startswith(b"\x89PNG") for r in images)
    assert state["peak"] == 1


def test_a_saved_file_gives_a_new_stand_and_a_fresh_list(client, db):
    doc = _new_doc()
    page = doc[0]
    _text(page, ["First words and later words."])
    _highlight(page, _word(page, "First"))
    paper_id = _item(db, doc, filename="annot_resaved.pdf")
    before = _list(client, paper_id)

    _highlight(page, _word(page, "later"), color=(0, 1, 0))
    path = Path(Config.ALL_DIR) / "annot_resaved.pdf"
    path.write_bytes(doc.tobytes())
    stamp = path.stat().st_mtime + 5
    os.utime(path, (stamp, stamp))
    after = _list(client, paper_id)

    assert after["file_modified"] != before["file_modified"]
    assert len(before["annotations"]) == 1 and len(after["annotations"]) == 2


def test_the_item_carries_the_stand_its_preview_url_is_built_from(client, db):
    doc = _new_doc()
    _text(doc[0], ["Words on a page."])
    _highlight(doc[0], _word(doc[0], "Words"))
    paper_id = _item(db, doc, filename="annot_stand_detail.pdf")

    item = client.get(f"/api/papers/{paper_id}").json()
    assert item["pdf_modified"] == _list(client, paper_id)["file_modified"]

    path = Path(Config.ALL_DIR) / "annot_stand_detail.pdf"
    stamp = path.stat().st_mtime + 5
    os.utime(path, (stamp, stamp))
    assert client.get(f"/api/papers/{paper_id}").json()["pdf_modified"] != item["pdf_modified"]

    path.unlink()   # the Item stays readable without its file
    assert client.get(f"/api/papers/{paper_id}").json()["pdf_modified"] is None


def test_an_unusable_file_stat_never_fails_the_item(client, db, monkeypatch):
    doc = _new_doc()
    _text(doc[0], ["Words on a page."])
    paper_id = _item(db, doc, filename="annot_stand_odd.pdf")

    def out_of_range(path):
        raise OverflowError("timestamp out of range for platform time_t")
    monkeypatch.setattr(pdf_annotations, "file_state", out_of_range)
    resp = client.get(f"/api/papers/{paper_id}")
    assert resp.status_code == 200 and resp.json()["pdf_modified"] is None


def test_reading_never_writes_the_file(client, db):
    doc = _new_doc()
    _text(doc[0], ["Words that stay exactly as they were."])
    _highlight(doc[0], _word(doc[0], "stay"))
    paper_id = _item(db, doc, filename="annot_untouched.pdf")
    path = Path(Config.ALL_DIR) / "annot_untouched.pdf"
    data, mtime = path.read_bytes(), path.stat().st_mtime_ns
    entry = _list(client, paper_id)["annotations"][0]
    client.get(f"/api/papers/{paper_id}/annotations/{entry['id']}/image")
    assert path.read_bytes() == data
    assert path.stat().st_mtime_ns == mtime


# ---------------------------------------------------------------------------
# The preview: vendored PDF.js, served as JavaScript; the PDF never cached
# ---------------------------------------------------------------------------

def _pdfjs_dir() -> str:
    app_js = (REPO / "static" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"const PDFJS_DIR = '/static/(vendor/pdfjs-[\d.]+)/';", app_js)
    assert m, "app.js names no vendored PDF.js directory"
    return m.group(1)


def test_the_vendored_pdfjs_the_spa_imports_is_complete():
    folder = REPO / "static" / _pdfjs_dir()
    for name in ("pdf.min.mjs", "pdf.worker.min.mjs", "pdf_viewer.mjs", "pdf_viewer.css", "LICENSE"):
        assert (folder / name).is_file(), name
    assert "Apache License" in (folder / "LICENSE").read_text(encoding="utf-8")
    # Neither CMaps nor standard fonts: their binary formats are unreviewable.
    assert not list(folder.rglob("*.bcmap")) and not list(folder.rglob("*.pfb"))


@pytest.mark.parametrize("name", ["pdf.min.mjs", "pdf.worker.min.mjs", "pdf_viewer.mjs"])
def test_pdfjs_modules_are_served_as_javascript_whatever_the_machine_says(client, monkeypatch, name):
    import starlette.responses

    # A Windows registry that maps scripts to text/plain must not matter.
    monkeypatch.setattr(starlette.responses, "guess_type", lambda *a, **k: ("text/plain", None))
    monkeypatch.setattr(mimetypes, "guess_type", lambda *a, **k: ("text/plain", None))
    resp = client.get(f"/static/{_pdfjs_dir()}/{name}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/javascript")


def test_the_pdf_preview_is_asked_again_every_time(client, db):
    doc = _new_doc()
    _text(doc[0], ["Preview text."])
    paper_id = _item(db, doc)
    resp = client.get(f"/api/papers/{paper_id}/pdf")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"
