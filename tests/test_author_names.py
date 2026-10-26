"""author_names: the core twin of an Add-on's name heuristic.

The name and author-list cases of that Add-on's citation-style tests, ported —
the core must read an author string exactly as the Add-on does — plus the
core's extra organization markers.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import bibtex_builder
from author_names import (display_people, is_organization, join_people, normalize_people, parse_name,
                          split_people)

PEOPLE_VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "people_vectors.json").read_text(encoding="utf-8"))["vectors"]


@pytest.mark.parametrize("vector", PEOPLE_VECTORS, ids=lambda v: v["input"])
def test_people_vectors_shared_with_the_spa(vector):
    """The server's half of the shared vectors: the SPA's edit form reads
    every input into the same people, one per line (tests/js)."""
    assert split_people(vector["input"]) == vector["people"]


class TestBracedOrganizations:
    """Maintainer decision (a): a name in braces is an organization. Storage
    keeps the braces, the export writes them, every display strips them."""

    def test_a_braced_name_is_an_organization_without_its_braces(self):
        name = parse_name("{Autobahn Westfalen}")
        assert (name.last, name.given, name.org) == ("Autobahn Westfalen", "", True)
        assert is_organization("{Autobahn Westfalen}")
        assert parse_name("{Müller, Schmidt and Partner}").last == "Müller, Schmidt and Partner"

    def test_the_export_writes_the_braces(self):
        assert bibtex_builder.people_field("{Autobahn Westfalen}; Chen, Li") == (
            "{Autobahn Westfalen} and Chen, Li")
        assert bibtex_builder.people_field("{Mueller, Schmidt and Partner}") == "{Mueller, Schmidt and Partner}"

    def test_display_strips_only_the_braces(self):
        assert display_people("{Autobahn Westfalen}; Chen, Li") == "Autobahn Westfalen; Chen, Li"
        assert display_people(None) == ""
        assert display_people("Chen, Li") == "Chen, Li"


@pytest.mark.parametrize("raw, last, given", [
    ("Mustermann, Tobias", "Mustermann", "Tobias"),
    ("Tobias Mustermann", "Mustermann", "Tobias"),
    ("T. Mustermann", "Mustermann", "T."),
    ("van der Berg, Jan", "van der Berg", "Jan"),
    ("Jan van der Berg", "van der Berg", "Jan"),
    ("CERN", "CERN", ""),
    ("  Krause ,  M.  ", "Krause", "M."),
])
def test_parse_name(raw, last, given):
    name = parse_name(raw)
    assert (name.last, name.given) == (last, given)


@pytest.mark.parametrize("value, names", [
    ("Mustermann, T.; Krause, M.", ["Mustermann, T.", "Krause, M."]),
    ("Mustermann, T. and Krause, M.", ["Mustermann, T.", "Krause, M."]),
    ("Mustermann, Tobias", ["Mustermann, Tobias"]),
    # the library's old form (CrossRef): "Family, Given" per person, joined by ", "
    ("Mustermann, Tobias, Krause, Maria", ["Mustermann, Tobias", "Krause, Maria"]),
    ("Mustermann, T. M., Krause, M.", ["Mustermann, T. M.", "Krause, M."]),
    # A comma-only string is CrossRef's "Family, Given" first: two parts are
    # one person, even when both have two words …
    ("García Márquez, Gabriel José", ["García Márquez, Gabriel José"]),
    ("Schmidt, Anna Maria Luise", ["Schmidt, Anna Maria Luise"]),
    ("Schmidt, Anna Maria Luise Sophie, Krause, M.", ["Schmidt, Anna Maria Luise Sophie", "Krause, M."]),
    # … a family name that is also an organization word is still a person …
    ("Group, Hans", ["Group, Hans"]),
    ("Group, Hans, Center, Anna", ["Group, Hans", "Center, Anna"]),
    # … and only an odd number of "Given Family" names reads as such a list.
    ("Tobias Mustermann, Maria Krause, Sven Lindner", ["Tobias Mustermann", "Maria Krause", "Sven Lindner"]),
    ("Wei Li, Na Wang, Jun Chen", ["Wei Li", "Na Wang", "Jun Chen"]),
    (["Mustermann, T.", " ", "Krause, M."], ["Mustermann, T.", "Krause, M."]),
    ("", []),
    (None, []),
    # family names of several words, given names of several words or initials
    ("van der Berg, J. P., Krause, M.", ["van der Berg, J. P.", "Krause, M."]),
    ("Da Silva, Ana Maria, Mustermann, Tobias", ["Da Silva, Ana Maria", "Mustermann, Tobias"]),
    ("Le Roux, Jean Paul", ["Le Roux, Jean Paul"]),
    ("Mustermann, J.-P., Krause, Maria", ["Mustermann, J.-P.", "Krause, Maria"]),
    # organizations stand alone (CrossRef writes them without a given name)
    ("ACME Consortium, Mustermann, Tobias", ["ACME Consortium", "Mustermann, Tobias"]),
    ("Mustermann, Tobias, ACME Consortium", ["Mustermann, Tobias", "ACME Consortium"]),
    ("ACME Consortium, World Health Organization", ["ACME Consortium", "World Health Organization"]),
    ("Krause, Maria, Max Planck Institut für Eisenforschung, Lindner, S.",
     ["Krause, Maria", "Max Planck Institut für Eisenforschung", "Lindner, S."]),
    ("Krause, Maria, Arbeitskreis Messtechnik im Maschinenbau Nord",
     ["Krause, Maria", "Arbeitskreis Messtechnik im Maschinenbau Nord"]),
    # with ";" each part is one person, in either order
    ("Da Silva, Ana Maria; Tobias Mustermann; ACME Consortium",
     ["Da Silva, Ana Maria", "Tobias Mustermann", "ACME Consortium"]),
])
def test_split_people(value, names):
    assert split_people(value) == names


@pytest.mark.parametrize("raw, last, given", [
    ("Da Silva, Ana Maria", "Da Silva", "Ana Maria"),
    ("Le Roux, Jean Paul", "Le Roux", "Jean Paul"),
    ("Ana Maria da Silva", "da Silva", "Ana Maria"),
    ("ACME Consortium", "ACME Consortium", ""),
    ("World Health Organization", "World Health Organization", ""),
    # "Family, Given" is always a person, whatever its words
    ("Schmidt, Anna Maria Luise", "Schmidt", "Anna Maria Luise"),
    ("García Márquez, Gabriel José", "García Márquez", "Gabriel José"),
    ("Group, Hans", "Group", "Hans"),
    ("Anna Maria Luise Schmidt", "Schmidt", "Anna Maria Luise"),
])
def test_parse_name_with_particles_and_organizations(raw, last, given):
    name = parse_name(raw)
    assert (name.last, name.given) == (last, given)
    assert name.org == (given == "")


def test_acme_consortium_without_a_comma_stays_an_organization():
    assert parse_name("ACME Consortium").org
    assert split_people("ACME Consortium") == ["ACME Consortium"]


@pytest.mark.parametrize("name", [
    "United Nations Environment Programme",
    "Bundesanstalt für Straßenwesen",
    "Umweltbundesamt",
    "Bundesministerium für Digitales und Verkehr",
    "Die Autobahn GmbH des Bundes",
    "Deutsche Bahn AG",
    "Maurer SE",
])
def test_grey_literature_bodies_are_organizations(name):
    assert is_organization(name)
    assert parse_name(name).org


@pytest.mark.parametrize("name", ["Tobias Mustermann", "Anna Maria Luise Schmidt", "Wei Li", "Hans Amtmann"])
def test_persons_stay_persons(name):
    assert not is_organization(name)


def test_join_and_normalize_write_the_storage_form():
    assert join_people(["Müller, Hans", " ", "Schmidt, Eva"]) == "Müller, Hans; Schmidt, Eva"
    assert normalize_people("Müller, Hans, Schmidt, Eva") == "Müller, Hans; Schmidt, Eva"
    assert normalize_people("Müller, Hans and Schmidt, Eva") == "Müller, Hans; Schmidt, Eva"
    assert normalize_people(None) == ""
