"""Host-service adapters for plugins (Phase 4: api.llm / api.library).

Neutral module (Backend-Modularisierung #93): it MAY import core modules
(``literature_manager``, ``llm_client``, ``bibtex_builder``, ``context``) because
it is the concrete bridge between the plugin contract (``plugin_api``) and the
core's LLM client + library. It is NOT a router and NOT a service; it has no
HTTP routes. ``webapp.py`` imports ``_CoreLlmApi`` / ``_CoreLibraryApi`` from here
and registers them on the shared ``registry`` (P3: plugins reach LLM & library
ONLY via these adapters — never by direct import of ``llm_client``/``Database``).

Side-effect note: constructing the module-level ``Database`` mirrors the pattern
the domain routers already use; it runs after ``Config.init_paths()`` because
``webapp.py`` imports this module only after path setup.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import bibtex_builder
import embedding_index
import library_events
import paper_ingest
from cite_key_generator import base_key as _legacy_cite_key_base
from context import get_conn
from filenames import stored_pdf_path
from literature_manager import Config, Database, extract_text_from_pdf
from llm_client import embed_texts, llm_for
from services import collection_export
from services import library_collections as collections_svc

# Aliases / shared resources so the relocated adapter bodies keep working
# verbatim (they used these private names in webapp.py).
_get_conn = get_conn
db = Database(Config.DB_PATH)


class _CoreLlmApi:
    """plugin_api.LlmApi: zentraler LLM-Dienst des Kerns (Keys/Limits im Kern)."""

    def complete(self, request: dict) -> dict:
        # tier="fast" ist die Absichtserklaerung des Plugins ("kleiner,
        # schematischer Task"), NICHT eine Modellwahl: aufgeloest wird sie hier
        # ueber TASK_MODELS, wie jede andere Call-Site auch (Model Routing).
        # Alles andere bleibt auf "plugin" (reasoning); ein explizites `model`
        # uebersteuert weiterhin beides.
        task = "plugin_fast" if str(request.get("tier") or "") == "fast" else "plugin"
        llm = llm_for(task, model=str(request.get("model") or "").strip() or None)
        content = llm.complete(
            request["messages"],
            timeout=int(request.get("timeout", 60)),
            temperature=request.get("temperature"),
        )
        return {"content": content}

    def embed(self, texts: list) -> list:
        # Chunking/Retry/Validierung leben zentral in llm_client.embed_texts()
        # (Kern-Slice H0, Issue #97); dies bleibt
        # ein Drei-Zeilen-Delegat. mode="document": Plugins vergleichen Texte
        # symmetrisch (Idee <-> Paper), kein Query-Instruktions-Prefix noetig.
        return embed_texts(texts, mode="document")

    def embed_model(self) -> str:
        # Name des konfigurierten Embedding-Modells, damit Plugins ihren
        # Vektor-Cache modell-scharf schluesseln koennen (Modellwechsel
        # ⇒ Re-Embed). Leer, wenn kein Endpoint konfiguriert ist.
        return Config.embed_model()


def _cite_key(paper: dict) -> str:
    """Gespeicherter Cite Key; Legacy-Fallback fuer Zeilen vor dem Backfill.

    Used by _CoreLibraryApi, the papers-list cite_keys filter, and the
    per-paper /bibtex endpoint. Export router has its own copy so it can
    remain webapp-import-free (import-linter constraint).
    """
    return paper.get("cite_key") or _legacy_cite_key_base(
        paper.get("authors") or "", paper.get("year")
    )



def _has_pdf_file(paper: dict) -> bool:
    """The Item's PDF is in the library folder (contract 2.3, ``has_pdf``)."""
    return stored_pdf_path(paper.get("filename")) is not None


def _reference_details(paper: dict) -> dict:
    """``volume``/``issue``/``pages`` (contract 2.3) from what the Item's
    stored metadata knows — a BibTeX import keeps them in ``raw_metadata``.
    The library has no columns for them; unknown is ``None``, never a guess."""
    try:
        raw = json.loads(paper.get("raw_metadata") or "{}")
    except (TypeError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    def pick(*keys):
        for key in keys:
            value = raw.get(key)
            if value not in (None, "", [], {}):
                return str(value).strip() or None
        return None

    return {
        "volume": pick("volume"),
        "issue": pick("issue", "number"),
        "pages": pick("pages", "page"),
    }


# ``idx_papers_cite_key`` is partial (``WHERE cite_key != ''``): SQLite uses it
# only when the query repeats that condition — without it, a full scan.
_CITE_KEY_LOOKUP = "SELECT * FROM papers WHERE cite_key = ? AND cite_key != ''"
# Rows without a stored key, counted on covering indexes (no row is read).
_KEYLESS_ROWS = (
    "SELECT (SELECT COUNT(*) FROM papers) - (SELECT COUNT(*) FROM papers WHERE cite_key != '')"
)


class _CoreLibraryApi:
    """plugin_api.LibraryApi: Lesezugriff auf die Bibliothek per Citekey,
    plus die eine Schreibmethode ``create_by_doi`` (``library.write``).

    Citekeys sind gespeicherte, eindeutige Paper-Eigenschaften (Spalte
    cite_key, backfilled in Database.init_db); _cite_key() faellt nur fuer
    Alt-Zeilen ohne Key auf das Legacy-Schema zurueck. Die fruehere
    Kollisionsluecke (on-the-fly-Keys) ist damit geschlossen.
    """

    @staticmethod
    def _wire(paper: dict) -> dict:
        details = _reference_details(paper)
        return {
            "citekey": _cite_key(paper),
            "id": paper.get("id"),
            "title": paper.get("title"),
            "authors": paper.get("authors"),
            "year": paper.get("year"),
            "journal": paper.get("journal"),
            "doi": paper.get("doi"),
            "abstract": paper.get("abstract"),
            # Contract 2.3 (additive): what a literature check needs.
            "has_pdf": _has_pdf_file(paper),
            **details,
        }

    def _find(self, citekey: str) -> Optional[dict]:
        """The Item with this Cite Key: one search on the unique
        ``idx_papers_cite_key``, never a full table load — an Add-on's
        literature check asks once per key. Rows without a stored key (from
        before the backfill) still match on their legacy key; whether there
        are any is asked of two covering indexes first, so a miss in a fully
        keyed library reads no row."""
        if not citekey:
            return None
        conn = _get_conn()
        try:
            row = conn.execute(_CITE_KEY_LOOKUP, (citekey,)).fetchone()
            if row is not None:
                return dict(row)
            if not conn.execute(_KEYLESS_ROWS).fetchone()[0]:
                return None
            for row in conn.execute(
                "SELECT * FROM papers WHERE cite_key IS NULL OR cite_key = '' "
                "ORDER BY year DESC, authors"
            ):
                paper = dict(row)
                if _cite_key(paper) == citekey:
                    return paper
            return None
        finally:
            conn.close()

    def get_reference(self, citekey: str) -> Optional[dict]:
        paper = self._find(citekey)
        return self._wire(paper) if paper else None

    def search_references(self, query: str, limit: int = 20) -> list:
        # Issue #104 (docs/PRD-semantische-suche.md Phase 4): semantic
        # ranking is additive -- hosts/plugins predating embeddings, or a
        # library with no embedding model configured, keep getting the
        # original lexical (db.search_papers) behaviour unchanged.
        semantic_ids = embedding_index.semantic_paper_ids(query, top_k=limit)
        if semantic_ids:
            by_id = {p["id"]: p for p in db.get_all_papers()}
            papers = [by_id[pid] for pid in semantic_ids if pid in by_id]
            if papers:
                return [self._wire(p) for p in papers]
        return [self._wire(p) for p in db.search_papers(query)[:limit]]

    def get_abstract(self, citekey: str) -> Optional[str]:
        paper = self._find(citekey)
        return (paper or {}).get("abstract") or None

    def get_full_text(self, citekey: str) -> Optional[str]:
        # Darf None liefern (discovery A5) — Plugins muessen auf Abstract degradieren.
        paper = self._find(citekey)
        filepath = stored_pdf_path((paper or {}).get("filename"))
        if filepath is None:
            return None
        try:
            return extract_text_from_pdf(filepath, max_pages=Config.MAX_OCR_PAGES) or None
        except Exception as exc:
            logging.warning("get_full_text(%s) fehlgeschlagen: %s", citekey, exc)
            return None

    def export_bibtex(self) -> str:
        # Gesamte Bibliothek als .bib (gespeicherte Cite Keys) — fuettert die
        # Manuskript-Vorschau des LaTeX-Editors.
        papers = db.get_all_papers()
        return "\n\n".join(
            bibtex_builder.format_entry(p, _cite_key(p)) for p in papers
        ) + "\n"

    def get_core_projects(self) -> list:
        # Kern-Projekte (Paper-Gruppierungen, werden abgeloest — ADR-0004) samt
        # Cite Keys der zugeordneten Paper, nur lesend. Fuettert die
        # plugin-seitige Startup-Migration zu Forschungsprojekten (Issue #51).
        conn = _get_conn()
        try:
            projects = []
            for row in conn.execute(
                "SELECT id, name, description FROM projects ORDER BY name"
            ).fetchall():
                project = dict(row)
                refs: list = []
                for paper_row in conn.execute(
                    "SELECT pa.* FROM paper_projects pp "
                    "JOIN papers pa ON pa.id = pp.paper_id "
                    "WHERE pp.project_id = ? ORDER BY pa.id",
                    (project["id"],),
                ).fetchall():
                    key = _cite_key(dict(paper_row))
                    if key and key not in refs:
                        refs.append(key)
                project["refs"] = refs
                projects.append(project)
            return projects
        finally:
            conn.close()

    def create_by_doi(
        self,
        doi: str,
        *,
        title: str = "",
        authors=None,
        year: Optional[int] = None,
        journal: str = "",
        abstract: str = "",
        source: str = paper_ingest.DOI_SOURCE,
    ) -> dict:
        """The DOI intake (ADR-0016) for an Add-on's Python.

        Why a host adapter may write here: ADR-0006 puts the write point at the
        transport boundary — for HTTP that is the router handler, for an
        in-process Add-on it is this adapter, the one place the contract call
        enters the core (as ``plugin_loader.AddonSettings.set`` is for
        ``plugins.json``). It decides nothing itself: it calls the very flow the
        REST handler calls, ``paper_ingest.intake_by_doi``, so there is no
        second ingest. The gate is not here either — ``plugin_loader`` wraps
        this object per Add-on, refuses the call without ``library.write`` and
        pins ``source`` to the Add-on id.
        """
        result = paper_ingest.intake_by_doi([{
            "doi": doi, "title": title, "authors": authors, "year": year,
            "journal": journal, "abstract": abstract, "source": source,
        }])[0]
        error = result.pop("error", None)
        if error:
            raise ValueError(error)
        return result

    # --- Collections (#204): read-only, under library.read -----------------
    # Unlike every user-facing read, these include tombstones: an Add-on that
    # was off when a Collection was deleted still finds its frozen membership.

    @staticmethod
    def _collection_wire(summary: dict) -> dict:
        return {key: summary[key] for key in COLLECTION_FIELDS}

    def list_collections(self) -> list:
        conn = _get_conn()
        try:
            rows = collections_svc.list_collections(
                conn, archived=collections_svc.ARCHIVED_ALL, include_deleted=True)
        finally:
            conn.close()
        return [self._collection_wire(r) for r in rows]

    def get_collection(self, collection_id: int) -> Optional[dict]:
        conn = _get_conn()
        try:
            found = collections_svc.get_collection(conn, int(collection_id), include_deleted=True)
        finally:
            conn.close()
        return self._collection_wire(found) if found else None

    def _collection_papers(self, collection_id: int) -> list:
        conn = _get_conn()
        try:
            ids = collections_svc.member_ids(conn, int(collection_id))
            if not ids:
                return []
            marks = ",".join("?" * len(ids))
            rows = conn.execute(f"SELECT * FROM papers WHERE id IN ({marks})", ids).fetchall()
        finally:
            conn.close()
        by_id = {r["id"]: dict(r) for r in rows}
        return [by_id[pid] for pid in ids if pid in by_id]

    def get_collection_refs(self, collection_id: int) -> list:
        # Membership order (oldest addition first) — stable across calls.
        refs: list = []
        for paper in self._collection_papers(collection_id):
            key = _cite_key(paper)
            if key and key not in refs:
                refs.append(key)
        return refs

    def export_collection_bibtex(self, collection_id: int) -> str:
        # The same builder (and the same Cite Keys) as the Collection's .bib
        # download (#202) and export_bibtex().
        return collection_export.bibtex_text(self._collection_papers(collection_id))

    def on_change(self, callback):
        # The core's change feed (library_events): Collection events after
        # every committed write. The Add-on unsubscribes in deactivate();
        # plugin_loader drops whatever it left behind on unload.
        return library_events.subscribe(callback)


#: The wire shape of one Collection in the Add-on Contract.
COLLECTION_FIELDS = (
    "id", "name", "description", "archived", "archived_at", "deleted",
    "deleted_at", "created_at", "updated_at", "item_count",
)
