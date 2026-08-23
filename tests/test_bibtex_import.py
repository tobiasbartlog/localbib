"""bibtex_import — .bib parsing, \\cite extraction, author/DOI normalisation."""
from __future__ import annotations

from bibtex_import import extract_cited_keys, format_authors, normalize_doi, parse_bib

SAMPLE_BIB = r"""
@article{vaswani_attention_2017,
  title   = {Attention is {All} you Need},
  author  = {Vaswani, Ashish and Shazeer, Noam},
  journal = {NeurIPS},
  year    = {2017},
  doi     = {https://doi.org/10.48550/arXiv.1706.03762},
}

@inproceedings{mueller2020,
  title     = {{\"U}ber die G{\"u}te},
  author    = {Hans M{\"u}ller},
  booktitle = {Proc. of GI},
  year      = 2020,
  publisher = {Springer},
}

@misc{nokey_skipped,
}

@book{lamport1994,
  title     = {LaTeX: A Document Preparation System},
  author    = {Lamport, Leslie},
  year      = {1994},
  isbn      = {0-201-52983-1},
  publisher = {Addison-Wesley},
}
"""


def test_parse_bib_entries_in_order_with_fields():
    entries = parse_bib(SAMPLE_BIB)
    keys = [e["key"] for e in entries]
    assert keys == ["vaswani_attention_2017", "mueller2020", "lamport1994"]

    v = entries[0]
    assert v["title"] == "Attention is All you Need"
    assert v["authors"] == "Vaswani, Ashish; Shazeer, Noam"
    assert v["year"] == 2017
    assert v["doi"] == "10.48550/arXiv.1706.03762"
    assert v["journal"] == "NeurIPS"
    assert v["entry_type"] == "article"


def test_parse_bib_umlauts_and_booktitle_fallback():
    m = parse_bib(SAMPLE_BIB)[1]
    assert m["title"] == "Über die Güte"
    assert m["authors"] == "Müller, Hans"   # "First Last" flipped to "Last, First"
    assert m["journal"] == "Proc. of GI"    # booktitle fills journal
    assert m["publisher"] == "Springer"


def test_parse_bib_book_isbn():
    b = parse_bib(SAMPLE_BIB)[2]
    assert b["entry_type"] == "book"
    assert b["isbn"] == "0-201-52983-1"
    assert b["raw"]["ID"] == "lamport1994"


def test_format_authors_org_name_single_token():
    assert format_authors("{Deutsche Bahn AG} and Doe, Jane") == "Deutsche Bahn AG; Doe, Jane"


def test_normalize_doi():
    assert normalize_doi("https://doi.org/10.1000/x.") == "10.1000/x"
    assert normalize_doi("  10.1000/y ") == "10.1000/y"
    assert normalize_doi("") == ""


def test_extract_cited_keys_variants_and_comments():
    tex = r"""
\documentclass{article}
\begin{document}
Wie \cite{a1} zeigt, und auch \citep[vgl.][S.~3]{b2, c3}.
\textcite{d4} sowie \autocite*{e5}.
% \cite{kommentiert} wird ignoriert
50\% Rabatt \cite{f6}
\nocite{*}
\end{document}
"""
    assert extract_cited_keys(tex) == {"a1", "b2", "c3", "d4", "e5", "f6"}


def test_extract_cited_keys_empty_source():
    assert extract_cited_keys("kein Zitat hier") == set()
