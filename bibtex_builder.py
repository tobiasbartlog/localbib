"""BibTeX rendering. Pure module, no I/O (like citation_graph_builder).

Single source for every place LocalBib emits BibTeX: the per-paper
endpoint, the .bib export, and the auto-generated bibliography fed to
the LaTeX preview compiler (docs/PRD-latex-editor.md).
"""

from typing import Dict, List, Optional

_REPLACEMENTS = [
    ("\\", "\\textbackslash{}"),
    ("{", "\\{"),
    ("}", "\\}"),
    ("&", "\\&"),
    ("%", "\\%"),
    ("#", "\\#"),
    ("_", "\\_"),
    ("~", "\\textasciitilde{}"),
    ("^", "\\textasciicircum{}"),
    ("ä", '{\\"a}'),
    ("ö", '{\\"o}'),
    ("ü", '{\\"u}'),
    ("Ä", '{\\"A}'),
    ("Ö", '{\\"O}'),
    ("Ü", '{\\"U}'),
    ("ß", "{\\ss}"),
]


def escape_latex(text: str) -> str:
    """Escapet Sonderzeichen fuer BibTeX/LaTeX."""
    if not text:
        return ""
    for old, new in _REPLACEMENTS:
        text = text.replace(old, new)
    return text


def format_entry(paper: Dict, key: Optional[str] = None) -> str:
    """One BibTeX entry for a paper dict; key defaults to paper['cite_key']."""
    key = key or paper.get("cite_key") or ""
    journal = paper.get("journal") or ""
    isbn = paper.get("isbn") or ""
    publisher = paper.get("publisher") or ""

    if journal:
        entry_type = "article"
    elif isbn and publisher:
        entry_type = "book"
    else:
        entry_type = "misc"

    lines = [f"@{entry_type}{{{key},"]

    if paper.get("authors"):
        authors_bib = " and ".join(
            a.strip() for a in paper["authors"].split(";") if a.strip()
        )
        lines.append(f"  author = {{{escape_latex(authors_bib)}}},")

    if paper.get("title"):
        lines.append(f"  title = {{{escape_latex(paper['title'])}}},")

    if paper.get("year"):
        lines.append(f"  year = {{{paper['year']}}},")

    if journal:
        lines.append(f"  journal = {{{escape_latex(journal)}}},")

    if publisher:
        lines.append(f"  publisher = {{{escape_latex(publisher)}}},")

    if isbn:
        lines.append(f"  isbn = {{{isbn}}},")

    if paper.get("doi"):
        lines.append(f"  doi = {{{paper['doi']}}},")

    if paper.get("page_count"):
        lines.append(f"  pages = {{{paper['page_count']}}},")

    if paper.get("abstract"):
        lines.append(f"  abstract = {{{escape_latex(paper['abstract'][:2000])}}},")

    lines.append("}")
    return "\n".join(lines)


def build(papers: List[Dict]) -> str:
    """Full .bib file content for a list of paper dicts with cite_keys."""
    if not papers:
        return ""
    return "\n\n".join(format_entry(p) for p in papers) + "\n"
