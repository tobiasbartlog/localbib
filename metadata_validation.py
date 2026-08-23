"""Metadata Validation for Papers.

Owns the policy that produces a :class:`Proposal` from a Paper + its PDF text:
checks the Paper's existing fields against the PDF content, runs CrossRef /
LLM / OpenAlex / arXiv lookups, and *corrects* fields where they disagree.

See ``CONTEXT.md`` for the domain definitions of **Metadata Validation**,
**Metadata Proposal**, and **Confidence**.

This module is intentionally side-effect-free: no DB writes, no filesystem
operations, no logging configuration. The caller passes ``paper`` (a dict
loaded from the DB) and ``pdf_text`` (already extracted); the caller is
responsible for persisting the accepted changes from the resulting Proposal.

The :class:`CrossRefStrategy`, :class:`LLMStrategy`, :class:`OpenAlexStrategy`,
and :class:`ArXivStrategy` classes remain exported for the time being because
``webapp.py`` still uses them directly from the live ``/validate`` endpoint.
Once that endpoint is rewritten to call :func:`propose`, the strategies will
become module-private.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from llm_client import LLMClient
from literature_manager import (
    categorize_with_llm,
    discover_doi,
    extract_arxiv_id_from_text,
    extract_isbn_from_text,
    extract_year_from_text,
    fetch_crossref_metadata,
    resolve_doi_from_arxiv,
    search_openalex_for_doi,
)

logger = logging.getLogger(__name__)

# Tracked Paper fields for the Metadata Proposal's diff surface.
_TRACKED_FIELDS: tuple[str, ...] = (
    "title", "authors", "year", "doi", "isbn", "abstract", "journal", "publisher",
)

# Stop-words that are dropped before comparing CrossRef titles to PDF text.
_TITLE_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "for", "and", "to", "with", "by",
    "from", "at", "as", "is",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm_words(s: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9\s]", " ", (s or "").lower()).split())


def crossref_title_matches_text(cr_title: str, text: str, threshold: float = 0.5) -> bool:
    """Return True if the CrossRef title's significant words appear in ``text``.

    This is the plausibility check that prevents accidentally overwriting a
    paper with metadata from a similarly-named but different work.
    """
    cr_words = _norm_words(cr_title) - _TITLE_STOPWORDS
    if not cr_words:
        return True
    pdf_words = _norm_words(text[:5000])
    overlap = len(cr_words & pdf_words) / len(cr_words)
    return overlap >= threshold


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

class Strategy(Protocol):
    def run(self, text: str, paper: dict[str, Any]) -> dict[str, Any]: ...


class CrossRefStrategy:
    """DOI discovery + CrossRef metadata lookup with PDF plausibility check.

    Pure: defers network calls to the injectable callables (defaulted to the
    module-level functions from ``literature_manager``) so tests can patch them
    without touching the network.
    """

    def __init__(
        self,
        discover: Callable[..., str | None] = discover_doi,
        fetch: Callable[[str], dict | None] = fetch_crossref_metadata,
        respect_manual_doi: bool = True,
    ) -> None:
        self._discover = discover
        self._fetch = fetch
        self._respect_manual_doi = respect_manual_doi
        # Last-run diagnostics, used by callers that need to know *why* a
        # particular field decision was made (e.g. validate_paper_metadata
        # wants to feed the LLM step with "crossref-matched-pdf" context).
        self.last_crossref_data: dict | None = None
        self.last_matches_pdf: bool = False
        self.last_doi: str | None = None

    def run(self, text: str, paper: dict[str, Any]) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        self.last_crossref_data = None
        self.last_matches_pdf = False
        self.last_doi = None

        doi_manually_verified = bool(paper.get("doi_manually_verified", 0))
        doi = self._discover(
            text,
            paper.get("title", "") or "",
            paper.get("authors", "") or "",
            paper.get("year"),
        )
        if not doi:
            return changes

        self.last_doi = doi
        cr = self._fetch(doi)
        if cr is None:
            # DOI was found but CrossRef has no metadata (Zenodo etc.) - still
            # worth recording.
            if not (self._respect_manual_doi and doi_manually_verified):
                changes["doi"] = doi
            return changes

        self.last_crossref_data = cr
        matches = crossref_title_matches_text(cr.get("title") or "", text)
        self.last_matches_pdf = matches

        if matches:
            if not (self._respect_manual_doi and doi_manually_verified):
                changes["doi"] = doi
            for field in ("title", "authors", "year", "abstract", "journal", "publisher", "isbn"):
                if cr.get(field):
                    changes[field] = cr[field]
        else:
            logger.warning(
                "CrossRef title %r does not match PDF content - ignoring CrossRef data",
                (cr.get("title") or "")[:60],
            )
            self.last_crossref_data = None
            if not (self._respect_manual_doi and doi_manually_verified):
                changes["doi"] = ""

        return changes


class LLMStrategy:
    """Ask the LLM to extract/verify Title/Authors/DOI/ISBN/Year from PDF text.

    The strategy holds the metadata-extraction prompt template. Callers may
    pass extra hints (e.g. filename) via the ``hints`` kwarg at construction
    time; these are appended to the prompt.
    """

    def __init__(
        self,
        client: LLMClient | None,
        hints: dict[str, str] | None = None,
        text_chars: int = 4000,
    ) -> None:
        self._client = client
        self._hints = hints or {}
        self._text_chars = text_chars

    def _build_prompt(self, text_snippet: str, current_title: str, current_authors: str) -> str:
        has_text = len(text_snippet.strip()) > 50
        if has_text:
            text_section = f"PDF-TEXT (erste Seiten):\n{text_snippet[:self._text_chars]}"
        else:
            text_section = "PDF-TEXT: Kein Text extrahierbar (gescanntes PDF ohne OCR)."

        filename = self._hints.get("filename", "")
        original_filename = self._hints.get("original_filename", "")
        filename_hint = ""
        if filename or original_filename:
            filename_hint = f"\nDATEINAME: {filename}"
            if original_filename and original_filename != filename:
                filename_hint += f"\nORIGINAL-DATEINAME: {original_filename}"

        return f"""Du bist ein Experte fuer wissenschaftliche Literatur.
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

    def extract(self, text_snippet: str, current_title: str, current_authors: str) -> dict[str, Any]:
        """Return the raw LLM JSON output (may include extra fields like arxiv_id)."""
        if self._client is None:
            return {}
        prompt = self._build_prompt(text_snippet, current_title, current_authors)
        try:
            content = self._client.complete([{"role": "user", "content": prompt}], timeout=60)
            return json.loads(content) if content else {}
        except Exception as e:
            logger.warning("LLM metadata extraction error: %s", e)
            return {}

    def run(self, text: str, paper: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            return {}
        current_title = paper.get("title", "") or ""
        current_authors = paper.get("authors", "") or ""
        llm_data = self.extract(text, current_title, current_authors)
        # Only carry forward fields the LLM filled in AND that are missing
        # from the paper. The complex precedence logic in
        # validate_paper_metadata still owns the merge decisions.
        out: dict[str, Any] = {}
        for f in ("title", "authors", "year", "doi", "isbn", "abstract"):
            if llm_data.get(f) and not paper.get(f):
                out[f] = llm_data[f]
        return out


class OpenAlexStrategy:
    """Try to fill a missing DOI via OpenAlex title search."""

    def __init__(self, search: Callable[..., str | None] = search_openalex_for_doi) -> None:
        self._search = search

    def run(self, text: str, paper: dict[str, Any]) -> dict[str, Any]:
        if paper.get("doi"):
            return {}
        title = paper.get("title", "") or ""
        if not title:
            return {}
        doi = self._search(title, paper.get("authors", "") or "", paper.get("year"))
        return {"doi": doi} if doi else {}


class ArXivStrategy:
    """Resolve an arXiv ID from the PDF text into a DataCite DOI."""

    def __init__(
        self,
        extract: Callable[[str], str | None] = extract_arxiv_id_from_text,
        resolve: Callable[[str], str | None] = resolve_doi_from_arxiv,
    ) -> None:
        self._extract = extract
        self._resolve = resolve

    def run(self, text: str, paper: dict[str, Any]) -> dict[str, Any]:
        if paper.get("doi"):
            return {}
        arxiv_id = self._extract(text or "")
        if not arxiv_id:
            return {}
        doi = self._resolve(arxiv_id)
        return {"doi": doi} if doi else {}


# ---------------------------------------------------------------------------
# Abstract helpers
# ---------------------------------------------------------------------------

def fetch_crossref_abstract(
    doi: str,
    fetch: Callable[[str], dict | None] = fetch_crossref_metadata,
) -> str:
    """Fetch the abstract for a known DOI via CrossRef (empty string on miss)."""
    if not doi:
        return ""
    try:
        cr = fetch(doi)
    except Exception as e:
        logger.warning("CrossRef abstract fetch failed for %s: %s", doi, e)
        return ""
    if not cr:
        return ""
    return (cr.get("abstract") or "").strip()


def llm_find_abstract_in_text(
    client: LLMClient,
    title: str,
    authors: str,
    text_snippet: str,
    timeout: int = 60,
) -> str:
    """Ask the LLM to locate an existing Abstract within a document.

    Returns the abstract text, or empty string if the LLM signals KEIN_ABSTRACT.
    """
    prompt = f"""Du bist ein Experte fuer wissenschaftliche Literatur.

Analysiere den folgenden Text eines wissenschaftlichen Dokuments und suche nach dem Abstract/der Zusammenfassung.
Ein Abstract steht typischerweise am Anfang des Dokuments, oft nach dem Titel und den Autoren.
Er ist haeufig mit "Abstract", "Summary", "Zusammenfassung" o.ae. ueberschrieben.

TITEL: {title or 'Unbekannt'}
AUTOREN: {authors or 'Unbekannt'}

DOKUMENT-TEXT:
{text_snippet}

Wenn du ein Abstract/eine Zusammenfassung im Text findest, gib es EXAKT so zurueck wie es im Dokument steht.
Wenn KEIN Abstract im Dokument vorhanden ist, antworte NUR mit dem Wort: KEIN_ABSTRACT

Antworte NUR mit dem Abstract-Text oder KEIN_ABSTRACT, ohne weitere Erklaerungen."""
    try:
        content = client.complete([{"role": "user", "content": prompt}], timeout=timeout)
    except Exception as e:
        logger.warning("LLM abstract search failed: %s", e)
        return ""
    if not content or "KEIN_ABSTRACT" in content.upper():
        return ""
    return content


def llm_generate_abstract(
    client: LLMClient,
    title: str,
    authors: str,
    text_snippet: str,
    timeout: int = 90,
) -> str:
    """Ask the LLM to *generate* a fresh abstract from the document text."""
    prompt = f"""Du bist ein Experte fuer wissenschaftliche Literatur.

Erstelle ein praegnantes Abstract (150-250 Woerter) fuer das folgende wissenschaftliche Dokument.
Das Abstract soll die Kernpunkte, Methodik und Ergebnisse zusammenfassen.
Schreibe das Abstract in der Sprache des Dokuments.

TITEL: {title or 'Unbekannt'}
AUTOREN: {authors or 'Unbekannt'}

DOKUMENT-TEXT:
{text_snippet}

Antworte NUR mit dem Abstract-Text, ohne Ueberschriften oder Erklaerungen."""
    try:
        return client.complete([{"role": "user", "content": prompt}], timeout=timeout) or ""
    except Exception as e:
        logger.warning("LLM abstract generation failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Metadata Proposal
# ---------------------------------------------------------------------------

@dataclass
class Proposal:
    """The output of Metadata Validation for one Paper.

    See ``CONTEXT.md`` for the domain definition. Consumed by the validate
    popup; only fields in ``changes`` are persisted (after optional user edit).
    """

    changes: dict[str, Any] = field(default_factory=dict)
    """Field -> proposed new value. Only fields whose new value differs from
    the Paper's current value appear here."""

    current: dict[str, Any] = field(default_factory=dict)
    """Field -> current value, restricted to the same keys as ``changes``.
    Used by the popup to render a before/after diff."""

    source_per_field: dict[str, str] = field(default_factory=dict)
    """Field -> provenance tag: one of ``crossref``, ``doi-discovery``, ``llm``,
    ``llm+crossref``, ``text``, ``rejected``."""

    category_suggestions: list[dict[str, Any]] = field(default_factory=list)
    """LLM-suggested Category assignments, each ``{category_id, confidence}``.
    Empty unless the caller provided both ``category_tree`` and
    ``categories_json`` and an ``llm_client``."""

    warnings: list[str] = field(default_factory=list)
    """Human-readable notes about decisions that the popup should surface
    (e.g. existing DOI rejected, LLM proposing to override a curated field)."""

    confidence: str = "high"
    """``"high"`` (safe to auto-apply in bulk Metadata Validation) or
    ``"low"`` (requires single-paper user review). See CONTEXT.md."""

    diagnostics: dict[str, Any] = field(default_factory=dict)
    """Raw upstream payloads (``crossref_data``, ``llm_data``) for debugging
    and for the existing ``_validate_crossref`` / ``_validate_llm`` response
    fields."""


def propose(
    paper: dict[str, Any],
    pdf_text: str,
    llm_client: LLMClient | None = None,
    hints: dict[str, str] | None = None,
    category_tree: str | None = None,
    categories_json: list[dict[str, Any]] | None = None,
) -> Proposal:
    """Compute a Metadata Proposal for one Paper.

    Pure (modulo injected network/LLM calls): no DB writes, no filesystem
    operations. The caller persists accepted changes from the returned
    :class:`Proposal`.

    Policy (matches the live ``/validate`` endpoint, which still uses its
    inline copy until step 3 of the refactor lands):

    1. **CrossRef** via :class:`CrossRefStrategy` — discovers DOI, fetches
       CrossRef metadata, plausibility-checks the CrossRef title against the
       PDF text. Mismatches discard the CrossRef payload and clear the DOI.
    2. **ISBN/year fallback** — extracted directly from the PDF text.
    3. **LLM extraction** — runs only when ``llm_client`` is provided.
       Overrides title when CrossRef did not match, or when the current title
       is empty / shorter than 5 chars / contains underscores. Overrides
       authors/year when CrossRef did not match or the field is empty.
    4. **LLM-DOI chain** — if the LLM proposes a DOI that the Paper lacks,
       re-fetch CrossRef with that DOI and fill any still-missing fields.
    5. **Existing-DOI plausibility check** — if the Paper's existing DOI was
       not modified and is not manually verified, re-fetch CrossRef for it and
       clear the DOI if its title does not match the PDF.
    6. **Category suggestions** — when ``category_tree`` and
       ``categories_json`` are both provided and ``llm_client`` is available.

    Confidence is ``"low"`` if any of: CrossRef title mismatched the PDF, the
    LLM proposes overriding an existing curated field, or no DOI was resolved.
    Otherwise ``"high"``.

    Network/LLM callables are resolved via this module's globals so that tests
    can monkeypatch ``metadata_validation.discover_doi`` etc. without touching
    the network.
    """
    proposal = Proposal()
    doi_manually_verified = bool(paper.get("doi_manually_verified", 0))

    # --- 1. CrossRef + DOI discovery --------------------------------------
    cr = CrossRefStrategy(
        discover=globals()["discover_doi"],
        fetch=globals()["fetch_crossref_metadata"],
    )
    cr_changes = cr.run(pdf_text, paper)
    crossref_data = cr.last_crossref_data
    crossref_matches_pdf = cr.last_matches_pdf
    cr_source = "crossref" if crossref_matches_pdf else "doi-discovery"
    for k, v in cr_changes.items():
        proposal.changes[k] = v
        proposal.source_per_field[k] = cr_source
    # Warning when CrossRef came back but didn't match the PDF (DOI was
    # blanked inside the strategy).
    if cr.last_doi and not crossref_matches_pdf and cr_changes.get("doi") == "":
        proposal.warnings.append(
            "CrossRef title for discovered DOI did not match PDF — DOI cleared"
        )

    # --- 2. ISBN fallback from PDF text -----------------------------------
    if not proposal.changes.get("isbn"):
        isbn = globals()["extract_isbn_from_text"](pdf_text)
        if isbn:
            proposal.changes["isbn"] = isbn
            proposal.source_per_field["isbn"] = "text"

    # --- 3. Year fallback from PDF text -----------------------------------
    if not proposal.changes.get("year"):
        year = globals()["extract_year_from_text"](pdf_text)
        if year:
            proposal.changes["year"] = year
            proposal.source_per_field["year"] = "text"

    # --- 4. LLM extraction + override policy ------------------------------
    llm_data: dict[str, Any] = {}
    if llm_client is not None:
        current_title = proposal.changes.get("title", paper.get("title") or "")
        current_authors = proposal.changes.get("authors", paper.get("authors") or "")
        llm_strat = LLMStrategy(llm_client, hints=hints)
        llm_data = llm_strat.extract(pdf_text, current_title, current_authors)
        if llm_data:
            current_t = proposal.changes.get("title", "") or ""
            # Title override
            if llm_data.get("title") and (
                not crossref_matches_pdf
                or not current_t
                or len(current_t) < 5
                or "_" in current_t
            ):
                if paper.get("title") and paper["title"] != llm_data["title"]:
                    proposal.warnings.append(
                        f"LLM proposes overriding existing title {paper['title']!r}"
                    )
                proposal.changes["title"] = llm_data["title"]
                proposal.source_per_field["title"] = "llm"
            # Authors override
            if llm_data.get("authors") and (
                not proposal.changes.get("authors") or not crossref_matches_pdf
            ):
                if paper.get("authors") and paper["authors"] != llm_data["authors"]:
                    proposal.warnings.append(
                        "LLM proposes overriding existing authors"
                    )
                proposal.changes["authors"] = llm_data["authors"]
                proposal.source_per_field["authors"] = "llm"
            # Year override
            if llm_data.get("year") and (
                not proposal.changes.get("year") or not crossref_matches_pdf
            ):
                proposal.changes["year"] = llm_data["year"]
                proposal.source_per_field["year"] = "llm"
            # DOI from LLM → re-fetch CrossRef and fill any still-missing fields
            if (
                llm_data.get("doi")
                and not proposal.changes.get("doi")
                and not doi_manually_verified
            ):
                proposal.changes["doi"] = llm_data["doi"]
                proposal.source_per_field["doi"] = "llm"
                cr_again = globals()["fetch_crossref_metadata"](llm_data["doi"])
                if cr_again:
                    for fld in (
                        "title", "authors", "year", "abstract",
                        "journal", "publisher", "isbn",
                    ):
                        if cr_again.get(fld) and not proposal.changes.get(fld):
                            proposal.changes[fld] = cr_again[fld]
                            proposal.source_per_field[fld] = "llm+crossref"
            # ISBN from LLM
            if llm_data.get("isbn") and not proposal.changes.get("isbn"):
                proposal.changes["isbn"] = llm_data["isbn"]
                proposal.source_per_field["isbn"] = "llm"

    # --- 5. Existing-DOI plausibility post-check --------------------------
    existing_doi = paper.get("doi") or ""
    if existing_doi and "doi" not in proposal.changes and not doi_manually_verified:
        existing_cr = globals()["fetch_crossref_metadata"](existing_doi)
        if existing_cr and not crossref_title_matches_text(
            existing_cr.get("title") or "", pdf_text
        ):
            proposal.changes["doi"] = ""
            proposal.source_per_field["doi"] = "rejected"
            proposal.warnings.append(
                f"Existing DOI {existing_doi} did not match PDF — cleared"
            )

    # --- 6. Category suggestions ------------------------------------------
    if llm_client is not None and category_tree and categories_json:
        title_for_cat = proposal.changes.get("title", paper.get("title") or "")
        abstract_for_cat = proposal.changes.get("abstract", paper.get("abstract") or "")
        try:
            suggestions = globals()["categorize_with_llm"](
                title=title_for_cat,
                abstract=abstract_for_cat,
                text_snippet=(pdf_text or "")[:2000],
                category_tree=category_tree,
                categories_json=categories_json,
            )
            proposal.category_suggestions = list(suggestions or [])
        except Exception as e:
            logger.warning("Category suggestion failed: %s", e)

    # --- 7. Confidence gating ---------------------------------------------
    low = False
    # CrossRef came back but didn't match the PDF
    if cr.last_doi and crossref_data is None and cr_changes.get("doi") == "":
        low = True
    # LLM proposed overriding a curated field
    if any(w.startswith("LLM proposes overriding") for w in proposal.warnings):
        low = True
    # No DOI was resolved anywhere
    final_doi = proposal.changes.get("doi", paper.get("doi") or "")
    if not final_doi:
        low = True
    proposal.confidence = "low" if low else "high"

    # --- 8. Diagnostics & diff trimming -----------------------------------
    proposal.diagnostics = {
        "crossref_data": crossref_data,
        "llm_data": llm_data,
    }
    # Drop changes that don't actually differ from the Paper's current value.
    proposal.changes = {
        k: v for k, v in proposal.changes.items() if v != paper.get(k)
    }
    proposal.source_per_field = {
        k: src for k, src in proposal.source_per_field.items()
        if k in proposal.changes
    }
    proposal.current = {k: paper.get(k) for k in proposal.changes.keys()}

    return proposal
