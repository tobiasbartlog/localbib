#!/usr/bin/env python3
"""SQLite-Datenbank für Paper und Kategorien."""

import sqlite3
import logging
from typing import Optional, Dict, List

from cite_key_generator import dedupe as dedupe_cite_key, generate as generate_cite_key


# =============================================================================
# DATENBANK
# =============================================================================

class Database:
    """SQLite-Datenbank für Paper und Kategorien."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self):
        """Erstellt Datenbank-Schema."""
        conn = self._connect()
        cursor = conn.cursor()

        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                parent_id INTEGER,
                description TEXT DEFAULT '',
                keywords TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (parent_id) REFERENCES categories(id),
                UNIQUE(name, parent_id)
            );

            CREATE TABLE IF NOT EXISTS papers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_hash TEXT UNIQUE NOT NULL,
                filename TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                title TEXT DEFAULT '',
                authors TEXT DEFAULT '',
                year INTEGER,
                doi TEXT DEFAULT '',
                isbn TEXT DEFAULT '',
                abstract TEXT DEFAULT '',
                journal TEXT DEFAULT '',
                publisher TEXT DEFAULT '',
                raw_metadata TEXT DEFAULT '',
                ocr_text TEXT DEFAULT '',
                openalex_id TEXT DEFAULT '',
                cited_by_count INTEGER DEFAULT 0,
                openalex_updated_at TEXT DEFAULT '',
                cite_key TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS paper_categories (
                paper_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                confidence REAL DEFAULT 0.0,
                assigned_by TEXT DEFAULT 'llm',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (paper_id, category_id),
                FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_papers_hash ON papers(file_hash);
            CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi);

            -- Benutzerdefinierte Spalten
            CREATE TABLE IF NOT EXISTS custom_fields (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                field_type TEXT NOT NULL DEFAULT 'text',
                options TEXT DEFAULT '',
                position INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS paper_custom_values (
                paper_id INTEGER NOT NULL,
                field_id INTEGER NOT NULL,
                value TEXT DEFAULT '',
                PRIMARY KEY (paper_id, field_id),
                FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
                FOREIGN KEY (field_id) REFERENCES custom_fields(id) ON DELETE CASCADE
            );

            -- Projekte
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                color TEXT DEFAULT '#6366f1',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS paper_projects (
                paper_id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                PRIMARY KEY (paper_id, project_id),
                FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
        """)

        # Migration: Spalten hinzufügen (für bestehende DBs)
        for col, coldef in [
            ("openalex_id", "TEXT DEFAULT ''"),
            ("cited_by_count", "INTEGER DEFAULT 0"),
            ("openalex_updated_at", "TEXT DEFAULT ''"),
            ("doi_manually_verified", "INTEGER DEFAULT 0"),
            ("abstract_source", "TEXT DEFAULT ''"),
            ("page_count", "INTEGER DEFAULT 0"),
            ("cite_key", "TEXT DEFAULT ''"),
            # Concrete import origin (e.g. the .bib/.ris filename) for papers
            # created by a batch import. Empty for older rows and for direct PDF
            # uploads; the SPA derives the import *type* from original_filename
            # and shows this as the source detail on the provenance stamp.
            ("import_source", "TEXT DEFAULT ''"),
        ]:
            try:
                cursor.execute(f"ALTER TABLE papers ADD COLUMN {col} {coldef}")
            except sqlite3.OperationalError:
                pass  # Spalte existiert bereits

        # Cite Keys: Eindeutigkeit (leere Keys ausgenommen) + einmaliger Backfill
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_cite_key
            ON papers(cite_key) WHERE cite_key != ''
        """)
        self._backfill_cite_keys(cursor)

        # Migration: Zitationstabelle fuer PDF-extrahierte Referenzen
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS paper_references (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_paper_id INTEGER NOT NULL,
                ref_index INTEGER DEFAULT 0,
                title TEXT DEFAULT '',
                authors TEXT DEFAULT '',
                year INTEGER,
                journal TEXT DEFAULT '',
                doi TEXT DEFAULT '',
                matched_paper_id INTEGER,
                match_confidence REAL DEFAULT 0.0,
                source TEXT DEFAULT 'pdf_llm',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_paper_id) REFERENCES papers(id) ON DELETE CASCADE,
                FOREIGN KEY (matched_paper_id) REFERENCES papers(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_paper_refs_source ON paper_references(source_paper_id);
            CREATE INDEX IF NOT EXISTS idx_paper_refs_matched ON paper_references(matched_paper_id);
        """);

        # Research-Chat: Chunks fuer RAG-basierte Papier-Analyse
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS paper_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id INTEGER NOT NULL,
                chunk_index INTEGER DEFAULT 0,
                page_start INTEGER DEFAULT 0,
                page_end INTEGER DEFAULT 0,
                chunk_text TEXT DEFAULT '',
                token_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_paper_chunks_paper ON paper_chunks(paper_id);
        """);

        # App-Settings: Key-Value-Store fuer globale App-Einstellungen (z.B. Lizenzschluessel)
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT DEFAULT ''
            );
        """);

        # Semantische Suche Phase 1 (#98): ein Vektor pro Paper (Titel+Abstract).
        # paper_id ist Primary Key -> genau ein aktiver Vektor je Paper; model/dim
        # werden mitgespeichert, damit ein Embedding-Modell-Wechsel erkennbar ist
        # (Indexer re-embedded, statt Vektorraeume stillschweigend zu mischen).
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS paper_embeddings (
                paper_id INTEGER PRIMARY KEY,
                model TEXT NOT NULL,
                dim INTEGER NOT NULL,
                vector BLOB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_paper_embeddings_model ON paper_embeddings(model);
        """);

        # Semantische Suche Phase 3 (#102): ein Vektor pro Chunk (kontextangereichert:
        # Titel[+Jahr/Journal] + Chunk-Text, PRD Entscheidung 9) fuers Passage-Retrieval.
        # chunk_id ist Primary Key -> genau ein aktiver Vektor je Chunk; model/dim wie
        # bei paper_embeddings mitgespeichert (Modellwechsel-Erkennung).
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS chunk_embeddings (
                chunk_id INTEGER PRIMARY KEY,
                model TEXT NOT NULL,
                dim INTEGER NOT NULL,
                vector BLOB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chunk_id) REFERENCES paper_chunks(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_model ON chunk_embeddings(model);
        """);

        conn.commit()
        conn.close()
        logging.info("✅ Datenbank initialisiert")

    @staticmethod
    def _backfill_cite_keys(cursor: sqlite3.Cursor):
        """Vergibt Cite Keys fuer Paper ohne Key — deterministisch nach id,
        damit wiederholte Laeufe bestehende Keys nie aendern."""
        rows = cursor.execute(
            "SELECT id, authors, year, cite_key FROM papers ORDER BY id"
        ).fetchall()
        existing = {r["cite_key"] for r in rows if r["cite_key"]}
        for row in rows:
            if row["cite_key"]:
                continue
            key = generate_cite_key(row["authors"] or "", row["year"], existing)
            existing.add(key)
            cursor.execute(
                "UPDATE papers SET cite_key = ? WHERE id = ?", (key, row["id"])
            )

    # --- Kategorien ---

    def add_category(self, name: str, parent_id: Optional[int] = None,
                     description: str = "", keywords: str = "") -> int:
        """Fügt Kategorie hinzu. Gibt ID zurück."""
        conn = self._connect()
        try:
            cursor = conn.execute(
                "INSERT INTO categories (name, parent_id, description, keywords) VALUES (?, ?, ?, ?)",
                (name, parent_id, description, keywords)
            )
            conn.commit()
            cat_id = cursor.lastrowid
            logging.info(f"📁 Kategorie erstellt: {name} (ID: {cat_id})")
            return cat_id
        except sqlite3.IntegrityError:
            # Existiert bereits
            row = conn.execute(
                "SELECT id FROM categories WHERE name = ? AND parent_id IS ?",
                (name, parent_id)
            ).fetchone()
            return row["id"] if row else -1
        finally:
            conn.close()

    def get_categories(self) -> List[Dict]:
        """Gibt alle Kategorien als hierarchische Struktur zurück."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT id, name, parent_id, description, keywords FROM categories ORDER BY parent_id, name"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_category_tree(self) -> str:
        """Gibt Kategorie-Baum als formatierten String zurück."""
        categories = self.get_categories()
        if not categories:
            return "(keine Kategorien definiert)"

        # Baue Baum
        top_level = [c for c in categories if c["parent_id"] is None]
        children = {}
        for c in categories:
            pid = c["parent_id"]
            if pid is not None:
                children.setdefault(pid, []).append(c)

        lines = []
        for top in top_level:
            lines.append(f"📂 {top['name']}")
            if top["description"]:
                lines.append(f"   Beschreibung: {top['description']}")
            for child in children.get(top["id"], []):
                lines.append(f"  ├── 📁 {child['name']}")
                if child["description"]:
                    lines.append(f"  │   Beschreibung: {child['description']}")
                for grandchild in children.get(child["id"], []):
                    lines.append(f"  │   ├── 📄 {grandchild['name']}")
                    if grandchild["description"]:
                        lines.append(f"  │   │   Beschreibung: {grandchild['description']}")

        return "\n".join(lines)

    # --- Papers ---

    def paper_exists(self, file_hash: str) -> bool:
        conn = self._connect()
        row = conn.execute("SELECT id FROM papers WHERE file_hash = ?", (file_hash,)).fetchone()
        conn.close()
        return row is not None

    def find_duplicate_paper(self, doi: str = "", title: str = "", file_hash: str = "") -> Optional[Dict]:
        """Findet ein existierendes Duplikat anhand von DOI oder Titel.

        Gibt das existierende Paper als Dict zurueck, oder None.
        Prueft: 1) file_hash  2) DOI  3) normalisierter Titel
        """
        import unicodedata
        conn = self._connect()
        try:
            # 1. Hash-Check
            if file_hash:
                row = conn.execute("SELECT id, title, filename FROM papers WHERE file_hash = ?", (file_hash,)).fetchone()
                if row:
                    return dict(row)

            # 2. DOI-Check (nur wenn DOI nicht leer)
            doi = (doi or "").strip().lower()
            if doi and len(doi) > 5:
                row = conn.execute(
                    "SELECT id, title, filename FROM papers WHERE LOWER(TRIM(doi)) = ?", (doi,)
                ).fetchone()
                if row:
                    return dict(row)

            # 3. Titel-Check (normalisiert)
            if title and len(title.strip()) > 15:
                t = unicodedata.normalize("NFKD", title)
                t = t.lower().strip()
                # Alle Papers laden und normalisiert vergleichen
                rows = conn.execute("SELECT id, title, filename FROM papers WHERE title != ''").fetchall()
                import re as _re
                def _norm(s):
                    s = unicodedata.normalize("NFKD", s)
                    s = s.lower().strip()
                    s = _re.sub(r"[^\w\s]", "", s)
                    s = _re.sub(r"\s+", " ", s)
                    return s
                t_norm = _norm(title)
                if len(t_norm) > 10:
                    for row in rows:
                        existing_norm = _norm(row["title"] or "")
                        if existing_norm and existing_norm == t_norm:
                            return dict(row)
            return None
        finally:
            conn.close()

    def add_paper(self, paper_data: Dict) -> int:
        """Fügt Paper hinzu (inkl. automatisch vergebenem Cite Key). Gibt ID zurück."""
        conn = self._connect()
        existing_keys = {
            r[0] for r in conn.execute(
                "SELECT cite_key FROM papers WHERE cite_key != ''"
            )
        }
        paper_data = dict(paper_data)
        paper_data.setdefault("page_count", 0)
        paper_data.setdefault("import_source", "")
        # Caller-supplied key (e.g. BibTeX import keeps the .bib entry key,
        # "Bestand gewinnt" / ADR-0003) — de-duplicated, never overwriting.
        desired_key = (paper_data.get("cite_key") or "").strip()
        if desired_key:
            paper_data["cite_key"] = dedupe_cite_key(desired_key, existing_keys)
        else:
            paper_data["cite_key"] = generate_cite_key(
                paper_data.get("authors") or "", paper_data.get("year"), existing_keys
            )
        cursor = conn.execute("""
            INSERT INTO papers (file_hash, filename, original_filename, title, authors,
                                year, doi, isbn, abstract, journal, publisher, raw_metadata, ocr_text, page_count,
                                cite_key, import_source)
            VALUES (:file_hash, :filename, :original_filename, :title, :authors,
                    :year, :doi, :isbn, :abstract, :journal, :publisher, :raw_metadata, :ocr_text,
                    :page_count, :cite_key, :import_source)
        """, paper_data)
        conn.commit()
        paper_id = cursor.lastrowid
        conn.close()
        return paper_id

    def assign_category(self, paper_id: int, category_id: int,
                        confidence: float = 0.0, assigned_by: str = "llm"):
        """Weist Paper einer Kategorie zu."""
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO paper_categories (paper_id, category_id, confidence, assigned_by) "
                "VALUES (?, ?, ?, ?)",
                (paper_id, category_id, confidence, assigned_by)
            )
            conn.commit()
        finally:
            conn.close()

    def get_paper_categories(self, paper_id: int) -> List[Dict]:
        conn = self._connect()
        rows = conn.execute("""
            SELECT c.id, c.name, c.parent_id, pc.confidence, pc.assigned_by
            FROM paper_categories pc
            JOIN categories c ON pc.category_id = c.id
            WHERE pc.paper_id = ?
        """, (paper_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_papers_by_category(self, category_id: int) -> List[Dict]:
        conn = self._connect()
        # Collect this category + all descendant category IDs
        cat_ids = [category_id]
        queue = [category_id]
        while queue:
            current = queue.pop(0)
            children = conn.execute(
                "SELECT id FROM categories WHERE parent_id = ?", (current,)
            ).fetchall()
            for child in children:
                cat_ids.append(child["id"])
                queue.append(child["id"])

        placeholders = ",".join("?" for _ in cat_ids)
        rows = conn.execute(f"""
            SELECT DISTINCT p.* FROM papers p
            JOIN paper_categories pc ON p.id = pc.paper_id
            WHERE pc.category_id IN ({placeholders})
            ORDER BY p.year DESC, p.authors
        """, cat_ids).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_all_papers(self) -> List[Dict]:
        conn = self._connect()
        rows = conn.execute("SELECT * FROM papers ORDER BY year DESC, authors").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def search_papers(self, query: str) -> List[Dict]:
        conn = self._connect()
        rows = conn.execute("""
            SELECT * FROM papers
            WHERE title LIKE ? OR authors LIKE ? OR abstract LIKE ?
            ORDER BY year DESC
        """, (f"%{query}%", f"%{query}%", f"%{query}%")).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # --- App-Settings (globale Key-Value-Einstellungen) ---

    def get_app_setting(self, key: str) -> str:
        conn = self._connect()
        try:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else ""
        finally:
            conn.close()

    def set_app_setting(self, key: str, value: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()
