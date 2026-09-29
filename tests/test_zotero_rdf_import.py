"""Unit tests for the Zotero RDF migration adapter (#175).

The committed fixture in ``tests/fixtures/zotero_export/`` is a real export in
miniature: four items, two nested collections, tags, one note, two exported
PDFs and two items without a file. What is worth pinning down is everything the
plain BibTeX route loses — the collection tree, the note, the date the entry
entered the *old* library, and which PDF belongs to which item.
"""

from __future__ import annotations

import os

import pytest

import zotero_rdf_import
from migration_items import MigrationUnreadable

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "zotero_export")
RDF = os.path.join(FIXTURE, "Zotero-Export.rdf")


@pytest.fixture(scope="module")
def items():
    return zotero_rdf_import.read_items(RDF)


def _by_key(items, key):
    return next(item for item in items if item.key == key)


class TestBibliographicFields:
    def test_every_item_of_the_export_arrives(self, items):
        assert [i.key for i in items] == ["smith2020", "muller2018", "doe2017", "item_4"]

    def test_article_fields(self, items):
        first = items[0]
        assert first.entry_type == "article"
        assert first.title == "A Study of Migrated Things"
        assert first.authors == "Smith, Jane; Doe, John"
        assert first.year == 2020
        assert first.doi == "10.1234/migrated"
        assert first.journal == "Journal of Things"
        assert first.abstract == "We studied the things that were migrated."

    def test_book_fields(self, items):
        book = _by_key(items, "muller2018")
        assert book.entry_type == "book"
        assert book.title == "Ein Buch über Migration"
        assert book.authors == "Müller, Anna"
        assert book.isbn == "978-3-16-148410-0"
        assert book.publisher == "Verlag für Dinge"

    def test_a_book_section_takes_its_container_title_as_journal(self, items):
        chapter = _by_key(items, "doe2017")
        assert chapter.entry_type == "incollection"
        assert chapter.journal == "Sammelband zur Migration"

    def test_an_rdf_description_with_an_item_type_is_an_item_too(self, items):
        """Zotero writes every type without a ``bib:`` class this way."""
        page = _by_key(items, "item_4")
        assert page.entry_type == "online"
        assert page.raw["zotero:url"] == "https://example.org/eine-webseite"

    def test_the_key_is_derived_because_rdf_has_no_cite_key(self, items):
        assert items[0].key == "smith2020"
        assert items[0].raw["zotero:about"] == "http://dx.doi.org/10.1234/migrated"

    def test_unmapped_fields_stay_in_raw(self, items):
        assert items[0].raw["pages"] == "100-119"
        assert items[0].raw["libraryCatalog"] == "Crossref"


class TestCollections:
    def test_a_nested_collection_becomes_a_path(self, items):
        assert items[0].collection_path == ["Diss", "Kapitel 2"]

    def test_a_top_level_collection_is_a_single_segment(self, items):
        assert _by_key(items, "muller2018").collection_path == ["Diss"]

    def test_an_item_in_several_collections_keeps_the_rest_in_raw(self, items):
        assert items[0].raw["zotero:otherCollections"] == ["Zu lesen"]

    def test_an_item_in_no_collection_has_an_empty_path(self, items):
        assert _by_key(items, "doe2017").collection_path == []


class TestTagsNotesAndDates:
    def test_manual_and_automatic_tags_both_arrive(self, items):
        assert items[0].tags == ["machine learning", "ethics"]

    def test_the_note_html_becomes_plain_text(self, items):
        assert items[0].notes == (
            "Read before chapter 2.\n"
            "The method section is the useful part & the rest is filler."
        )

    def test_date_submitted_is_the_date_added(self, items):
        assert items[0].date_added == "2014-07-01 09:15:00"
        assert _by_key(items, "doe2017").date_added is None

    def test_html_to_text_keeps_block_structure(self):
        assert zotero_rdf_import.html_to_text(
            "<div>a<br/>b</div><p>c &amp; d</p>") == "a\nb\nc & d"


class TestAttachments:
    def test_an_exported_file_resolves_to_the_pdf_on_disk(self, items):
        path = items[0].attachments[0]
        assert os.path.isfile(path)
        assert path.endswith("A Study of Migrated Things.pdf")
        assert items[0].attachments_missing == []

    def test_a_percent_encoded_resource_still_finds_its_file(self, items):
        """Zotero escapes spaces in ``rdf:resource``; the file on disk has them."""
        assert " - 2018 - " in _by_key(items, "muller2018").attachments[0]

    def test_an_item_without_a_file_has_no_attachments(self, items):
        assert _by_key(items, "doe2017").attachments == []
        assert _by_key(items, "doe2017").attachments_missing == []

    def test_a_linked_file_with_an_absolute_url_resolves(self, tmp_path):
        pdf = tmp_path / "linked.pdf"
        pdf.write_bytes(b"%PDF-1.4 linked")
        rdf = tmp_path / "export.rdf"
        rdf.write_text(_linked_file_rdf(pdf.as_uri()), encoding="utf-8")
        item = zotero_rdf_import.read_items(str(rdf))[0]
        assert item.attachments == [str(pdf)]

    def test_a_missing_file_is_counted_not_raised(self, tmp_path):
        rdf = tmp_path / "export.rdf"
        rdf.write_text(_linked_file_rdf("files/99/gone.pdf"), encoding="utf-8")
        item = zotero_rdf_import.read_items(str(rdf))[0]
        assert item.attachments == []
        assert item.attachments_missing == ["files/99/gone.pdf"]

    def test_a_snapshot_is_not_mistaken_for_a_pdf(self, tmp_path):
        snapshot = tmp_path / "files" / "1" / "page.html"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text("<html></html>", encoding="utf-8")
        rdf = tmp_path / "export.rdf"
        rdf.write_text(_linked_file_rdf("files/1/page.html", mime="text/html"),
                       encoding="utf-8")
        item = zotero_rdf_import.read_items(str(rdf))[0]
        assert item.attachments == [] and item.attachments_missing == []


class TestPathResolution:
    def test_a_folder_with_one_rdf_is_accepted(self):
        assert zotero_rdf_import.read_items(FIXTURE)[0].title == \
            "A Study of Migrated Things"

    def test_a_folder_without_an_rdf_is_refused(self, tmp_path):
        with pytest.raises(MigrationUnreadable):
            zotero_rdf_import.read_items(str(tmp_path))

    def test_a_folder_with_two_rdfs_is_refused_not_guessed(self, tmp_path):
        for name in ("a.rdf", "b.rdf"):
            (tmp_path / name).write_text("<rdf:RDF/>", encoding="utf-8")
        with pytest.raises(MigrationUnreadable):
            zotero_rdf_import.read_items(str(tmp_path))


class TestMalformedInput:
    def test_broken_xml_raises_migration_unreadable(self):
        with pytest.raises(MigrationUnreadable):
            zotero_rdf_import.to_items("<rdf:RDF><bib:Article></rdf:RDF>")

    def test_well_formed_xml_that_is_not_rdf_is_refused(self):
        with pytest.raises(MigrationUnreadable):
            zotero_rdf_import.to_items("<library><book/></library>")

    def test_an_empty_document_is_refused(self):
        with pytest.raises(MigrationUnreadable):
            zotero_rdf_import.to_items("")

    def test_an_rdf_without_items_is_empty_not_an_error(self):
        assert zotero_rdf_import.to_items(
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>'
        ) == []


def _linked_file_rdf(resource: str, mime: str = "application/pdf") -> str:
    return f"""<rdf:RDF
 xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:z="http://www.zotero.org/namespaces/export#"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:link="http://purl.org/rss/1.0/modules/link/"
 xmlns:bib="http://purl.org/net/biblio#">
    <bib:Article rdf:about="#item_1">
        <z:itemType>journalArticle</z:itemType>
        <dc:title>Ein verknuepftes PDF</dc:title>
        <link:link rdf:resource="#item_2"/>
    </bib:Article>
    <z:Attachment rdf:about="#item_2">
        <rdf:resource rdf:resource="{resource}"/>
        <link:type>{mime}</link:type>
    </z:Attachment>
</rdf:RDF>"""
