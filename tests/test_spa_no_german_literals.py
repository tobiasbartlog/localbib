"""Guard: no German UI literal and no locale-less ``toLocale*()`` in app.js.

The UI is English-sourced and German-translated (ADR-0018): every visible
string is a catalog key resolved by ``t()``. Since the architecture review
(SPA-8, SPA-9) found about fifty German literals that had slipped through,
this test scans ``static/app.js`` - string literals, and the visible text of
the Vue templates - for umlauts and common German words, and every
``toLocaleString/DateString/TimeString`` call for a locale argument.

Comments are skipped: code comments may stay German. A legitimate German
literal goes into ``ALLOWED`` with the reason.
"""
from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "static" / "app.js"

#: Literals that must stay as they are, with the reason.
ALLOWED = {
    # Stored data, not UI text: the import source prefix older versions wrote
    # into original_filename, matched to label the item's origin.
    "BibTeX-Import:",
    "RIS-Import:",
}
#: Substrings that may appear anywhere (protocol, not text).
ALLOWED_PARTS = (
    "[Quelle",  # the research-chat source marker the SPA parses (ADR-0018)
)

_WORDS = (
    r"Paper\s+(oeffnen|öffnen|aktualisiert|verarbeitet|werden)|Unbekannt|Starte|fehlgeschlagen|"
    r"Behalten|geloescht|gelöscht|oeffnen|Fehler|Keine|keine|nicht|wird|wurde|werden|Datei|"
    r"Eintr(ae|ä)ge|bitte|Bitte|loeschen|Löschen|Speichern|Abbrechen|Wirklich|wirklich|Seite|"
    r"Seiten|Sekunden|erfolgreich|gespeichert|Kategorie|Sammlung|Fertig|Lade|Laden|Suche|"
    r"ausw(ae|ä)hlen|hinzuf(ue|ü)gen|entfernen|Verarbeitung|Aktualisierung|und|oder|f(ue|ü)r|"
    r"mit|von|bei|zur|zum|der|das|ein|eine|gepr(ue|ü)ft|(ue|ü)bernommen|nachgeladen|"
    r"abgeschlossen|Pr(ue|ü)fung|Men(ue|ü)|ausf(ue|ü)hren|Eigene|Eigenes|Niedrig|Alle|alle|"
    r"Quelle|Quellen|Ordner|Schlie(ss|ß)en|Zur(ue|ü)ck|Weiter|Hinweis|Achtung|Warnung|gefunden|"
    r"vorhanden|bereits|noch|Noch|jetzt|Jetzt|hier|Hier|sind|ist|kann|konnte|Lege|Ziehe|"
    r"Referenz|Referenzen|Unterkategorie|Unterkategorien|Ebene|Hauptkategorie|Konfidenz|"
    r"Validierung|Assistent|Du|Suchergebnisse|unver(ae|ä)ndert|ebenfalls|Bereich|Importiere|"
    r"Generiere|Verarbeite"
)
GERMAN = re.compile(r"[äöüÄÖÜß]|\b(" + _WORDS + r")\b")
PAPER = re.compile(r"\bPaper\b")  # the UI entity is Item / Eintrag (CONTEXT.md)
LOCALELESS = re.compile(r"\.toLocale(String|DateString|TimeString)\(\s*\)")


def _strip(src: str):
    """``(kind, text, line)`` for every string literal and template line,
    with comments (``//``, ``/* */``, ``<!-- -->``) left out."""
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "'\"":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            yield "str", src[i + 1:j], src.count("\n", 0, i) + 1
            i = j + 1
            continue
        if c == "`":
            j = i + 1
            while j < n and src[j] != "`":
                j += 2 if src[j] == "\\" else 1
            body = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), src[i + 1:j], flags=re.S)
            base = src.count("\n", 0, i) + 1
            for k, line in enumerate(body.split("\n")):
                yield "tpl", line, base + k
            i = j + 1
            continue
        if c == "/" and re.search(r"[=(,:;!&|?{}\[\]]\s*$", src[max(0, i - 20):i]):
            j = i + 1  # a regex literal: skip it, its quotes are not strings
            while j < n and src[j] not in "/\n":
                j += 2 if src[j] == "\\" else 1
            i = j + 1
            continue
        i += 1


def _allowed(text: str) -> bool:
    return text.strip() in ALLOWED or any(p in text for p in ALLOWED_PARTS)


def _findings() -> list[str]:
    out = []
    for kind, text, line in _strip(APP.read_text(encoding="utf-8")):
        if kind == "str":
            candidates = [text]
        else:
            visible = re.sub(r"<[^>]*>", " ", re.sub(r"\{\{.*?\}\}", " ", text))
            candidates = [visible] + re.findall(r"'([^'\n]*)'", text)
        for cand in candidates:
            if not _allowed(cand) and (GERMAN.search(cand) or PAPER.search(cand)):
                out.append(f"app.js:{line}: {cand.strip()[:100]!r}")
    return out


def test_no_german_or_paper_literal_in_the_spa():
    findings = _findings()
    assert findings == [], "translate via t() (both catalogs) or allow-list with a reason:\n" + "\n".join(findings)


def test_every_to_locale_call_names_its_locale():
    lines = [f"app.js:{i}: {line.strip()[:100]}"
             for i, line in enumerate(APP.read_text(encoding="utf-8").split("\n"), 1)
             if LOCALELESS.search(line)]
    assert lines == [], "pass uiLocale() / $locale():\n" + "\n".join(lines)


def test_the_guard_bites():
    """The scanner itself finds what it must (and skips comments)."""
    sample = (
        "// Kommentar darf deutsch sein\n"
        "alert('Update fehlgeschlagen.');\n"
        "const tpl = `<p>Noch keine Referenzen</p>{{ x ? 'Behalten' : '' }}`;\n"
        "n.toLocaleString();\n"
    )
    found = [t for k, t, _ in _strip(sample)]
    assert "Kommentar darf deutsch sein" not in " ".join(found)
    assert any(GERMAN.search(t) for t in found if "fehlgeschlagen" in t)
    assert LOCALELESS.search(sample)
