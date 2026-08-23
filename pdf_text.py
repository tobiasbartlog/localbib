#!/usr/bin/env python3
"""PDF-Verarbeitung: Hashing, Textextraktion, OCR und Entsperrung."""

import os
import hashlib
import logging

import fitz  # PyMuPDF

from config import Config


def compute_file_hash(filepath: str) -> str:
    """Berechnet SHA256-Hash einer Datei."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def extract_text_from_pdf(filepath: str, max_pages: int = 5, force_ocr: bool = False) -> str:
    """Extrahiert Text aus PDF mit PyMuPDF. Fällt auf OCR zurück bei gescannten PDFs."""
    try:
        doc = fitz.open(filepath)
        text_parts = []

        # 1. Normaler Textextraktion
        if not force_ocr:
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                text_parts.append(page.get_text())

        text = "\n".join(text_parts)

        # 2. OCR-Fallback wenn zu wenig Text extrahiert wurde
        if len(text.strip()) < Config.OCR_MIN_TEXT_LENGTH:
            ocr_text = ocr_pdf(filepath, max_pages)
            if ocr_text and len(ocr_text.strip()) > len(text.strip()):
                logging.info(f"   OCR lieferte {len(ocr_text)} Zeichen (vs. {len(text.strip())} normal)")
                doc.close()
                return ocr_text

        doc.close()
        return text
    except Exception as e:
        logging.error(f"❌ PDF-Text-Extraktion fehlgeschlagen: {e}")
        return ""


def unlock_pdf(filepath: str) -> bool:
    """Entfernt Passwortschutz / Bearbeitungseinschränkungen aus einem PDF.

    Nutzt PyMuPDF: öffnet das PDF, prüft auf Verschlüsselung,
    und speichert es ohne Verschlüsselung neu.
    Gibt True zurück wenn Schutz entfernt wurde.
    """
    try:
        doc = fitz.open(filepath)

        if not doc.is_encrypted and doc.permissions == 0:
            # Kein Schutz vorhanden
            doc.close()
            return False

        # Prüfe Berechtigungen (Bit-Flags nach PDF-Spec)
        # permissions < 0 oder bestimmte Bits = Einschränkungen
        needs_unlock = doc.is_encrypted or (doc.permissions != 0 and doc.permissions != -1)

        if not needs_unlock:
            # Zusätzlicher Check: Versuche zu erkennen ob Permissions gesetzt sind
            metadata = doc.metadata
            doc.close()

            # Reopne with pikepdf for robust permission removal
            try:
                import pikepdf
                with pikepdf.open(filepath, allow_overwriting_input=True) as pdf:
                    if pdf.is_encrypted:
                        pdf.save(filepath)
                        logging.info(f"   🔓 PDF-Schutz entfernt (pikepdf): {os.path.basename(filepath)}")
                        return True
                return False
            except ImportError:
                return False
            except Exception:
                return False

        # PDF ohne Verschlüsselung neu speichern
        import tempfile
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".pdf", dir=os.path.dirname(filepath))
        os.close(tmp_fd)

        try:
            doc.save(tmp_path, encryption=fitz.PDF_ENCRYPT_NONE, garbage=4, deflate=True)
            doc.close()
            os.replace(tmp_path, filepath)
            logging.info(f"   🔓 PDF-Schutz entfernt: {os.path.basename(filepath)}")
            return True
        except Exception as e:
            doc.close()
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise e

    except Exception as e:
        logging.warning(f"⚠️  PDF-Schutz konnte nicht entfernt werden: {e}")
        # Fallback: pikepdf
        try:
            import pikepdf
            with pikepdf.open(filepath, allow_overwriting_input=True) as pdf:
                pdf.save(filepath)
            logging.info(f"   🔓 PDF-Schutz entfernt (pikepdf-Fallback): {os.path.basename(filepath)}")
            return True
        except ImportError:
            logging.info("   ℹ️  pikepdf nicht installiert (optionaler Fallback)")
        except Exception as e2:
            logging.warning(f"   ⚠️  Auch pikepdf-Fallback fehlgeschlagen: {e2}")
        return False


def ocr_pdf(filepath: str, max_pages: int = 5) -> str:
    """Führt OCR auf einem PDF durch mittels PyMuPDF + Tesseract."""
    tessdata = Config.setup_tesseract()
    if not tessdata and Config.TESSERACT_PATH != "__system__":
        logging.warning("⚠️  Tesseract nicht gefunden. OCR nicht möglich.")
        logging.warning("   Installiere Tesseract: winget install UB-Mannheim.TesseractOCR")
        return ""

    try:
        doc = fitz.open(filepath)
        text_parts = []
        total_pages = min(max_pages, len(doc))
        logging.info(f"🔍 OCR: Verarbeite {total_pages} Seiten mit Tesseract...")

        for i in range(total_pages):
            page = doc[i]
            try:
                kwargs = {
                    "language": Config.OCR_LANGUAGES,
                    "dpi": 300,
                    "full": True,
                }
                if tessdata:
                    kwargs["tessdata"] = tessdata
                tp = page.get_textpage_ocr(**kwargs)
                page_text = page.get_text(textpage=tp)
                text_parts.append(page_text)
                logging.debug(f"   Seite {i+1}: {len(page_text)} Zeichen")
            except Exception as e:
                logging.warning(f"   OCR Seite {i+1} fehlgeschlagen: {e}")

        doc.close()
        result = "\n".join(text_parts)
        logging.info(f"   ✅ OCR abgeschlossen: {len(result)} Zeichen aus {total_pages} Seiten")
        return result
    except Exception as e:
        logging.error(f"❌ OCR fehlgeschlagen: {e}")
        return ""


def ocr_pdf_searchable(filepath: str) -> bool:
    """Macht ein gescanntes PDF durchsuchbar indem ein unsichtbarer Textlayer eingebettet wird.

    Verwendet PyMuPDF + Tesseract. Schreibt unsichtbaren Text (render_mode=3) über die Seitenbilder,
    sodass der Text im PDF-Viewer markiert und kopiert werden kann.
    Gibt True zurück bei Erfolg.
    """
    tessdata = Config.setup_tesseract()
    if not tessdata and Config.TESSERACT_PATH != "__system__":
        logging.warning("⚠️  Tesseract nicht gefunden. Durchsuchbares PDF nicht möglich.")
        return False

    try:
        doc = fitz.open(filepath)
        font = fitz.Font("helv")
        pages_modified = 0
        total_pages = len(doc)
        logging.info(f"📄 Erstelle durchsuchbares PDF: {os.path.basename(filepath)} ({total_pages} Seiten)")

        for page_idx in range(total_pages):
            page = doc[page_idx]

            # Seite überspringen wenn sie bereits Text enthält
            existing_text = page.get_text().strip()
            if len(existing_text) > 50:
                continue

            # OCR auf dieser Seite ausführen
            try:
                kwargs = {
                    "language": Config.OCR_LANGUAGES,
                    "dpi": 300,
                    "full": True,
                }
                if tessdata:
                    kwargs["tessdata"] = tessdata
                tp = page.get_textpage_ocr(**kwargs)
            except Exception as e:
                logging.warning(f"   OCR Seite {page_idx+1} fehlgeschlagen: {e}")
                continue

            # Wort-Positionen extrahieren
            words = page.get_text("words", textpage=tp)
            if not words:
                continue

            # Unsichtbaren Text an Wort-Positionen einfügen
            tw = fitz.TextWriter(page.rect)
            for word in words:
                x0, y0, x1, y1 = word[:4]
                text = word[4]
                if not text.strip():
                    continue
                word_height = y1 - y0
                fs = max(1, word_height * 0.7)
                try:
                    tw.append((x0, y1 - fs * 0.2), text, fontsize=fs, font=font)
                except Exception:
                    pass

            # render_mode=3 = unsichtbarer Text (nicht gezeichnet, aber selektierbar)
            tw.write_text(page, render_mode=3)
            pages_modified += 1

            if (page_idx + 1) % 10 == 0:
                logging.info(f"   ... {page_idx + 1}/{total_pages} Seiten verarbeitet")

        if pages_modified > 0:
            # In-place save: entweder inkrementell oder via temporäre Datei
            try:
                doc.save(filepath, incremental=True, encryption=0)
            except Exception:
                # Fallback: in temp-Datei speichern und ersetzen
                import tempfile
                tmp_fd, tmp_path = tempfile.mkstemp(suffix=".pdf", dir=os.path.dirname(filepath))
                os.close(tmp_fd)
                doc.save(tmp_path, garbage=4, deflate=True)
                doc.close()
                os.replace(tmp_path, filepath)
                doc = None  # Prevent double close
            logging.info(f"   ✅ Durchsuchbares PDF erstellt ({pages_modified}/{total_pages} Seiten mit OCR-Text)")
        else:
            logging.info("   ℹ️  Alle Seiten hatten bereits Text, nichts zu tun")

        if doc:
            doc.close()
        return pages_modified > 0
    except Exception as e:
        logging.error(f"❌ Durchsuchbares PDF fehlgeschlagen: {e}")
        return False
