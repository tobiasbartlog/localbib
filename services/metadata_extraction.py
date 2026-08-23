"""Service: LLM-based metadata extraction helpers.

Pure functions — no DB writes, no logging config, no FS ops. Callers are
responsible for constructing the ``LLMClient`` and persisting any results.

Extracted from ``webapp.py`` (``_llm_extract_metadata``) and
``routers/categories.py`` (``_llm_suggest_category``) as part of
Backend-Modularisierung #86.

Usage::

    from llm_client import llm_for
    from services.metadata_extraction import llm_extract_metadata, llm_suggest_category
    llm = llm_for("metadata_extract")
    data = llm_extract_metadata(llm, pdf_text, current_title, current_authors)
    suggestion = llm_suggest_category(llm, category_name, existing_categories)
"""

from __future__ import annotations


def llm_extract_metadata(
    llm_client,
    text_snippet: str,
    current_title: str,
    current_authors: str,
    filename: str = "",
    original_filename: str = "",
) -> dict:
    """Extract / verify title, authors, DOI, year, ISBN via LLM.

    Parameters
    ----------
    llm_client:
        A configured :class:`~llm_client.LLMClient` instance (or ``None``).
        Returns ``{}`` immediately when ``None``.
    text_snippet:
        Raw PDF text (first pages). May be empty for scanned PDFs.
    current_title / current_authors:
        Current metadata values — passed to the LLM so it can decide whether
        to override them.
    filename / original_filename:
        PDF file names used as additional context hints.

    Returns
    -------
    dict
        Parsed JSON payload from the LLM containing whichever metadata fields
        the model was confident about.  Empty dict on any error or missing key.
    """
    if llm_client is None:
        return {}

    has_text = len(text_snippet.strip()) > 50
    if has_text:
        text_section = f"PDF-TEXT (erste Seiten):\n{text_snippet[:4000]}"
    else:
        text_section = "PDF-TEXT: Kein Text extrahierbar (gescanntes PDF ohne OCR)."

    filename_hint = ""
    if filename or original_filename:
        filename_hint = f"\nDATEINAME: {filename}"
        if original_filename and original_filename != filename:
            filename_hint += f"\nORIGINAL-DATEINAME: {original_filename}"

    prompt = f"""Du bist ein Experte fuer wissenschaftliche Literatur.
Analysiere die folgenden Informationen und extrahiere/recherchiere die korrekten Metadaten.

Suche insbesondere nach:
- DOI (z.B. 10.xxxx/...)
- ArXiv-ID (z.B. arXiv:xxxx.xxxxx)
- ISBN (z.B. 978-x-xxx-xxxxx-x)
- Titel des Werks
- Autoren oder Herausgeber
- Erscheinungsjahr

{text_section}
{filename_hint}

AKTUELLE METADATEN (moeglicherweise falsch oder unvollstaendig):
Titel: {current_title}
Autoren: {current_authors}

WICHTIG:
- Falls der PDF-Text leer ist, nutze den Dateinamen und dein Wissen ueber das Werk,
um moeglichst korrekte Metadaten zu bestimmen. Bei Normen/Standards gib den Herausgeber als Autor an.
- Der Titel muss IMMER als lesbarer Text zurueckgegeben werden, NIEMALS mit Unterstrichen.
  Beispiel: FALSCH "SIA_D0111_Lehmbauregeln" -> RICHTIG "Lehmbauregeln" oder "Regeln zum Bauen mit Lehm"
  Unterstriche im aktuellen Titel deuten darauf hin, dass der Titel aus dem Dateinamen stammt und korrigiert werden muss.

Gib die korrekten Metadaten als JSON zurueck. Nur die Felder die du sicher bestimmen kannst.
Format (NUR JSON, kein anderer Text):
{{"title": "Korrekter Titel", "authors": "Nachname1, Vorname1; Nachname2, Vorname2", "year": 2024, "doi": "10.xxxx/...", "isbn": "978...", "arxiv_id": "xxxx.xxxxx"}}

Wenn der aktuelle Titel korrekt ist, gib ihn unveraendert zurueck.
Antworte NUR mit dem JSON-Objekt."""

    return llm_client.complete_json(
        [{"role": "user", "content": prompt}], expect=dict, default={}, timeout=60
    )


def llm_suggest_category(
    llm_client,
    name: str,
    existing_categories: list,
) -> dict:
    """Suggest keywords and a description for a new category via LLM.

    Parameters
    ----------
    llm_client:
        A configured :class:`~llm_client.LLMClient` instance (or ``None``).
        Returns ``{}`` immediately when ``None``.
    name:
        The proposed category name.
    existing_categories:
        List of existing category dicts (``name``, ``description``,
        ``keywords``) used as context so the LLM avoids overlapping categories.

    Returns
    -------
    dict
        ``{"description": "...", "keywords": "..."}`` or ``{}`` on any error.
    """
    if llm_client is None:
        return {}

    existing_info = "\n".join(
        f"- {c.get('name', '')}: {c.get('description', '')} [Keywords: {c.get('keywords', '')}]"
        for c in existing_categories
    )

    prompt = f"""Du bist ein Experte fuer wissenschaftliche Literaturklassifikation.

Generiere sinnvolle Keywords und eine Beschreibung fuer die folgende Kategorie.
Die Kategorie soll in einem System fuer wissenschaftliche Literatur (Dissertationsforschung) verwendet werden.

KATEGORIENAME: {name}

EXISTIERENDE KATEGORIEN (zur Orientierung):
{existing_info}

Antworte NUR mit JSON, kein anderer Text:
{{"description": "Kurze Beschreibung (1-2 Saetze)", "keywords": "keyword1, keyword2, keyword3, keyword4, keyword5"}}"""

    return llm_client.complete_json(
        [{"role": "user", "content": prompt}], expect=dict, default={}, timeout=60
    )
