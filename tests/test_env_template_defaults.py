"""SET-B11: ``.env.template`` must not promise a value the app does not take.

It said ``MAX_OCR_PAGES=math.inf`` while ``Config`` reads an integer and
falls back to 5. Every numeric key in the template must parse as what
``Config.reload_from_env`` would keep.
"""
from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

from config import Config

TEMPLATE = Path(__file__).resolve().parent.parent / ".env.template"


def test_the_template_values_are_what_config_reads(monkeypatch):
    values = dotenv_values(TEMPLATE, interpolate=False)
    for key in ("MAX_OCR_PAGES", "WATCH_INTERVAL"):
        raw = values.get(key)
        assert raw is not None, key
        assert raw.isdigit() and int(raw) >= 1, f"{key}={raw!r} is not an integer >= 1"
        monkeypatch.setenv(key, raw)
    Config.reload_from_env()
    try:
        assert Config.MAX_OCR_PAGES == int(values["MAX_OCR_PAGES"])
        assert Config.WATCH_INTERVAL == int(values["WATCH_INTERVAL"])
    finally:
        monkeypatch.undo()
        Config.reload_from_env()
