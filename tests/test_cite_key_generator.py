from __future__ import annotations

from cite_key_generator import base_key, generate


class TestBaseKey:
    def test_surname_before_first_comma(self):
        assert base_key("Smith, John; Doe, Jane", 2020) == "Smith2020"

    def test_plain_name_without_comma_keeps_full_name(self):
        # Legacy behaviour: split on "," only — "First Last" stays whole.
        assert base_key("John Smith", 2020) == "JohnSmith2020"

    def test_missing_authors_falls_back_to_unknown(self):
        assert base_key("", 2021) == "Unknown2021"

    def test_missing_year_falls_back_to_xxxx(self):
        assert base_key("Smith, John", None) == "SmithXXXX"

    def test_punctuation_stripped(self):
        assert base_key("O'Brien-Smith, Pat", 1999) == "OBrienSmith1999"

    def test_non_ascii_preserved_like_legacy(self):
        # \w matches unicode letters; legacy keys kept umlauts.
        assert base_key("Müller, Hans", 2020) == "Müller2020"


class TestGenerate:
    def test_no_collision_returns_base(self):
        assert generate("Smith, John", 2020, set()) == "Smith2020"

    def test_first_collision_gets_a(self):
        assert generate("Smith, John", 2020, {"Smith2020"}) == "Smith2020a"

    def test_suffixes_sequence(self):
        existing = {"Smith2020", "Smith2020a", "Smith2020b"}
        assert generate("Smith, John", 2020, existing) == "Smith2020c"

    def test_exhausted_single_letters_rolls_over_to_two(self):
        existing = {"Smith2020"} | {
            f"Smith2020{chr(c)}" for c in range(ord("a"), ord("z") + 1)
        }
        assert generate("Smith, John", 2020, existing) == "Smith2020aa"

    def test_existing_keys_not_mutated(self):
        existing = {"Smith2020"}
        generate("Smith, John", 2020, existing)
        assert existing == {"Smith2020"}
