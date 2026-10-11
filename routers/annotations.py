"""Router: the annotations a reader set in an Item's PDF (#229, spec #222 A).

Serves:
  GET /api/papers/{paper_id}/annotations
  GET /api/papers/{paper_id}/annotations/{annotation_id}/image

Read-only by design: the PDF is the only source, nothing is persisted and the
file is never written. Every rule lives in the pure module ``pdf_annotations``;
this router finds the Item's file, keeps one reading per file *Stand* in memory
and shapes the answer (ADR-0006). Errors are codes (ADR-0018):
``error.item_not_found``, ``error.no_pdf_for_item``, ``error.annotation_not_found``
and ``error.annotations.unreadable`` (409, e.g. a password-protected PDF, or
any failure of PyMuPDF on the file).

PyMuPDF is not thread-safe, and the SPA asks for several pictures at once:
every PyMuPDF call of this router runs under ``_pdf_lock``, one at a time.
Known limit: that lock covers this router only; import, OCR and ``pdf_text``
use PyMuPDF in parallel as they did before (no process-wide lock on purpose -
OCR under it would block pictures for minutes).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import weakref
from collections import OrderedDict

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

import pdf_annotations
from context import get_conn, safe_pdf_path

router = APIRouter()

# The in-memory cache: (file, size, mtime) -> Reading. A file saved again by the
# reader has a new size or mtime and is simply read anew; old keys age out.
_CACHE_SIZE = 8
_cache: "OrderedDict[tuple, pdf_annotations.Reading]" = OrderedDict()
_lock = threading.Lock()       # guards _cache, held only for a dict operation
_pdf_lock = threading.Lock()   # serialises every PyMuPDF call (read and render)
# Where requests wait for their turn: on the event loop, so a queue of pictures
# holds no worker thread of the shared pool. ``_pdf_lock`` stays the real guard.
_queues: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _queue() -> asyncio.Lock:
    """The waiting line of the running event loop (the app has one; a lock is
    bound to the loop it first waits on, so each loop gets its own)."""
    loop = asyncio.get_running_loop()
    lock = _queues.get(loop)
    if lock is None:
        lock = _queues[loop] = asyncio.Lock()
    return lock


def _pdf_path(paper_id: int) -> str:
    """The Item's PDF in the library folder, or the matching error code."""
    conn = get_conn()
    try:
        row = conn.execute("SELECT filename FROM papers WHERE id = ?", (paper_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="error.item_not_found")
    if not row["filename"]:
        raise HTTPException(status_code=404, detail="error.no_pdf_for_item")
    try:
        path = safe_pdf_path(row["filename"])
    except HTTPException:
        raise HTTPException(status_code=404, detail="error.no_pdf_for_item")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="error.no_pdf_for_item")
    return path


def _unreadable(path: str, exc: pdf_annotations.UnreadablePdf) -> HTTPException:
    """The 409 for a file PyMuPDF cannot read. A failure is logged; a password
    or a file that is no PDF is an expected state and is not."""
    if exc.__cause__ is not None:   # set only for a failure; no-PDF/password carry none
        logging.warning("Annotations: %s not readable: %s", os.path.basename(path), exc)
    return HTTPException(status_code=409, detail="error.annotations.unreadable")


def _cached(key: tuple):
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
        return hit


def _key(path: str) -> tuple:
    try:
        size, mtime_ns, _ = pdf_annotations.file_state(path)
    except OSError:
        raise HTTPException(status_code=404, detail="error.no_pdf_for_item")
    return (os.path.realpath(path), size, mtime_ns)


async def _reading_async(path: str) -> pdf_annotations.Reading:
    """A cache hit is answered at once; only PyMuPDF work waits in line, and it
    waits here on the event loop, not on a pool thread."""
    hit = _cached(_key(path))
    if hit is not None:
        return hit
    async with _queue():
        return await run_in_threadpool(_reading, path)


def _reading(path: str) -> pdf_annotations.Reading:
    key = _key(path)
    hit = _cached(key)
    if hit is not None:
        return hit
    with _pdf_lock:
        # A request that waited here may find the reading the one before it made.
        hit = _cached(key)
        if hit is not None:
            return hit
        try:
            reading = pdf_annotations.read(path)
        except pdf_annotations.UnreadablePdf as exc:
            raise _unreadable(path, exc)
        with _lock:
            _cache[key] = reading
            while len(_cache) > _CACHE_SIZE:
                _cache.popitem(last=False)
    return reading


def _image(path: str, page_index: int, rect) -> bytes:
    with _pdf_lock:
        try:
            return pdf_annotations.render_image(path, page_index, rect)
        except pdf_annotations.UnreadablePdf as exc:
            raise _unreadable(path, exc)


@router.get("/api/papers/{paper_id}/annotations")
async def list_annotations(paper_id: int):
    """What the reader marked in the Item's PDF, in reading order."""
    path = _pdf_path(paper_id)
    reading = await _reading_async(path)
    return reading.payload


@router.get("/api/papers/{paper_id}/annotations/{annotation_id}/image")
async def annotation_image(paper_id: int, annotation_id: str):
    """PNG of one annotation's area with a margin, its annotations drawn."""
    path = _pdf_path(paper_id)
    reading = await _reading_async(path)
    area = reading.areas.get(annotation_id)
    if area is None:
        raise HTTPException(status_code=404, detail="error.annotation_not_found")
    page_index, rect = area
    async with _queue():
        png = await run_in_threadpool(_image, path, page_index, rect)
    return Response(content=png, media_type="image/png")
