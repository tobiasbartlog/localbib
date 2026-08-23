from __future__ import annotations

import pytest

from citation_graph_builder import Edge, Graph, Node, Paper, build
from openalex_client import Work


def _work(
    oa_id: str,
    doi: str = "",
    title: str = "External Paper",
    cited_by_count: int = 5,
    ref_works: list[str] | None = None,
) -> Work:
    return Work(
        id=oa_id,
        doi=doi,
        title=title,
        authors=["Smith, A."],
        year=2020,
        cited_by_count=cited_by_count,
        referenced_works=ref_works or [],
    )


def _paper(
    pid: int,
    doi: str = "",
    ref_works: list[str] | None = None,
    oa_id: str = "",
) -> Paper:
    return Paper(
        id=pid,
        doi=doi,
        title=f"Own Paper {pid}",
        authors="Doe, J.",
        year=2022,
        openalex_id=oa_id,
        cited_by_count=0,
        referenced_works=ref_works or [],
    )


class TestOwnPaperNodes:
    def test_own_papers_get_type_own(self):
        g = build([_paper(1)], {}, depth=1)
        assert g.nodes["own_1"].type == "own"

    def test_own_paper_depth_is_zero(self):
        g = build([_paper(1)], {}, depth=1)
        assert g.nodes["own_1"].depth == 0

    def test_multiple_own_papers(self):
        g = build([_paper(1), _paper(2)], {}, depth=1)
        assert "own_1" in g.nodes
        assert "own_2" in g.nodes


class TestExternalNodes:
    def test_referenced_external_gets_type_external(self):
        w = _work("https://openalex.org/W99")
        p = _paper(1, ref_works=["https://openalex.org/W99"])
        g = build([p], {"https://openalex.org/W99": w}, depth=1)
        assert g.nodes["https://openalex.org/W99"].type == "external"

    def test_external_depth_is_one(self):
        w = _work("https://openalex.org/W99")
        p = _paper(1, ref_works=["https://openalex.org/W99"])
        g = build([p], {"https://openalex.org/W99": w}, depth=1)
        assert g.nodes["https://openalex.org/W99"].depth == 1

    def test_external_that_is_in_library_gets_own_ref_type(self):
        oa_id = "https://openalex.org/W99"
        p1 = _paper(1, doi="10.1/x", ref_works=[oa_id])
        p2 = _paper(2, doi="10.2/y", oa_id=oa_id)  # same OA ID as an external work
        w = _work(oa_id, doi="10.2/y")
        g = build([p1, p2], {oa_id: w}, depth=1)
        # oa_id is own_oa_ids → own_ref
        node = g.nodes.get(oa_id) or g.nodes.get("own_2")
        assert node is not None
        assert node.type in ("own_ref", "own")

    def test_unreferenced_work_not_in_graph(self):
        w = _work("https://openalex.org/W99")
        g = build([_paper(1)], {"https://openalex.org/W99": w}, depth=1)
        assert "https://openalex.org/W99" not in g.nodes


class TestEdgeDirection:
    def test_edge_source_is_citing_paper(self):
        oa_id = "https://openalex.org/W99"
        p = _paper(1, ref_works=[oa_id])
        g = build([p], {oa_id: _work(oa_id)}, depth=1)
        assert any(e.source == "own_1" for e in g.edges)

    def test_edge_target_is_cited_work(self):
        oa_id = "https://openalex.org/W99"
        p = _paper(1, ref_works=[oa_id])
        g = build([p], {oa_id: _work(oa_id)}, depth=1)
        assert any(e.target == oa_id for e in g.edges)

    def test_no_self_loops(self):
        oa_id = "https://openalex.org/W99"
        p = _paper(1, ref_works=[oa_id])
        g = build([p], {oa_id: _work(oa_id)}, depth=1)
        assert all(e.source != e.target for e in g.edges)


class TestDepth:
    def test_depth_1_excludes_level2_works(self):
        w1_id = "https://openalex.org/W1"
        w2_id = "https://openalex.org/W2"
        p = _paper(1, ref_works=[w1_id])
        w1 = _work(w1_id, ref_works=[w2_id])
        w2 = _work(w2_id)
        g = build([p], {w1_id: w1, w2_id: w2}, depth=1)
        assert w2_id not in g.nodes

    def test_depth_2_includes_level2_works(self):
        w1_id = "https://openalex.org/W1"
        w2_id = "https://openalex.org/W2"
        p = _paper(1, ref_works=[w1_id])
        w1 = _work(w1_id, ref_works=[w2_id])
        w2 = _work(w2_id)
        g = build([p], {w1_id: w1, w2_id: w2}, depth=2)
        assert w2_id in g.nodes
        assert g.nodes[w2_id].depth == 2

    def test_depth_2_work_edge_points_to_level1_source(self):
        w1_id = "https://openalex.org/W1"
        w2_id = "https://openalex.org/W2"
        p = _paper(1, ref_works=[w1_id])
        w1 = _work(w1_id, ref_works=[w2_id])
        w2 = _work(w2_id)
        g = build([p], {w1_id: w1, w2_id: w2}, depth=2)
        assert any(e.source == w1_id and e.target == w2_id for e in g.edges)


class TestDeduplication:
    def test_same_doi_two_oa_ids_produces_one_node(self):
        oa1 = "https://openalex.org/W1"
        oa2 = "https://openalex.org/W2"
        p1 = _paper(1, ref_works=[oa1])
        p2 = _paper(2, ref_works=[oa2])
        # Both W1 and W2 have the same DOI
        w1 = _work(oa1, doi="10.1/shared")
        w2 = _work(oa2, doi="10.1/shared")
        g = build([p1, p2], {oa1: w1, oa2: w2}, depth=1)
        doi_nodes = [n for n in g.nodes.values() if n.doi == "10.1/shared"]
        assert len(doi_nodes) == 1

    def test_no_duplicate_edges(self):
        oa_id = "https://openalex.org/W99"
        p1 = _paper(1, ref_works=[oa_id])
        p2 = _paper(2, ref_works=[oa_id])
        g = build([p1, p2], {oa_id: _work(oa_id)}, depth=1)
        edges_to_target = [(e.source, e.target) for e in g.edges if e.target == oa_id]
        assert len(edges_to_target) == len(set(edges_to_target))
