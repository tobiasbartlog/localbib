"""Unit tests for the BibTeX and RIS migration adapters (#174).

Both adapters are pure: they read a file and return items. What is worth
pinning down is the part the plain imports always dropped — tags, collections,
notes, the date the entry entered the *old* library, and the local PDF.
"""

from __future__ import annotations

import bibtex_migration
import ris_import


# ---------------------------------------------------------------------------
# Fixtures on disk (an export is a file next to its attachments)
# ---------------------------------------------------------------------------

def _pdf(tmp_path, *parts) -> str:
    target = tmp_path.joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"%PDF-1.4 test")
    return str(target)


BIB = """
@article{smith2020,
  title = {A Study of Things},
  author = {Smith, Jane and Doe, John},
  journal = {Journal of Things},
  year = {2020},
  doi = {10.1234/abc},
  abstract = {We studied things.},
  keywords = {machine learning, ethics},
  groups = {Reading/Papers},
  note = {Very relevant for chapter 3},
  timestamp = {2019-03-04},
  file = {:%(pdf)s:pdf},
}

@book{mueller2018,
  title = {Ein Buch},
  author = {M\\"uller, Anna},
  year = {2018},
  publisher = {Verlag},
  isbn = {978-3-16-148410-0},
  mendeley-tags = {tag1;tag2},
  file = {Full Text PDF:missing/nope.pdf:application/pdf},
}
"""


class TestBibtexAdapter:
    def _items(self, tmp_path):
        pdf = _pdf(tmp_path, "files", "12", "x.pdf")
        (tmp_path / "library.bib").write_text(
            BIB % {"pdf": pdf.replace(":", r"\:")}, encoding="utf-8")
        return bibtex_migration.read_items(str(tmp_path / "library.bib")), pdf

    def test_bibliographic_fields_survive(self, tmp_path):
        items, _ = self._items(tmp_path)
        first = items[0]
        assert first.key == "smith2020"
        assert first.entry_type == "article"
        assert first.title == "A Study of Things"
        assert first.authors == "Smith, Jane; Doe, John"
        assert first.year == 2020
        assert first.doi == "10.1234/abc"
        assert first.journal == "Journal of Things"
        assert items[1].isbn == "978-3-16-148410-0"
        assert items[1].publisher == "Verlag"

    def test_keywords_become_tags(self, tmp_path):
        items, _ = self._items(tmp_path)
        assert items[0].tags == ["machine learning", "ethics"]

    def test_mendeley_tags_are_read_too(self, tmp_path):
        items, _ = self._items(tmp_path)
        assert items[1].tags == ["tag1", "tag2"]

    def test_groups_become_a_collection_path(self, tmp_path):
        items, _ = self._items(tmp_path)
        assert items[0].collection_path == ["Reading", "Papers"]
        assert items[1].collection_path == []

    def test_note_and_timestamp_land_in_their_fields(self, tmp_path):
        items, _ = self._items(tmp_path)
        assert items[0].notes == "Very relevant for chapter 3"
        assert items[0].date_added == "2019-03-04"
        assert items[1].date_added is None

    def test_the_file_field_resolves_to_the_pdf_on_disk(self, tmp_path):
        items, pdf = self._items(tmp_path)
        assert items[0].attachments == [pdf]
        assert items[0].attachments_missing == []

    def test_a_missing_file_is_counted_not_raised(self, tmp_path):
        items, _ = self._items(tmp_path)
        assert items[1].attachments == []
        assert items[1].attachments_missing == [
            "Full Text PDF:missing/nope.pdf:application/pdf"]

    def test_unknown_fields_stay_in_raw(self, tmp_path):
        items, _ = self._items(tmp_path)
        assert items[0].raw["groups"] == "Reading/Papers"
        assert items[0].raw["keywords"] == "machine learning, ethics"

    def test_a_windows_path_is_not_eaten_by_the_latex_pass(self, tmp_path):
        """``\\c`` is a cedilla to a LaTeX converter and a folder to Windows.

        The verbatim second parse exists for exactly this entry — without it
        ``C:\\citavi\\y.pdf`` comes back as a combining accent.
        """
        (tmp_path / "x.bib").write_text(
            "@misc{a, title={T}, file={Name:C\\\\:\\\\citavi\\\\y.pdf:PDF}}",
            encoding="utf-8")
        items = bibtex_migration.read_items(str(tmp_path / "x.bib"))
        assert "citavi" in items[0].raw["file"]

    def test_tags_and_collections_split_on_both_separators(self):
        assert bibtex_migration.split_tags("a, b; c") == ["a", "b", "c"]
        assert bibtex_migration.split_tags("a, a") == ["a"]
        assert bibtex_migration.split_collection("Diss/Kap 2, Andere") == ["Diss", "Kap 2"]


# ---------------------------------------------------------------------------
# RIS
# ---------------------------------------------------------------------------

RIS = """TY  - JOUR
TI  - Deep Things in Shallow Waters
AU  - Smith, Jane
AU  - Doe, John
PY  - 2021///
JO  - Journal of Things
DO  - 10.5555/xyz
AB  - This abstract is wrapped by the exporting tool
and continues on a second line.
KW  - machine learning
KW  - ethics
N1  - Read twice, cite once.
VL  - 12
IS  - 3
SP  - 100
EP  - 119
UR  - https://example.org/paper
L1  - %(pdf)s
ER  -

TY  - BOOK
TI  - Ein Buch
AU  - Mueller, Anna
PY  - 2018
PB  - Verlag
SN  - 978-3-16-148410-0
L1  - missing/nope.pdf
ER  -
"""


class TestRisParser:
    def test_records_are_split_on_ty_and_er(self):
        records = ris_import.parse_ris(RIS % {"pdf": "x.pdf"})
        assert len(records) == 2
        assert records[0]["TY"] == ["JOUR"]

    def test_a_wrapped_value_is_folded_back_into_its_tag(self):
        records = ris_import.parse_ris(RIS % {"pdf": "x.pdf"})
        assert records[0]["AB"] == [
            "This abstract is wrapped by the exporting tool "
            "and continues on a second line."
        ]

    def test_repeated_tags_keep_every_value(self):
        records = ris_import.parse_ris(RIS % {"pdf": "x.pdf"})
        assert records[0]["AU"] == ["Smith, Jane", "Doe, John"]
        assert records[0]["KW"] == ["machine learning", "ethics"]

    def test_a_record_without_er_still_arrives(self):
        records = ris_import.parse_ris("TY  - JOUR\nTI  - Orphan\n")
        assert records[0]["TI"] == ["Orphan"]


class TestRisAdapter:
    def _items(self, tmp_path):
        pdf = _pdf(tmp_path, "pdfs", "deep.pdf")
        (tmp_path / "export.ris").write_text(RIS % {"pdf": pdf}, encoding="utf-8")
        return ris_import.read_items(str(tmp_path / "export.ris")), pdf

    def test_core_fields(self, tmp_path):
        items, _ = self._items(tmp_path)
        first = items[0]
        assert first.entry_type == "article"
        assert first.title == "Deep Things in Shallow Waters"
        assert first.authors == "Smith, Jane; Doe, John"
        assert first.year == 2021
        assert first.doi == "10.5555/xyz"
        assert first.journal == "Journal of Things"
        assert items[1].entry_type == "book"
        assert items[1].isbn == "978-3-16-148410-0"

    def test_kw_becomes_tags_and_n1_becomes_notes(self, tmp_path):
        items, _ = self._items(tmp_path)
        assert items[0].tags == ["machine learning", "ethics"]
        assert items[0].notes == "Read twice, cite once."

    def test_l1_resolves_to_a_local_pdf(self, tmp_path):
        items, pdf = self._items(tmp_path)
        assert items[0].attachments == [pdf]
        assert items[1].attachments == []
        assert items[1].attachments_missing == ["missing/nope.pdf"]

    def test_volume_issue_pages_and_url_stay_in_raw(self, tmp_path):
        items, _ = self._items(tmp_path)
        raw = items[0].raw
        assert (raw["VL"], raw["IS"], raw["SP"], raw["EP"]) == ("12", "3", "100", "119")
        assert raw["UR"] == "https://example.org/paper"


class TestRisLegacyEntries:
    def test_the_old_endpoint_shape_is_unchanged(self):
        entries = ris_import.to_legacy_entries(RIS % {"pdf": "x.pdf"})
        assert len(entries) == 2
        first = entries[0]
        assert first["title"] == "Deep Things in Shallow Waters"
        assert first["authors"] == ["Smith, Jane", "Doe, John"]
        assert first["year"] == 2021
        assert first["doi"] == "10.5555/xyz"
        assert first["journal"] == "Journal of Things"
        assert first["type"] == "JOUR"
        assert first["abstract"].endswith("second line.")

    def test_extra_tags_survive_into_raw_metadata(self):
        entries = ris_import.to_legacy_entries(RIS % {"pdf": "x.pdf"})
        assert entries[0]["KW"] == ["machine learning", "ethics"]
        assert entries[0]["N1"] == "Read twice, cite once."

    def test_a_record_without_a_title_is_dropped(self):
        entries = ris_import.to_legacy_entries("TY  - JOUR\nAU  - Nobody\nER  - \n")
        assert entries == []
