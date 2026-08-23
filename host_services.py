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

import logging
import os
from typing import Optional

import bibtex_builder
import embedding_index
from cite_key_generator import base_key as _legacy_cite_key_base
from context import get_conn
from literature_manager import Config, Database, extract_text_from_pdf
from llm_client import embed_texts, llm_for

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
        return (Config.LLM_EMBED_MODEL or "").strip()


def _cite_key(paper: dict) -> str:
    """Gespeicherter Cite Key; Legacy-Fallback fuer Zeilen vor dem Backfill.

    Used by _CoreLibraryApi, the papers-list cite_keys filter, and the
    per-paper /bibtex endpoint. Export router has its own copy so it can
    remain webapp-import-free (import-linter constraint).
    """
    return paper.get("cite_key") or _legacy_cite_key_base(
        paper.get("authors") or "", paper.get("year")
    )


class _CoreLibraryApi:
    """plugin_api.LibraryApi: Lesezugriff auf die Bibliothek per Citekey.

    Citekeys sind gespeicherte, eindeutige Paper-Eigenschaften (Spalte
    cite_key, backfilled in Database.init_db); _cite_key() faellt nur fuer
    Alt-Zeilen ohne Key auf das Legacy-Schema zurueck. Die fruehere
    Kollisionsluecke (on-the-fly-Keys) ist damit geschlossen.
    """

    @staticmethod
    def _wire(paper: dict) -> dict:
        return {
            "citekey": _cite_key(paper),
            "id": paper.get("id"),
            "title": paper.get("title"),
            "authors": paper.get("authors"),
            "year": paper.get("year"),
            "journal": paper.get("journal"),
            "doi": paper.get("doi"),
            "abstract": paper.get("abstract"),
        }

    def _find(self, citekey: str) -> Optional[dict]:
        if not citekey:
            return None
        for paper in db.get_all_papers():
            if _cite_key(paper) == citekey:
                return paper
        return None

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
        filename = (paper or {}).get("filename")
        if not filename:
            return None
        filepath = os.path.join(Config.ALL_DIR, os.path.basename(filename))
        if not os.path.isfile(filepath):
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

    def on_change(self, callback):
        # Kein Change-Feed im Kern — bewusster No-op-Unsubscriber.
        return lambda: None
