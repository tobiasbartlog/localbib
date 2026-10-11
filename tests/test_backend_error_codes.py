"""LIB-B8: the backend sends codes, never German sentences.

``translateDetail`` in the SPA passes a non-catalog string through as is, so
a sentence from a router reached the English UI. These sites now raise or
send codes from the catalogs.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

import context
from literature_manager import Config

REPO = Path(__file__).resolve().parent.parent
GERMAN = re.compile(r"[äöüÄÖÜß]|\b(nicht|keine?|gefunden|fehlgeschlagen|wird|werden|bereits|Datei|"
                    r"Paper\s+\w+|Referenzen|Literaturverzeichnis|extrahiert|Seiten|Zeichen|Suche)\b")
SITES = ["context.py", "paper_ingest.py", "validation_policy.py", "routers/references.py",
         "routers/validate.py", "routers/import.py", "services/smart_import_pipeline.py",
         "routers/annotations.py"]


def _messages(path: Path):
    """String constants handed to ``detail=`` or a ``"message"`` key."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "detail":
            yield node.lineno, node.value
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == "message":
                    yield node.lineno, v


def _texts(value):
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        yield value.value
    elif isinstance(value, ast.JoinedStr):
        yield "".join(v.value for v in value.values if isinstance(v, ast.Constant))


@pytest.mark.parametrize("site", SITES)
def test_no_german_sentence_is_sent(site):
    offenders = [f"{site}:{line} {text!r}"
                 for line, value in _messages(REPO / site)
                 for text in _texts(value) if GERMAN.search(text)]
    assert offenders == []


def test_safe_pdf_path_raises_codes(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "ALL_DIR", str(tmp_path))
    with pytest.raises(HTTPException) as exc:
        context.safe_pdf_path("missing.pdf")
    assert exc.value.detail == "error.pdf_not_on_disk"


def test_every_code_sent_is_in_both_catalogs():
    codes = set()
    for site in SITES:
        for _line, value in _messages(REPO / site):
            for text in _texts(value):
                if re.fullmatch(r"[a-z]+(\.[A-Za-z_]+)+", text):
                    codes.add(text)
            if isinstance(value, ast.Dict):
                for k, v in zip(value.keys, value.values):
                    if isinstance(k, ast.Constant) and k.value == "code" and isinstance(v, ast.Constant):
                        codes.add(v.value)
    catalogs = [(REPO / "static" / "locales" / f"core.{lang}.js").read_text(encoding="utf-8")
                for lang in ("en", "de")]
    missing = sorted(c for c in codes for cat in catalogs if f'"{c}":' not in cat)
    assert missing == []
