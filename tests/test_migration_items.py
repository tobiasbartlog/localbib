"""Unit tests for the migration item model and attachment resolution (#174).

The attachment part is where the real risk sits: four tools, four ways to
escape a path, and one acceptance criterion — a real file must be found, a
missing one must be counted, and nothing may raise.
"""

from __future__ import annotations

import pytest

from migration_items import (
    ATTACH_MODES,
    MigrationItem,
    MigrationOptions,
    attachment_candidates,
    resolve_attachment,
    resolve_attachments,
    split_attachment_specs,
)


@pytest.fixture
def library(tmp_path):
    """A source directory with a PDF next to it, like a real export folder."""
    files = tmp_path / "files" / "12"
    files.mkdir(parents=True)
    pdf = files / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    return tmp_path, pdf


# ---------------------------------------------------------------------------
# The item model
# ---------------------------------------------------------------------------

class TestModel:
    def test_item_defaults_are_empty_not_shared(self):
        a, b = MigrationItem(), MigrationItem()
        a.tags.append("x")
        assert b.tags == []
        assert a.date_added is None

    def test_options_defaults_bring_everything_but_the_llm(self):
        o = MigrationOptions()
        assert (o.import_tags, o.import_notes, o.import_collections) == (True, True, True)
        assert o.llm_categorize is False
        assert o.attach_pdfs == "all"

    def test_options_reject_a_mode_they_do_not_know(self):
        o = MigrationOptions(attach_pdfs="everything", duplicates="explode",
                             extract_references="maybe")
        assert o.attach_pdfs == "all"
        assert o.duplicates == "skip"
        assert o.extract_references == "none"
        assert "all" in ATTACH_MODES


# ---------------------------------------------------------------------------
# Attachment resolution
# ---------------------------------------------------------------------------

class TestSplitSpecs:
    def test_semicolon_separates_several_attachments(self):
        assert split_attachment_specs("a.pdf;b.pdf") == ["a.pdf", "b.pdf"]

    def test_an_escaped_semicolon_stays_inside_the_path(self):
        assert split_attachment_specs(r"we\;ird.pdf") == [r"we\;ird.pdf"]

    def test_empty_field_yields_nothing(self):
        assert split_attachment_specs("") == []
        assert split_attachment_specs("  ;  ") == []


class TestDialects:
    def test_zotero_relative_path_resolves_against_the_export_dir(self, library):
        base, pdf = library
        spec = "Full Text PDF:files/12/x.pdf:application/pdf"
        assert resolve_attachment(spec, str(base)) == str(pdf)

    def test_jabref_escaped_drive_colon(self, library):
        base, pdf = library
        spec = ":" + str(pdf).replace(":", r"\:") + ":pdf"
        assert resolve_attachment(spec, str(base)) == str(pdf)

    def test_citavi_escapes_colon_and_doubles_backslashes(self, library):
        base, pdf = library
        doubled = str(pdf).replace("\\", "\\\\").replace(":", "\\\\:")
        spec = "Name:" + doubled + ":PDF"
        assert resolve_attachment(spec, str(base)) == str(pdf)

    def test_plain_absolute_path(self, library):
        base, pdf = library
        assert resolve_attachment(str(pdf), "") == str(pdf)

    def test_plain_relative_path(self, library):
        base, pdf = library
        assert resolve_attachment("files/12/x.pdf", str(base)) == str(pdf)

    def test_file_url(self, library):
        base, pdf = library
        url = "file:///" + str(pdf).replace("\\", "/").lstrip("/")
        assert resolve_attachment(url, str(base)) == str(pdf)

    def test_mendeley_leading_colon_without_drive(self, library):
        base, pdf = library
        spec = ":" + str(pdf).replace("\\", "/") + ":pdf"
        assert resolve_attachment(spec, str(base)) == str(pdf)


class TestMissing:
    def test_a_path_that_does_not_exist_is_none_not_an_exception(self, library):
        base, _ = library
        assert resolve_attachment("files/12/nope.pdf", str(base)) is None

    def test_garbage_never_raises(self, library):
        base, _ = library
        for spec in ("", "   ", ":::", "\\", "C:\x00bad", "file://", "a" * 5000):
            assert resolve_attachment(spec, str(base)) is None

    def test_a_directory_is_not_an_attachment(self, library):
        base, _ = library
        assert resolve_attachment("files", str(base)) is None


class TestField:
    def test_found_and_missing_are_reported_separately(self, library):
        base, pdf = library
        found, missing = resolve_attachments(
            "Full Text PDF:files/12/x.pdf:application/pdf;Gone:files/12/gone.pdf:pdf",
            str(base),
        )
        assert found == [str(pdf)]
        assert missing == ["Gone:files/12/gone.pdf:pdf"]

    def test_the_same_file_twice_is_attached_once(self, library):
        base, pdf = library
        found, missing = resolve_attachments(
            "A:files/12/x.pdf:pdf;B:files/12/x.pdf:pdf", str(base))
        assert found == [str(pdf)]
        assert missing == []

    def test_candidates_are_derived_without_touching_the_disk(self):
        cands = attachment_candidates("Desc:files/12/x.pdf:application/pdf")
        assert "files/12/x.pdf" in cands
        assert cands[0] == "files/12/x.pdf"  # the classic form wins

    def test_a_windows_path_keeps_its_separators(self):
        cands = attachment_candidates(r":C\:\Users\me\lit\x.pdf:pdf")
        assert r"C:\Users\me\lit\x.pdf" in cands
