"""Router: appearance / custom icon.

Serves POST /api/appearance/icon, DELETE /api/appearance/icon and
GET /api/appearance/icon.  Writes to the static/ directory next to the
project root (or sys._MEIPASS in a frozen build).  No I/O at import time.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

router = APIRouter()

# Derive the static/ directory the same way webapp.py derives SCRIPT_DIR:
# in a frozen build use sys._MEIPASS, otherwise the parent of routers/.
_FROZEN = getattr(sys, "frozen", False)
if _FROZEN:
    _STATIC_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "static"
else:
    _STATIC_DIR = Path(__file__).parent.parent / "static"


@router.post("/api/appearance/icon")
async def upload_icon(file: UploadFile = File(...)):
    """Laedt ein benutzerdefiniertes Icon hoch (fuer Taskleiste und Menue)."""
    allowed = {".png", ".ico", ".svg", ".jpg", ".jpeg", ".webp"}
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Dateityp {ext} nicht erlaubt. Erlaubt: {', '.join(allowed)}",
        )

    content = await file.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Datei zu gross (max. 2 MB)")

    for old in _STATIC_DIR.glob("custom_icon.*"):
        old.unlink()

    icon_path = _STATIC_DIR / f"custom_icon{ext}"
    icon_path.write_bytes(content)
    return {"status": "ok", "path": f"/static/custom_icon{ext}"}


@router.delete("/api/appearance/icon")
async def delete_icon():
    """Loescht das benutzerdefinierte Icon."""
    for old in _STATIC_DIR.glob("custom_icon.*"):
        old.unlink()
    return {"status": "ok"}


@router.get("/api/appearance/icon")
async def get_icon():
    """Gibt den Pfad zum benutzerdefinierten Icon zurueck."""
    for f in _STATIC_DIR.glob("custom_icon.*"):
        return {"path": f"/static/{f.name}"}
    return {"path": None}
