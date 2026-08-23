#!/usr/bin/env python3
"""CLI-Befehle und Entry-Point für den Literatur-Manager."""

import os
import re
import logging
import argparse
from pathlib import Path

from config import Config, setup_logging
from database import Database
from pipeline import process_paper
from filenames import create_symlinks
from watcher import start_watcher


# =============================================================================
# CLI
# =============================================================================

def cmd_init(args):
    """Initialisiert die Datenbank mit Standardkategorien."""
    db = Database(Config.DB_PATH)

    # Tobis Kategorien
    print("\n📂 Erstelle Kategorie-Struktur...\n")

    # Oberkategorie: Themen
    themen_id = db.add_category(
        "Themen", None,
        "Inhaltliche Themenbereiche der Dissertation",
        "Forschung, Thema, Inhalt"
    )

    db.add_category(
        "Lehm", themen_id,
        "Lehmbau, Lehm als Baumaterial, Tonminerale, Erdbau",
        "clay, earth, Lehm, Ton, Erdbau, adobe, rammed earth"
    )

    db.add_category(
        "Dissertationen", themen_id,
        "Relevante Dissertationen anderer Forscher",
        "Dissertation, PhD, Doktorarbeit, Promotionsschrift"
    )

    db.add_category(
        "Materialeigenschaften", themen_id,
        "Materialkennwerte, Prüfverfahren, Festigkeit, Porosität, Rheologie",
        "material properties, strength, porosity, rheology, testing"
    )

    db.add_category(
        "3D_Druck", themen_id,
        "Additive Fertigung, 3D-Druck im Bauwesen, Extrusion",
        "3D printing, additive manufacturing, extrusion, AM, large-scale"
    )

    # Oberkategorie: AI
    ai_id = db.add_category(
        "AI", None,
        "Künstliche Intelligenz und Machine Learning",
        "AI, ML, deep learning, neural network"
    )

    db.add_category(
        "LLM", ai_id,
        "Large Language Models, NLP, Transformer",
        "LLM, GPT, BERT, transformer, NLP, language model"
    )

    db.add_category(
        "World_Models", ai_id,
        "World Models, Simulationsmodelle, Umgebungsmodellierung",
        "world model, simulation, environment model, planning"
    )

    # Oberkategorie: Dissertationen (als eigene Top-Level-Kategorie)
    db.add_category(
        "Dissertationen_Sammlung", None,
        "Sammlung aller relevanten Dissertationen (Querschnitt)",
        "Dissertation, PhD, thesis"
    )

    print("\n" + db.get_category_tree())
    print("\n✅ Kategorien erstellt! Du kannst jederzeit weitere hinzufügen.\n")


def cmd_add_category(args):
    """Fügt eine neue Kategorie hinzu."""
    db = Database(Config.DB_PATH)
    parent_id = args.parent if args.parent else None
    cat_id = db.add_category(args.name, parent_id, args.description or "", args.keywords or "")
    print(f"✅ Kategorie '{args.name}' erstellt (ID: {cat_id})")

    # Ordner erstellen
    _rebuild_category_folders(db)


def cmd_list_categories(args):
    """Zeigt alle Kategorien."""
    db = Database(Config.DB_PATH)
    print("\n" + db.get_category_tree() + "\n")


def cmd_import(args):
    """Importiert PDFs aus Input-Ordner."""
    db = Database(Config.DB_PATH)
    pdf_files = list(Path(Config.INPUT_DIR).glob("*.pdf"))

    if not pdf_files:
        print(f"📭 Keine PDFs in {Config.INPUT_DIR}")
        return

    print(f"📬 {len(pdf_files)} PDF(s) gefunden\n")

    for pdf in pdf_files:
        try:
            process_paper(str(pdf), db)
        except Exception as e:
            logging.error(f"❌ Fehler bei {pdf.name}: {e}")

    print(f"\n✅ Import abgeschlossen!")


def cmd_watch(args):
    """Startet Watchdog-Überwachung."""
    db = Database(Config.DB_PATH)
    start_watcher(db)


def cmd_list_papers(args):
    """Zeigt alle Paper."""
    db = Database(Config.DB_PATH)
    papers = db.get_all_papers()

    if not papers:
        print("📭 Keine Paper in der Datenbank.")
        return

    print(f"\n📚 {len(papers)} Paper in der Datenbank:\n")
    for p in papers:
        cats = db.get_paper_categories(p["id"])
        cat_names = ", ".join(c["name"] for c in cats) if cats else "—"
        year = p["year"] or "????"
        authors = p["authors"][:40] if p["authors"] else "Unbekannt"
        title = p["title"][:60] if p["title"] else "Kein Titel"
        print(f"  [{p['id']:3d}] {year} | {authors:<40} | {title}")
        print(f"         Tags: {cat_names}")
        if p["doi"]:
            print(f"         DOI: {p['doi']}")
        print()


def cmd_search(args):
    """Sucht in Paper."""
    db = Database(Config.DB_PATH)
    results = db.search_papers(args.query)

    if not results:
        print(f"🔍 Keine Ergebnisse für '{args.query}'")
        return

    print(f"\n🔍 {len(results)} Treffer für '{args.query}':\n")
    for p in results:
        year = p["year"] or "????"
        print(f"  [{p['id']:3d}] {year} | {p['authors'][:40]} | {p['title'][:60]}")


def cmd_rebuild(args):
    """Baut Kategorie-Ordner und Symlinks neu auf."""
    db = Database(Config.DB_PATH)
    _rebuild_category_folders(db)
    _rebuild_all_symlinks(db)
    print("✅ Ordnerstruktur und Verknüpfungen neu erstellt!")


def cmd_export_bibtex(args):
    """Exportiert alle Paper als BibTeX."""
    db = Database(Config.DB_PATH)
    papers = db.get_all_papers()

    if not papers:
        print("📭 Keine Paper zum Exportieren.")
        return

    output_path = args.output or os.path.join(Config.BASE_DIR, "literatur.bib")

    with open(output_path, "w", encoding="utf-8") as f:
        for p in papers:
            # BibTeX-Key generieren
            author_key = re.sub(r'[^\w]', '', (p["authors"] or "Unknown").split(",")[0])
            year = p["year"] or "XXXX"
            key = f"{author_key}{year}"

            f.write(f"@article{{{key},\n")
            if p["title"]:
                f.write(f"  title = {{{p['title']}}},\n")
            if p["authors"]:
                f.write(f"  author = {{{p['authors']}}},\n")
            if p["year"]:
                f.write(f"  year = {{{p['year']}}},\n")
            if p["journal"]:
                f.write(f"  journal = {{{p['journal']}}},\n")
            if p["doi"]:
                f.write(f"  doi = {{{p['doi']}}},\n")
            if p["isbn"]:
                f.write(f"  isbn = {{{p['isbn']}}},\n")
            if p["abstract"]:
                f.write(f"  abstract = {{{p['abstract'][:500]}}},\n")
            f.write(f"}}\n\n")

    print(f"✅ BibTeX exportiert: {output_path} ({len(papers)} Einträge)")


def cmd_stats(args):
    """Zeigt Statistiken."""
    db = Database(Config.DB_PATH)
    papers = db.get_all_papers()
    categories = db.get_categories()

    print(f"\n📊 Statistiken:")
    print(f"   Paper:      {len(papers)}")
    print(f"   Kategorien: {len(categories)}")

    if papers:
        years = [p["year"] for p in papers if p["year"]]
        if years:
            print(f"   Zeitraum:   {min(years)} – {max(years)}")

        with_doi = sum(1 for p in papers if p["doi"])
        print(f"   Mit DOI:    {with_doi}/{len(papers)}")

    print()


# --- Hilfsfunktionen ---

def _rebuild_category_folders(db: Database):
    """Erstellt Ordnerstruktur aus Kategorien."""
    categories = db.get_categories()
    cat_map = {c["id"]: c for c in categories}

    for cat in categories:
        path_parts = [cat["name"]]
        parent_id = cat.get("parent_id")
        while parent_id is not None:
            parent = cat_map.get(parent_id)
            if parent:
                path_parts.insert(0, parent["name"])
                parent_id = parent.get("parent_id")
            else:
                break

        cat_dir = os.path.join(Config.CATEGORIES_DIR, *path_parts)
        os.makedirs(cat_dir, exist_ok=True)


def _rebuild_all_symlinks(db: Database):
    """Löscht alle Dateien und veraltete Ordner, baut Kategorie-Ordner neu auf."""
    if os.path.isdir(Config.CATEGORIES_DIR):
        for root, dirs, files in os.walk(Config.CATEGORIES_DIR):
            for fname in files:
                try:
                    os.remove(os.path.join(root, fname))
                except OSError as e:
                    logging.warning(f"Konnte Datei nicht entfernen: {fname}: {e}")
        for root, dirs, files in os.walk(Config.CATEGORIES_DIR, topdown=False):
            if root == Config.CATEGORIES_DIR:
                continue
            try:
                os.rmdir(root)
            except OSError:
                pass

    os.makedirs(Config.CATEGORIES_DIR, exist_ok=True)
    _rebuild_category_folders(db)
    papers = db.get_all_papers()
    for p in papers:
        create_symlinks(db, p["id"], p["filename"])


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="📚 Literatur-Manager – Automatisches Tagging für Dissertationen",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  %(prog)s init                     # Datenbank + Kategorien erstellen
  %(prog)s import                   # PDFs aus Input-Ordner verarbeiten
  %(prog)s watch                    # Input-Ordner überwachen
  %(prog)s list                     # Alle Paper anzeigen
  %(prog)s search "clay printing"   # Paper suchen
  %(prog)s categories               # Kategorien anzeigen
  %(prog)s add-cat "Nachhaltigkeit" --parent 1 --desc "EPD, LCA, Ökobilanz"
  %(prog)s bibtex                   # BibTeX exportieren
  %(prog)s rebuild                  # Ordner/Links neu aufbauen
  %(prog)s stats                    # Statistiken
        """
    )

    parser.add_argument(
        "--dir", "-d", default=os.path.join(os.path.expanduser("~"), "Literatur"),
        help="Basis-Verzeichnis (Standard: ~/Literatur)"
    )
    parser.add_argument("--verbose", "-v", action="store_true")

    subparsers = parser.add_subparsers(dest="command")

    # init
    subparsers.add_parser("init", help="Datenbank initialisieren + Standardkategorien")

    # import
    subparsers.add_parser("import", help="PDFs aus Input-Ordner verarbeiten")

    # watch
    subparsers.add_parser("watch", help="Input-Ordner überwachen (Watchdog)")

    # list
    subparsers.add_parser("list", help="Alle Paper anzeigen")

    # search
    p_search = subparsers.add_parser("search", help="Paper suchen")
    p_search.add_argument("query", help="Suchbegriff")

    # categories
    subparsers.add_parser("categories", help="Kategorien anzeigen")

    # add-cat
    p_cat = subparsers.add_parser("add-cat", help="Kategorie hinzufügen")
    p_cat.add_argument("name", help="Name der Kategorie")
    p_cat.add_argument("--parent", "-p", type=int, help="Parent-Kategorie-ID")
    p_cat.add_argument("--desc", "--description", dest="description", help="Beschreibung")
    p_cat.add_argument("--keywords", "-k", help="Keywords (kommagetrennt)")

    # bibtex
    p_bib = subparsers.add_parser("bibtex", help="BibTeX exportieren")
    p_bib.add_argument("--output", "-o", help="Ausgabedatei")

    # rebuild
    subparsers.add_parser("rebuild", help="Ordner und Symlinks neu aufbauen")

    # stats
    subparsers.add_parser("stats", help="Statistiken anzeigen")

    args = parser.parse_args()

    # Pfade initialisieren
    Config.init_paths(args.dir)
    setup_logging(args.verbose)

    if not args.command:
        parser.print_help()
        return

    commands = {
        "init": cmd_init,
        "import": cmd_import,
        "watch": cmd_watch,
        "list": cmd_list_papers,
        "search": cmd_search,
        "categories": cmd_list_categories,
        "add-cat": cmd_add_category,
        "bibtex": cmd_export_bibtex,
        "rebuild": cmd_rebuild,
        "stats": cmd_stats,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()
