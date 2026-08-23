"""Citation network orchestrator.

Pure-ish orchestration extracted from the former 700-line
``build_citation_network`` endpoint in webapp.py.

Responsibilities:
- Read own-library papers (with DOIs) from the SQLite connection.
- Fetch OpenAlex works by DOI, then by title for misses.
- BFS-fetch referenced works up to the requested depth.
- Hand off to ``citation_graph_builder.build()`` for graph assembly.
- Merge PDF-extracted references (paper_references table) as edges.
- Convert the result into the wire-format dict the frontend consumes.

The orchestrator does NOT write to the DB. It returns the set of
discovered side-effect updates (DOI back-writes, OpenAlex metadata
refreshes) so the calling endpoint can persist them.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime

import citation_graph_builder as cgb
from citation_graph_builder import Graph
from openalex_client import OpenAlexClient, Work


# ─────────────────────────────────────────────────────────────────────────────
# Result + side-effect record types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _PaperUpdate:
    paper_id: int
    new_doi: str | None = None  # set when title-search resolved a DOI
    openalex_id: str | None = None
    cited_by_count: int | None = None


@dataclass
class NetworkResult:
    """Wire-format response + DB updates to persist."""
    payload: dict
    paper_updates: list[_PaperUpdate] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _normalize_doi(doi: str) -> str:
    doi = (doi or "").strip()
    if doi.startswith("http"):
        doi = doi.split("doi.org/")[-1]
    return doi.lower().rstrip(".")


def _normalize_title(s: str) -> str:
    return re.sub(r"[^a-z0-9\s]", "", (s or "").lower()).strip()


def _work_to_raw(w: Work) -> dict:
    """Convert an openalex_client.Work to the raw OpenAlex-shaped dict the
    legacy code used downstream."""
    return {
        "id": w.id,
        "doi": f"https://doi.org/{w.doi}" if w.doi else "",
        "title": w.title,
        "authorships": [{"author": {"display_name": a}} for a in w.authors],
        "publication_year": w.year,
        "cited_by_count": w.cited_by_count,
        "referenced_works": w.referenced_works,
    }


# ─────────────────────────────────────────────────────────────────────────────
# DB loading
# ─────────────────────────────────────────────────────────────────────────────


def _load_doi_papers(conn: sqlite3.Connection) -> list[dict]:
    """Load papers that have a DOI. Returns dicts with normalized ``_doi``."""
    rows = conn.execute(
        "SELECT id, title, doi, authors, year FROM papers "
        "WHERE doi != '' AND doi IS NOT NULL"
    ).fetchall()
    out = []
    for r in rows:
        p = dict(r)
        doi = (p.get("doi") or "").strip()
        if not doi:
            continue
        p["_doi"] = _normalize_doi(doi)
        out.append(p)
    return out


def _load_supporting_data(conn: sqlite3.Connection) -> dict:
    """Load all the auxiliary rows the orchestrator needs in one pass."""
    all_papers_rows = conn.execute(
        "SELECT id, title, authors, year, doi, cited_by_count FROM papers"
    ).fetchall()
    all_papers_map = {r["id"]: dict(r) for r in all_papers_rows}

    pdf_refs_matched = [dict(r) for r in conn.execute(
        """SELECT pr.source_paper_id, pr.matched_paper_id, pr.title, pr.authors,
                  pr.year, pr.journal, pr.doi
           FROM paper_references pr
           WHERE pr.matched_paper_id IS NOT NULL"""
    ).fetchall()]

    pdf_refs_unmatched = [dict(r) for r in conn.execute(
        """SELECT pr.source_paper_id, pr.title, pr.authors, pr.year,
                  pr.journal, pr.doi, pr.id as ref_id
           FROM paper_references pr
           WHERE pr.matched_paper_id IS NULL
             AND pr.title IS NOT NULL AND pr.title != ''"""
    ).fetchall()]

    pdf_ref_counts: dict[int, int] = {}
    for r in conn.execute(
        "SELECT source_paper_id, COUNT(*) as cnt "
        "FROM paper_references GROUP BY source_paper_id"
    ).fetchall():
        pdf_ref_counts[r["source_paper_id"]] = r["cnt"]

    paper_categories_map: dict[int, list] = {}
    try:
        cat_rows = conn.execute(
            "SELECT pc.paper_id, pc.category_id, c.name "
            "FROM paper_categories pc "
            "JOIN categories c ON pc.category_id = c.id"
        ).fetchall()
        for row in cat_rows:
            paper_categories_map.setdefault(row["paper_id"], []).append(
                {"id": row["category_id"], "name": row["name"]}
            )
    except sqlite3.OperationalError:
        # Tables may not exist in some test fixtures
        pass

    return {
        "all_papers_map": all_papers_map,
        "pdf_refs_matched": pdf_refs_matched,
        "pdf_refs_unmatched": pdf_refs_unmatched,
        "pdf_ref_counts": pdf_ref_counts,
        "paper_categories_map": paper_categories_map,
    }


# ─────────────────────────────────────────────────────────────────────────────
# OpenAlex fetching phases
# ─────────────────────────────────────────────────────────────────────────────


def _fetch_own_papers_openalex(
    oa_client: OpenAlexClient,
    doi_papers: list[dict],
) -> tuple[dict, list[_PaperUpdate]]:
    """Phase 1: batch-fetch own papers by DOI, then title-search for misses.

    Returns (oa_works_by_doi, paper_updates). paper_updates captures DOI
    back-writes from successful title searches.
    """
    oa_works: dict[str, dict] = {}  # normalized_doi -> raw work dict
    updates: list[_PaperUpdate] = []

    dois_to_fetch = [p["_doi"] for p in doi_papers]
    for work in oa_client.fetch_works_by_doi(dois_to_fetch):
        if work.doi:
            oa_works[work.doi] = _work_to_raw(work)

    # Title-search fallback
    for p in doi_papers:
        if p["_doi"] in oa_works:
            continue
        title = (p.get("title") or "").strip()
        if not title or len(title) < 5:
            continue
        try:
            w = oa_client.fetch_work_by_title(title, authors=p.get("authors") or "")
            if not w:
                logging.info(f"OpenAlex Titel-Suche: '{title[:50]}' - nicht gefunden")
                continue
            query_words = set(_normalize_title(title).split())
            result_words = set(_normalize_title(w.title or "").split())
            denom = max(len(query_words), len(result_words))
            similarity = (len(query_words & result_words) / denom) if denom else 0
            if similarity < 0.6:
                continue
            wd = (w.doi or "").lower()
            if wd:
                oa_works[wd] = _work_to_raw(w)
                p["_doi"] = wd
                updates.append(_PaperUpdate(paper_id=p["id"], new_doi=wd))
                logging.info(f"OpenAlex Titel-Suche: '{title[:50]}' → {wd}")
        except Exception as e:
            logging.warning(f"OpenAlex Titel-Suche fehlgeschlagen für '{title[:40]}': {e}")
        time.sleep(0.2)

    return oa_works, updates


def _prefetch_references(
    oa_client: OpenAlexClient,
    cgb_papers: list[cgb.Paper],
    depth: int,
) -> dict[str, Work]:
    """BFS pre-fetch of referenced works at all depth levels."""
    level1_ref_map: dict[str, list[str]] = {}
    for cp in cgb_papers:
        for ref_id in cp.referenced_works:
            level1_ref_map.setdefault(ref_id, []).append(f"own_{cp.id}")

    shared_ids = {k for k, v in level1_ref_map.items() if len(v) >= 2}
    l1_to_fetch: set[str] = set(shared_ids)
    for ref_id in level1_ref_map:
        if len(l1_to_fetch) >= 100:
            break
        l1_to_fetch.add(ref_id)

    all_works: dict[str, Work] = {}
    if l1_to_fetch:
        all_works.update(oa_client.fetch_works_by_id(list(l1_to_fetch)))

    frontier_works = dict(all_works)
    for _ in range(2, depth + 1):
        next_ids: set[str] = set()
        for w in frontier_works.values():
            for next_ref in (w.referenced_works or [])[:30]:
                if next_ref not in all_works:
                    next_ids.add(next_ref)
            if len(next_ids) >= 50:
                break
        if not next_ids:
            break
        new_works = oa_client.fetch_works_by_id(list(next_ids)[:50])
        all_works.update(new_works)
        frontier_works = new_works

    return all_works


# ─────────────────────────────────────────────────────────────────────────────
# Graph → wire format
# ─────────────────────────────────────────────────────────────────────────────


def _graph_to_wire_nodes(
    graph: Graph,
    paper_categories_map: dict[int, list],
) -> dict[str, dict]:
    """Convert ``cgb.Graph`` nodes to the response dict shape."""
    nodes: dict[str, dict] = {}
    for nid, n in graph.nodes.items():
        if n.type == "own":
            nodes[nid] = {
                "id": nid,
                "paper_id": n.paper_id,
                "doi": n.doi,
                "title": n.title,
                "authors": n.authors,
                "year": n.year,
                "type": "own",
                "cited_by_count": n.cited_by_count,
                "openalex_id": n.openalex_id,
                "categories": paper_categories_map.get(n.paper_id, []),
            }
        else:
            nodes[nid] = {
                "id": nid,
                "doi": n.doi,
                "title": n.title,
                "authors": n.authors,
                "year": n.year,
                "type": n.type,
                "cited_by_count": n.cited_by_count,
                "openalex_id": n.openalex_id,
                "referenced_by_count": len(n.referenced_by),
                "referenced_by": n.referenced_by,
                "depth": n.depth,
            }
    return nodes


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestration
# ─────────────────────────────────────────────────────────────────────────────


def build_network(
    conn: sqlite3.Connection,
    oa_client: OpenAlexClient,
    depth: int,
) -> NetworkResult:
    """Build the citation network for all DOI-bearing papers in the library.

    Returns a ``NetworkResult`` with the wire-format payload and a list of
    DB updates the endpoint should persist (DOI back-writes + OpenAlex
    metadata refreshes).
    """
    doi_papers = _load_doi_papers(conn)
    my_dois = {p["_doi"] for p in doi_papers}

    # --- Step 1: own-paper OpenAlex lookup (DOI batch + title fallback) ----
    oa_works, paper_updates = _fetch_own_papers_openalex(oa_client, doi_papers)

    # --- Step 2: queue OpenAlex metadata back-writes for own papers --------
    for p in doi_papers:
        oa = oa_works.get(p["_doi"])
        if oa:
            paper_updates.append(_PaperUpdate(
                paper_id=p["id"],
                openalex_id=oa.get("id", ""),
                cited_by_count=oa.get("cited_by_count", 0),
            ))

    # --- Step 3: load supporting data --------------------------------------
    aux = _load_supporting_data(conn)
    all_papers_map: dict[int, dict] = aux["all_papers_map"]
    pdf_refs_matched: list[dict] = aux["pdf_refs_matched"]
    pdf_refs_unmatched: list[dict] = aux["pdf_refs_unmatched"]
    pdf_ref_counts: dict[int, int] = aux["pdf_ref_counts"]
    paper_categories_map: dict[int, list] = aux["paper_categories_map"]

    # --- Step 4: build cgb.Paper structs -----------------------------------
    own_oa_ids: set[str] = set()
    ref_count_total = 0
    cgb_papers: list[cgb.Paper] = []
    for p in doi_papers:
        oa = oa_works.get(p["_doi"])
        oa_id = oa.get("id", "") if oa else ""
        ref_works = (oa.get("referenced_works") or []) if oa else []
        if oa_id:
            own_oa_ids.add(oa_id)
        ref_count_total += len(ref_works)
        cgb_papers.append(cgb.Paper(
            id=p["id"],
            doi=p["_doi"],
            title=p["title"] or "",
            authors=p.get("authors") or "",
            year=p.get("year"),
            openalex_id=oa_id,
            cited_by_count=oa.get("cited_by_count", 0) if oa else 0,
            referenced_works=ref_works,
        ))

    # --- Step 5: pre-fetch referenced works for all depths -----------------
    all_works = _prefetch_references(oa_client, cgb_papers, depth)

    # --- Step 6: build citation graph --------------------------------------
    graph = cgb.build(cgb_papers, all_works, depth)

    # --- Step 7: convert graph to wire-format ------------------------------
    nodes: dict[str, dict] = _graph_to_wire_nodes(graph, paper_categories_map)
    edges: list[dict] = [
        {"source": e.source, "target": e.target} for e in graph.edges
    ]
    edge_set: set[tuple[str, str]] = {(e["source"], e["target"]) for e in edges}

    own_paper_stats: list[dict] = [
        {
            "paper_id": cp.id,
            "title": cp.title,
            "authors": cp.authors,
            "year": cp.year,
            "doi": cp.doi,
            "cited_by_count": cp.cited_by_count,
            "reference_count": len(cp.referenced_works),
            "pdf_reference_count": pdf_ref_counts.get(cp.id, 0),
            "in_openalex": bool(oa_works.get(cp.doi)),
        }
        for cp in cgb_papers
    ]

    all_referenced_papers: list[dict] = []
    for nid, n in graph.nodes.items():
        if n.type in ("external", "own_ref"):
            all_referenced_papers.append({
                "openalex_id": n.openalex_id,
                "doi": n.doi,
                "title": n.title,
                "authors": n.authors,
                "year": n.year,
                "cited_by_count": n.cited_by_count,
                "referenced_by_own": len(n.referenced_by),
                "in_library": n.type == "own_ref",
                "depth": n.depth,
                "source": "openalex",
            })
    all_referenced_papers.sort(key=lambda x: -x.get("cited_by_count", 0))

    # --- Step 8: PDF-extracted references integration ----------------------
    pdf_edge_count = _integrate_pdf_references(
        nodes=nodes,
        edges=edges,
        edge_set=edge_set,
        all_referenced_papers=all_referenced_papers,
        own_paper_stats=own_paper_stats,
        pdf_refs_matched=pdf_refs_matched,
        pdf_refs_unmatched=pdf_refs_unmatched,
        pdf_ref_counts=pdf_ref_counts,
        paper_categories_map=paper_categories_map,
        all_papers_map=all_papers_map,
        own_oa_ids=own_oa_ids,
        my_dois=my_dois,
        oa_client=oa_client,
        depth=depth,
    )

    # --- Step 9: final missing_sources + dedup ------------------------------
    existing_own_ids = {n["paper_id"] for n in nodes.values() if n.get("paper_id")}

    combined_own_refs: dict[str, set] = {}
    for e in edges:
        src_node = nodes.get(e["source"])
        if src_node and src_node.get("type") == "own":
            combined_own_refs.setdefault(e["target"], set()).add(e["source"])

    missing_sources: list[dict] = []
    shared_count = 0
    for tgt_nid, own_sources in combined_own_refs.items():
        n = nodes.get(tgt_nid)
        if not n or n.get("type") in ("own", "own_ref"):
            continue
        n["referenced_by_count"] = len(own_sources)
        n["referenced_by"] = list(own_sources)
        if len(own_sources) >= 2:
            shared_count += 1
        missing_sources.append(n)

    missing_sources.sort(
        key=lambda x: (-x.get("referenced_by_count", 0), -x.get("cited_by_count", 0))
    )
    all_referenced_papers.sort(key=lambda x: -x.get("cited_by_count", 0))

    seen_ref_keys: set[str] = set()
    unique_referenced: list[dict] = []
    for rp in all_referenced_papers:
        rk = rp.get("doi") or rp.get("title", "").lower()
        if rk and rk not in seen_ref_keys:
            seen_ref_keys.add(rk)
            unique_referenced.append(rp)

    default_missing = sum(
        1 for ms in missing_sources if ms.get("referenced_by_count", 0) >= 2
    )
    logging.info(
        f"Netzwerk: {len(nodes)} Knoten, {len(edges)} Kanten "
        f"({pdf_edge_count} PDF), {len(missing_sources)} potentielle fehlende Quellen"
    )

    payload = {
        "nodes": list(nodes.values()),
        "edges": edges,
        "missing_sources": missing_sources[:500],
        "own_paper_stats": own_paper_stats,
        "referenced_papers": unique_referenced[:500],
        "stats": {
            "own_papers": len(existing_own_ids),
            "papers_found_in_openalex": len(oa_works),
            "total_references": ref_count_total + pdf_edge_count,
            "shared_references": shared_count,
            "missing_sources": default_missing,
            "pdf_reference_edges": pdf_edge_count,
            "depth": depth,
        },
    }
    return NetworkResult(payload=payload, paper_updates=paper_updates)


# ─────────────────────────────────────────────────────────────────────────────
# PDF references integration (Step 8 subroutine)
# ─────────────────────────────────────────────────────────────────────────────


def _integrate_pdf_references(
    *,
    nodes: dict[str, dict],
    edges: list[dict],
    edge_set: set[tuple[str, str]],
    all_referenced_papers: list[dict],
    own_paper_stats: list[dict],
    pdf_refs_matched: list[dict],
    pdf_refs_unmatched: list[dict],
    pdf_ref_counts: dict[int, int],
    paper_categories_map: dict[int, list],
    all_papers_map: dict[int, dict],
    own_oa_ids: set[str],
    my_dois: set[str],
    oa_client: OpenAlexClient,
    depth: int,
) -> int:
    """Merge PDF-extracted references into the graph. Returns count of new
    PDF edges added."""
    pdf_edge_count = 0

    existing_own_ids = {n["paper_id"] for n in nodes.values() if n.get("paper_id")}

    # Materialize own-paper nodes for papers without DOI but with PDF refs
    papers_with_refs: set[int] = set()
    for ref in pdf_refs_matched:
        papers_with_refs.add(ref["source_paper_id"])
        papers_with_refs.add(ref["matched_paper_id"])
    for ref in pdf_refs_unmatched:
        papers_with_refs.add(ref["source_paper_id"])

    for pid in papers_with_refs:
        if pid in existing_own_ids or pid not in all_papers_map:
            continue
        p = all_papers_map[pid]
        nid = f"own_{pid}"
        nodes[nid] = {
            "id": nid,
            "paper_id": pid,
            "doi": (p.get("doi") or "").strip(),
            "title": p.get("title") or "",
            "authors": p.get("authors") or "",
            "year": p.get("year"),
            "type": "own",
            "cited_by_count": p.get("cited_by_count") or 0,
            "openalex_id": "",
            "categories": paper_categories_map.get(pid, []),
        }
        existing_own_ids.add(pid)
        if not any(s["paper_id"] == pid for s in own_paper_stats):
            own_paper_stats.append({
                "paper_id": pid,
                "title": p.get("title") or "",
                "authors": p.get("authors") or "",
                "year": p.get("year"),
                "doi": (p.get("doi") or "").strip(),
                "cited_by_count": p.get("cited_by_count") or 0,
                "reference_count": 0,
                "pdf_reference_count": pdf_ref_counts.get(pid, 0),
                "in_openalex": False,
            })

    # Add any remaining papers with PDF refs to own_paper_stats
    for pid, cnt in pdf_ref_counts.items():
        if pid not in all_papers_map:
            continue
        if any(s["paper_id"] == pid for s in own_paper_stats):
            continue
        p = all_papers_map[pid]
        own_paper_stats.append({
            "paper_id": pid,
            "title": p.get("title") or "",
            "authors": p.get("authors") or "",
            "year": p.get("year"),
            "doi": (p.get("doi") or "").strip(),
            "cited_by_count": p.get("cited_by_count") or 0,
            "reference_count": 0,
            "pdf_reference_count": cnt,
            "in_openalex": False,
        })

    # Matched PDF refs -> own→own edges
    for ref in pdf_refs_matched:
        src_nid = f"own_{ref['source_paper_id']}"
        tgt_nid = f"own_{ref['matched_paper_id']}"
        if src_nid in nodes and tgt_nid in nodes:
            if (src_nid, tgt_nid) not in edge_set:
                edge_set.add((src_nid, tgt_nid))
                edges.append({"source": src_nid, "target": tgt_nid, "edge_type": "pdf_ref"})
                pdf_edge_count += 1

    # Group unmatched references by title
    unmatched_grouped: dict[str, dict] = {}
    for ref in pdf_refs_unmatched:
        key = (ref["title"] or "").strip().lower()
        if not key:
            continue
        if key not in unmatched_grouped:
            unmatched_grouped[key] = {
                "title": (ref["title"] or "").strip(),
                "authors": (ref["authors"] or "").strip(),
                "year": ref["year"],
                "doi": (ref["doi"] or "").strip(),
                "sources": [],
            }
        unmatched_grouped[key]["sources"].append(ref["source_paper_id"])

    # OpenAlex lookup for unmatched DOIs
    doi_unmatched = {k: v for k, v in unmatched_grouped.items() if v["doi"]}
    pdf_oa_fetched: dict[str, dict] = {}
    if doi_unmatched:
        doi_list = [v["doi"] for v in doi_unmatched.values()]
        for work in oa_client.fetch_works_by_doi(doi_list):
            if work.id:
                pdf_oa_fetched[work.id] = _work_to_raw(work)

    doi_to_oa: dict[str, tuple[str, dict]] = {}
    for oa_id, w in pdf_oa_fetched.items():
        wd = (w.get("doi") or "").replace("https://doi.org/", "").lower()
        if wd:
            doi_to_oa[wd] = (oa_id, w)

    pdf_ext_ref_count: dict[str, list[str]] = {}

    for key, info in unmatched_grouped.items():
        ref_by_nids = [f"own_{sid}" for sid in info["sources"] if f"own_{sid}" in nodes]
        is_shared = len(set(info["sources"])) >= 2
        ref_doi = info["doi"]

        oa_data = doi_to_oa.get(ref_doi) if ref_doi else None

        if oa_data:
            oa_id, w = oa_data
            if oa_id in nodes:
                for src_nid in ref_by_nids:
                    if (src_nid, oa_id) not in edge_set:
                        edge_set.add((src_nid, oa_id))
                        edges.append({"source": src_nid, "target": oa_id, "edge_type": "pdf_ref"})
                        pdf_edge_count += 1
                continue

            auth_names = [
                a.get("author", {}).get("display_name", "")
                for a in (w.get("authorships") or [])[:5]
            ]
            authors_str = ", ".join(n for n in auth_names if n)
            cited = w.get("cited_by_count", 0)
            in_lib = oa_id in own_oa_ids or ref_doi in my_dois
            node_type = "own_ref" if in_lib else ("missing" if is_shared else "external")

            nodes[oa_id] = {
                "id": oa_id,
                "doi": ref_doi,
                "title": w.get("title") or info["title"],
                "authors": authors_str or info["authors"],
                "year": w.get("publication_year") or info["year"],
                "type": node_type,
                "cited_by_count": cited,
                "referenced_by_count": len(ref_by_nids),
                "referenced_by": ref_by_nids,
                "depth": 1,
                "source": "pdf_extracted_oa",
            }
            for src_nid in ref_by_nids:
                if (src_nid, oa_id) not in edge_set:
                    edge_set.add((src_nid, oa_id))
                    edges.append({"source": src_nid, "target": oa_id, "edge_type": "pdf_ref"})
                    pdf_edge_count += 1

            all_referenced_papers.append({
                "openalex_id": oa_id,
                "doi": ref_doi,
                "title": w.get("title") or info["title"],
                "authors": authors_str or info["authors"],
                "year": w.get("publication_year") or info["year"],
                "cited_by_count": cited,
                "referenced_by_own": len(ref_by_nids),
                "in_library": in_lib,
                "depth": 1,
                "source": "pdf",
            })

            # Queue referenced_works for deeper traversal
            for next_ref in (w.get("referenced_works") or [])[:30]:
                if next_ref not in nodes:
                    pdf_ext_ref_count.setdefault(next_ref, []).append(oa_id)
                    if (oa_id, next_ref) not in edge_set:
                        edge_set.add((oa_id, next_ref))
                        edges.append({"source": oa_id, "target": next_ref})
        else:
            ext_nid = f"ext_ref_{hash(key) & 0xFFFFFFFF}"
            if ext_nid in nodes:
                continue
            nodes[ext_nid] = {
                "id": ext_nid,
                "doi": info["doi"],
                "title": info["title"],
                "authors": info["authors"],
                "year": info["year"],
                "type": "missing" if is_shared else "external",
                "cited_by_count": 0,
                "referenced_by_count": len(ref_by_nids),
                "referenced_by": ref_by_nids,
                "depth": 1,
                "source": "pdf_extracted",
            }
            for src_nid in ref_by_nids:
                if (src_nid, ext_nid) not in edge_set:
                    edge_set.add((src_nid, ext_nid))
                    edges.append({"source": src_nid, "target": ext_nid, "edge_type": "pdf_ref"})
                    pdf_edge_count += 1
            all_referenced_papers.append({
                "openalex_id": "",
                "doi": info["doi"],
                "title": info["title"],
                "authors": info["authors"],
                "year": info["year"],
                "cited_by_count": 0,
                "referenced_by_own": len(ref_by_nids),
                "in_library": False,
                "depth": 1,
                "source": "pdf",
            })

    # Deeper-level fetch for PDF-ref OpenAlex nodes
    if pdf_ext_ref_count:
        combined_next = {
            ref_id: list(sources)
            for ref_id, sources in pdf_ext_ref_count.items()
            if ref_id not in nodes
        }
        max_pdf_depth = max(depth, 2)
        for current_depth in range(2, max_pdf_depth + 1):
            if not combined_next:
                break
            to_fetch = list(combined_next.keys())[:50]
            fetched = oa_client.fetch_works_by_id(to_fetch)
            fetched_raw = {oa_id: _work_to_raw(w) for oa_id, w in fetched.items()}
            combined_next = _process_pdf_next_level(
                fetched_raw, combined_next, current_depth, depth,
                nodes, edges, edge_set, all_referenced_papers,
                own_oa_ids, my_dois,
            )

    return pdf_edge_count


def _process_pdf_next_level(
    fetched: dict[str, dict],
    current_ref_count: dict[str, list[str]],
    current_depth: int,
    depth_limit: int,
    nodes: dict[str, dict],
    edges: list[dict],
    edge_set: set[tuple[str, str]],
    all_referenced_papers: list[dict],
    own_oa_ids: set[str],
    my_dois: set[str],
) -> dict[str, list[str]]:
    """Add a deeper-level batch of OA works as nodes and return the next
    frontier."""
    next_level_refs: dict[str, list[str]] = {}
    for oa_id, w in fetched.items():
        ref_doi = (w.get("doi") or "").replace("https://doi.org/", "").lower()
        in_lib = oa_id in own_oa_ids or ref_doi in my_dois

        auth_names = [
            a.get("author", {}).get("display_name", "")
            for a in (w.get("authorships") or [])[:5]
        ]
        authors_str = ", ".join(n for n in auth_names if n)

        ref_by = current_ref_count.get(oa_id, [])
        is_shared = len(ref_by) >= 2
        node_type = "own_ref" if in_lib else ("missing" if is_shared else "external")
        if current_depth > 1:
            node_type = f"depth{current_depth}" if not in_lib else "own_ref"

        cited = w.get("cited_by_count", 0)
        if oa_id not in nodes:
            nodes[oa_id] = {
                "id": oa_id,
                "doi": ref_doi,
                "title": w.get("title") or "Unbekannt",
                "authors": authors_str,
                "year": w.get("publication_year"),
                "type": node_type,
                "cited_by_count": cited,
                "referenced_by_count": len(ref_by),
                "referenced_by": ref_by,
                "depth": current_depth,
            }

        all_referenced_papers.append({
            "openalex_id": oa_id,
            "doi": ref_doi,
            "title": w.get("title") or "Unbekannt",
            "authors": authors_str,
            "year": w.get("publication_year"),
            "cited_by_count": cited,
            "referenced_by_own": len(ref_by),
            "in_library": in_lib,
            "depth": current_depth,
            "source": "openalex",
        })

        if current_depth < depth_limit:
            for next_ref in (w.get("referenced_works") or [])[:30]:
                if next_ref not in nodes:
                    next_level_refs.setdefault(next_ref, []).append(oa_id)
                    if (oa_id, next_ref) not in edge_set:
                        edge_set.add((oa_id, next_ref))
                        edges.append({"source": oa_id, "target": next_ref})

    return next_level_refs


# ─────────────────────────────────────────────────────────────────────────────
# Side-effect persistence (called by the endpoint)
# ─────────────────────────────────────────────────────────────────────────────


def persist_updates(conn: sqlite3.Connection, updates: list[_PaperUpdate]) -> None:
    """Apply the DOI / OpenAlex metadata updates collected by build_network."""
    if not updates:
        return
    now = datetime.now().isoformat()
    for u in updates:
        if u.new_doi is not None:
            conn.execute(
                "UPDATE papers SET doi=? WHERE id=?",
                (u.new_doi, u.paper_id),
            )
        if u.openalex_id is not None or u.cited_by_count is not None:
            conn.execute(
                "UPDATE papers SET openalex_id=?, cited_by_count=?, "
                "openalex_updated_at=? WHERE id=?",
                (
                    u.openalex_id or "",
                    u.cited_by_count or 0,
                    now,
                    u.paper_id,
                ),
            )
    conn.commit()
