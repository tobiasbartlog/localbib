"""Cite-key generation. Pure module, no I/O (like paper_matcher).

A Cite Key is the stored, unique, user-editable BibTeX key of a Paper
(see docs/PRD-latex-editor.md). The base scheme deliberately reproduces
the legacy on-the-fly key (``FirstAuthorYear``) so that the one-time
backfill yields the same keys plugin-side ``refs`` were written against.
"""

import itertools
import re
import string
from typing import Optional, Set


def base_key(authors: str, year: Optional[int]) -> str:
    """Legacy scheme: first author up to the first comma + year.

    Must stay byte-identical to the historical webapp._generate_bibtex_key
    output for backfill compatibility — do not "fix" edge cases here.
    """
    first_author = authors.split(",")[0].strip() if authors else "Unknown"
    author_part = re.sub(r"[^\w]", "", first_author)
    return f"{author_part}{year or 'XXXX'}"


def _suffixes():
    yield ""
    for length in itertools.count(1):
        for combo in itertools.product(string.ascii_lowercase, repeat=length):
            yield "".join(combo)


def dedupe(base: str, existing_keys: Set[str]) -> str:
    """De-duplicate ``base`` against ``existing_keys`` with a/b/…/aa… suffixes.
    Also used by the BibTeX import to keep .bib entry keys unique."""
    for suffix in _suffixes():
        key = f"{base}{suffix}"
        if key not in existing_keys:
            return key


def generate(authors: str, year: Optional[int], existing_keys: Set[str]) -> str:
    """Unique key for (authors, year): base key, de-duplicated with a/b/…/aa…"""
    return dedupe(base_key(authors, year), existing_keys)
