"""Where a passage starts on a PDF page (contract 2.6) - over HTTP.

``GET /api/papers/{id}/locate?page=N&text=...`` answers the area of the
paragraph the passage starts in, as the page fractions the preview's frame
takes (like an annotation's ``rect``), or ``null`` - never a guessed spot.
Every test builds its PDF with PyMuPDF and files it as an Item, like
``test_annotations_api.py``.
"""
from __future__ import annotations

from pathlib import Path

import fitz

from literature_manager import Config

FONT = fitz.Font("tiro")

PARAGRAPH = [
    "Lifecycle costs of bridges are dominated by the maintenance",
    "measures over a service life of many decades, while the",
    "construction costs account for a smaller share of the total.",
]
OTHER = [
    "Concrete carbonation proceeds with the square root of time",
    "and depends on the humidity of the surrounding air.",
]
PASSAGE = " ".join(PARAGRAPH) + " Further text of the chunk follows here."

_counter = {"n": 0}


def _text(page, lines, *, x=50, y=100, size=10, leading=13):
    tw = fitz.TextWriter(page.rect)
    for i, line in enumerate(lines):
        tw.append((x, y + i * leading), line, font=FONT, fontsize=size)
    tw.write_text(page)


def _item(db, doc=None, *, filename="") -> int:
    _counter["n"] += 1
    name = filename
    if doc is not None:
        name = f"locate_{_counter['n']}.pdf"
        (Path(Config.ALL_DIR) / name).write_bytes(doc.tobytes())
    conn = db._connect()
    try:
        cur = conn.execute(
            "INSERT INTO papers (file_hash, filename, original_filename, title, authors, year) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"locate-hash-{_counter['n']}", name, name, "Bridges", "Doe, J.", 2026),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _two_pages():
    doc = fitz.open()
    first = doc.new_page(width=420, height=600)
    _text(first, PARAGRAPH, y=200)
    _text(first, OTHER, y=400)
    second = doc.new_page(width=420, height=600)
    _text(second, OTHER, y=100)
    return doc


def _locate(client, paper_id, page, text):
    resp = client.get(f"/api/papers/{paper_id}/locate", params={"page": page, "text": text})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_found_passage_answers_the_area_of_its_paragraph(client, db):
    pid = _item(db, _two_pages())
    out = _locate(client, pid, 1, PASSAGE)
    assert out["page"] == 1
    rect = out["rect"]
    assert set(rect) == {"x", "y", "w", "h"}
    # The paragraph: three lines from y=200 at 10 pt on a 600 pt page, x from 50.
    assert 0.10 <= rect["x"] <= 0.13
    assert 0.30 <= rect["y"] <= 0.33
    assert 0.06 <= rect["h"] <= 0.09          # all three lines, not just the first
    assert 0.5 <= rect["w"] <= 0.9
    # The other paragraph further down is not part of it.
    assert rect["y"] + rect["h"] < 400 / 600


def test_a_shorter_start_is_tried_when_the_long_one_is_not_on_the_page(client, db):
    pid = _item(db, _two_pages())
    garbled = " ".join(PARAGRAPH[0].split()[:6]) + " zzqx wwvk lorem ipsum dolor sit"
    out = _locate(client, pid, 1, garbled)
    assert out["rect"] is not None and 0.30 <= out["rect"]["y"] <= 0.33


def test_text_not_on_the_page_is_null(client, db):
    pid = _item(db, _two_pages())
    assert _locate(client, pid, 1, "Nothing of this sentence stands anywhere in the file.")["rect"] is None
    assert _locate(client, pid, 1, "")["rect"] is None
    assert _locate(client, pid, 1, "too short")["rect"] is None


def test_the_wrong_page_is_null(client, db):
    pid = _item(db, _two_pages())
    assert _locate(client, pid, 2, PASSAGE)["rect"] is None
    assert _locate(client, pid, 9, PASSAGE) == {"page": 9, "rect": None}
    # OTHER stands on both pages: on page 2 it is found there.
    assert _locate(client, pid, 2, " ".join(OTHER))["rect"]["y"] < 0.2


def test_a_start_that_occurs_twice_on_the_page_is_null(client, db):
    doc = fitz.open()
    page = doc.new_page(width=420, height=600)
    _text(page, OTHER, y=100)
    _text(page, OTHER, y=300)
    pid = _item(db, doc)
    assert _locate(client, pid, 1, " ".join(OTHER))["rect"] is None


def test_a_rotated_page_answers_what_the_viewer_shows(client, db):
    doc = _two_pages()
    doc[0].set_rotation(90)
    pid = _item(db, doc)
    rect = _locate(client, pid, 1, PASSAGE)["rect"]
    # Turned by 90 degrees the wide, low paragraph stands tall and narrow.
    assert rect is not None and rect["h"] > rect["w"]


def test_errors_are_codes(client, db):
    assert client.get("/api/papers/999999/locate?page=1&text=x").json()["detail"] == "error.item_not_found"
    no_pdf = _item(db)
    resp = client.get(f"/api/papers/{no_pdf}/locate?page=1&text=x")
    assert resp.status_code == 404 and resp.json()["detail"] == "error.no_pdf_for_item"
    pid = _item(db, _two_pages())
    assert client.get(f"/api/papers/{pid}/locate?page=0&text=x").status_code == 422
    assert client.get(f"/api/papers/{pid}/locate?text=x").status_code == 422


def test_an_unreadable_file_is_409(client, db):
    name = "locate_broken.pdf"
    (Path(Config.ALL_DIR) / name).write_bytes(b"%PDF-1.4 this is no pdf at all")
    pid = _item(db, filename=name)
    resp = client.get(f"/api/papers/{pid}/locate", params={"page": 1, "text": PASSAGE})
    assert resp.status_code == 409 and resp.json()["detail"] == "error.annotations.unreadable"
