from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from openalex_client import Work


@dataclass
class Paper:
    """Own library paper that seeds the citation graph."""
    id: int
    doi: str
    title: str
    authors: str
    year: int | None
    openalex_id: str
    cited_by_count: int
    referenced_works: list[str] = field(default_factory=list)


@dataclass
class Node:
    id: str
    type: Literal["own", "external", "own_ref"]
    doi: str
    title: str
    authors: str
    year: int | None
    cited_by_count: int
    openalex_id: str
    paper_id: int | None
    depth: int
    referenced_by: list[str] = field(default_factory=list)


@dataclass
class Edge:
    source: str
    target: str


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)


def build(
    own_papers: list[Paper],
    works: dict[str, Work],
    depth: int,
) -> Graph:
    """Assemble a citation graph from own papers and pre-fetched OpenAlex works.

    Pure function — no I/O, deterministic given the same inputs.

    Strategy:
    - Depth 0: own library papers → type "own"
    - Depth 1..depth: referenced works from the previous level → type "external"
      or "own_ref" when the work is also in the library
    - DOI-based deduplication: two OA IDs for the same DOI collapse into one node
    - Edges: source = citing node, target = cited node
    """
    graph = Graph()
    own_oa_ids: set[str] = {p.openalex_id for p in own_papers if p.openalex_id}
    own_dois: set[str] = {p.doi for p in own_papers if p.doi}
    doi_to_nid: dict[str, str] = {}  # doi → canonical node id (for dedup)
    edge_set: set[tuple[str, str]] = set()

    # ── depth 0: own papers ──────────────────────────────────────────────────
    for p in own_papers:
        nid = f"own_{p.id}"
        graph.nodes[nid] = Node(
            id=nid,
            type="own",
            doi=p.doi,
            title=p.title,
            authors=p.authors,
            year=p.year,
            cited_by_count=p.cited_by_count,
            openalex_id=p.openalex_id,
            paper_id=p.id,
            depth=0,
        )
        if p.doi:
            doi_to_nid[p.doi] = nid

    # ref_by[oa_id] = list of source node ids that reference this work
    ref_by: dict[str, list[str]] = {}
    for p in own_papers:
        for ref_id in p.referenced_works:
            ref_by.setdefault(ref_id, []).append(f"own_{p.id}")

    # ── depth 1..depth: BFS over referenced works ────────────────────────────
    frontier: set[str] = set(ref_by.keys())
    for current_depth in range(1, depth + 1):
        next_frontier: set[str] = set()
        for oa_id in frontier:
            work = works.get(oa_id)
            if work is None:
                continue

            is_own = oa_id in own_oa_ids or (work.doi and work.doi in own_dois)

            # Resolve node id — deduplicate via DOI
            if work.doi and work.doi in doi_to_nid:
                target_nid = doi_to_nid[work.doi]
            else:
                target_nid = oa_id

            if target_nid not in graph.nodes:
                graph.nodes[target_nid] = Node(
                    id=target_nid,
                    type="own_ref" if is_own else "external",
                    doi=work.doi,
                    title=work.title,
                    authors=", ".join(work.authors[:5]),
                    year=work.year,
                    cited_by_count=work.cited_by_count,
                    openalex_id=oa_id,
                    paper_id=None,
                    depth=current_depth,
                    referenced_by=list(ref_by.get(oa_id, [])),
                )
                if work.doi:
                    doi_to_nid[work.doi] = target_nid
            else:
                for src in ref_by.get(oa_id, []):
                    if src not in graph.nodes[target_nid].referenced_by:
                        graph.nodes[target_nid].referenced_by.append(src)

            for src_nid in ref_by.get(oa_id, []):
                pair = (src_nid, target_nid)
                if pair not in edge_set:
                    edge_set.add(pair)
                    graph.edges.append(Edge(source=src_nid, target=target_nid))

            # Queue next depth level (cap at 30 per node to match existing behaviour)
            if current_depth < depth:
                for next_ref in (work.referenced_works or [])[:30]:
                    if next_ref not in graph.nodes:
                        ref_by.setdefault(next_ref, []).append(target_nid)
                        next_frontier.add(next_ref)

        frontier = next_frontier

    return graph
